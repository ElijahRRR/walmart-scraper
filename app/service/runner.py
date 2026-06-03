"""采集服务层 — 三流程接入 + 结果落库 + 健壮性（M4）。

将引擎层（WalmartCollector）与任务状态机（tasks.py）、数据库（db.py）
串联在一起，对外暴露：

  save_product(result, task_id)           → bool（是否成功写入）
  save_listing_items(items, task_id)      → int（写入数量）

  run_ids(ids, with_detail=True)          → task_id
  run_keyword(keyword, ...)               → task_id
  run_seller(seller_id, ...)              → task_id

M4 新增能力：
  断点续采：run_ids 跳过 task 已完成项（mark_item_done + get_completed_ids）
  失败重试：瞬时失败最多重试 RETRY_MAX=2 次；封控不重试，直接进 lane BLOCKED
  变动检测：save_product 与 products 表旧记录对比 price/in_stock/seller_count，
            写 product_changes 表，并更新 products.has_change/prev_* 字段
  webhook：任务完成后若配置了 WEBHOOK_URL，POST 摘要（失败只记录，不阻塞）
"""
import json
import logging
from typing import Any, Iterable, Optional

from app.db import get_conn, bump_metric
from app.service.tasks import (
    create_task, update_progress, update_status,
    mark_item_done, get_completed_ids,
)

logger = logging.getLogger(__name__)

# ── 常量 ─────────────────────────────────────────────────────────────────────

# 瞬时失败最多重试次数（不含首次尝试）
RETRY_MAX: int = 2

# 封控状态标识（这些 _status 代表封控，不应重试）
BLOCKED_STATUSES: frozenset[str] = frozenset({
    "blocked", "waiting_room", "captcha",
})


# ─────────────────────────────────────────────────────────────────────────────
# 变动检测辅助
# ─────────────────────────────────────────────────────────────────────────────

def _detect_changes(product_id: str, new_price: Optional[float],
                    new_in_stock: Optional[int],
                    new_seller_count: Optional[int]) -> dict:
    """与 products 表现有记录对比，返回变动详情。

    Returns:
        {
          "has_change": bool,
          "changed_fields": list[str],
          "old_price": float | None,
          "old_in_stock": int | None,
          "old_seller_count": int | None,
        }
    """
    result = {
        "has_change": False,
        "changed_fields": [],
        "old_price": None,
        "old_in_stock": None,
        "old_seller_count": None,
    }

    with get_conn() as conn:
        row = conn.execute(
            "SELECT price, seller_count, parse_status FROM products WHERE product_id=?",
            (product_id,),
        ).fetchone()

    if row is None:
        # 首次入库，不算变动
        return result

    old_price = row["price"]
    old_seller_count = row["seller_count"]
    # 用 parse_status 表示在库状态（ok/partial=在库；否则=缺货/未知）
    # 注意：products 表没有 in_stock 字段，用 parse_status 近似
    # 新记录的 in_stock 用 new_in_stock 传入（True=1/False=0/None=未知）
    old_in_stock = None  # products 表无独立 in_stock 字段，此处保留 None 占位

    result["old_price"] = old_price
    result["old_in_stock"] = old_in_stock
    result["old_seller_count"] = old_seller_count

    changed = []

    # 价格变动：非空且数值差异 > 0.001（避免浮点抖动）
    if (old_price is not None and new_price is not None
            and abs(old_price - new_price) > 0.001):
        changed.append("price")

    # 卖家数量变动
    if (old_seller_count is not None and new_seller_count is not None
            and old_seller_count != new_seller_count):
        changed.append("seller_count")

    # in_stock 变动（如果调用方传了 new_in_stock）
    if old_in_stock is not None and new_in_stock is not None and old_in_stock != new_in_stock:
        changed.append("in_stock")

    if changed:
        result["has_change"] = True
        result["changed_fields"] = changed

    return result


def _write_change_record(product_id: str, task_id: Optional[int],
                         change_info: dict,
                         new_price: Optional[float],
                         new_in_stock: Optional[int],
                         new_seller_count: Optional[int]) -> None:
    """向 product_changes 表写入一条变动记录。"""
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO product_changes(
                    product_id, task_id,
                    old_price, new_price,
                    old_in_stock, new_in_stock,
                    old_seller_count, new_seller_count,
                    changed_fields
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product_id, task_id,
                    change_info["old_price"], new_price,
                    change_info["old_in_stock"], new_in_stock,
                    change_info["old_seller_count"], new_seller_count,
                    json.dumps(change_info["changed_fields"], ensure_ascii=False),
                ),
            )
    except Exception as exc:
        logger.warning("_write_change_record 写入失败（不影响主流程）: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# 落库：详情（products 表 upsert + 变动检测）
# ─────────────────────────────────────────────────────────────────────────────

def save_product(result: dict, task_id: Optional[int] = None) -> bool:
    """把 parse_product 的输出写入 products 表（按 product_id upsert）。

    M4 新增：
      - upsert 时与旧值对比 price/seller_count，有变动则写 product_changes 表，
        并更新 products 的 prev_* 字段和 has_change 标志。

    _status 非 "ok" 的结果不写主数据（但计入日志）：
      - blocked / empty_page / no_next_data → 采集失败，不污染库
      - partial → 允许写入（字段不全但有效数据）

    Returns:
        True  = 成功写入
        False = 跳过（_status 不合格 或 无 product_id）
    """
    status = result.get("_status", "")
    product_id = result.get("product_id")

    if not product_id:
        logger.debug("save_product: 无 product_id，跳过")
        return False

    # blocked / empty / no_next_data → 不入库
    if status in ("blocked", "empty_page", "no_next_data", "give_up"):
        logger.debug("save_product: _status=%s product_id=%s，跳过", status, product_id)
        return False

    # 从 result 里拿卖家信息（seller 是嵌套 dict）
    seller = result.get("seller") or {}
    ship_info = result.get("ship_info")
    other_sellers = result.get("other_sellers") or []
    product_details = result.get("product_details") or []
    images = result.get("images") or []

    # 把 bool 转 int（SQLite 无原生 bool）
    is_wfs = int(bool(result.get("is_wfs")))
    seller_fulfilled = int(bool(result.get("seller_fulfilled")))

    new_price = result.get("price")
    new_seller_count = result.get("seller_count")
    new_in_stock: Optional[int] = None  # products 表无独立字段，暂定 None

    # ── 变动检测（在写入前对比旧值） ───────────────────────────────────────
    change_info = _detect_changes(product_id, new_price, new_in_stock, new_seller_count)
    has_change = int(change_info["has_change"])

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO products(
                product_id, task_id,
                brand, title, category, url,
                price, price_string, was_price, currency,
                ship_price, ship_info, order_limit,
                fulfillment_channel, is_wfs, seller_fulfilled,
                rating, reviews,
                upc, gtin13,
                image_url, images,
                long_description, long_description_text, product_details,
                seller_name, seller_id, seller_type, catalog_seller_id,
                seller_rating, seller_review_count,
                seller_count, other_seller_count, other_sellers,
                parse_status,
                prev_price, prev_in_stock, prev_seller_count, has_change
            ) VALUES (
                ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?,
                ?, ?, ?, ?
            )
            ON CONFLICT(product_id) DO UPDATE SET
                task_id             = excluded.task_id,
                brand               = excluded.brand,
                title               = excluded.title,
                category            = excluded.category,
                url                 = excluded.url,
                price               = excluded.price,
                price_string        = excluded.price_string,
                was_price           = excluded.was_price,
                currency            = excluded.currency,
                ship_price          = excluded.ship_price,
                ship_info           = excluded.ship_info,
                order_limit         = excluded.order_limit,
                fulfillment_channel = excluded.fulfillment_channel,
                is_wfs              = excluded.is_wfs,
                seller_fulfilled    = excluded.seller_fulfilled,
                rating              = excluded.rating,
                reviews             = excluded.reviews,
                upc                 = excluded.upc,
                gtin13              = excluded.gtin13,
                image_url           = excluded.image_url,
                images              = excluded.images,
                long_description    = excluded.long_description,
                long_description_text = excluded.long_description_text,
                product_details     = excluded.product_details,
                seller_name         = excluded.seller_name,
                seller_id           = excluded.seller_id,
                seller_type         = excluded.seller_type,
                catalog_seller_id   = excluded.catalog_seller_id,
                seller_rating       = excluded.seller_rating,
                seller_review_count = excluded.seller_review_count,
                seller_count        = excluded.seller_count,
                other_seller_count  = excluded.other_seller_count,
                other_sellers       = excluded.other_sellers,
                parse_status        = excluded.parse_status,
                prev_price          = products.price,
                prev_in_stock       = products.prev_in_stock,
                prev_seller_count   = products.seller_count,
                has_change          = excluded.has_change,
                snapshot_at         = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            """,
            (
                product_id, task_id,
                result.get("brand"), result.get("title"),
                result.get("category"), result.get("url"),
                new_price, result.get("price_string"),
                result.get("was_price"), result.get("currency", "USD"),
                result.get("ship_price"),
                json.dumps(ship_info, ensure_ascii=False) if ship_info is not None else None,
                result.get("order_limit"),
                result.get("fulfillment_channel"), is_wfs, seller_fulfilled,
                result.get("rating"), result.get("reviews"),
                result.get("upc"), result.get("gtin13"),
                result.get("image_url"),
                json.dumps(images, ensure_ascii=False),
                result.get("long_description"),
                result.get("long_description_text"),
                json.dumps(product_details, ensure_ascii=False),
                seller.get("name"), seller.get("id"),
                seller.get("type"), seller.get("catalog_seller_id"),
                seller.get("rating"), seller.get("review_count"),
                new_seller_count, result.get("other_seller_count"),
                json.dumps(other_sellers, ensure_ascii=False) if other_sellers else None,
                status,
                # 首次插入时 prev_* 均为 None（NULL）
                None, None, None, has_change,
            ),
        )

    # ── 有变动时写 product_changes 记录 ─────────────────────────────────────
    if change_info["has_change"]:
        _write_change_record(product_id, task_id, change_info,
                             new_price, new_in_stock, new_seller_count)
        logger.debug(
            "save_product: 检测到变动 product_id=%s 字段=%s",
            product_id, change_info["changed_fields"],
        )

    # 成功写入：累计商品总数
    bump_metric(total_products=1)
    logger.debug("save_product: upsert product_id=%s task_id=%s has_change=%s",
                 product_id, task_id, has_change)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 落库：列表项（listings 表，按 task_id+product_id 去重）
# ─────────────────────────────────────────────────────────────────────────────

def save_listing_items(items: list[dict], task_id: int) -> int:
    """把 parse_listing 的 items 列表写入 listings 表。

    按 (task_id, product_id) 唯一约束 INSERT OR IGNORE 实现去重。
    无 product_id 的条目自动跳过。

    Returns:
        实际写入（含跳过已存在）的条目数（INSERT OR IGNORE 计真正插入数）
    """
    written = 0
    with get_conn() as conn:
        for it in items:
            pid = it.get("product_id")
            if not pid:
                continue
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO listings(
                    task_id, product_id,
                    title, brand, price, rating, reviews,
                    seller_name, seller_id, fulfillment_type,
                    url, image_url
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id, pid,
                    it.get("title"), it.get("brand"),
                    it.get("price"), it.get("rating"), it.get("reviews"),
                    it.get("seller_name"), it.get("seller_id"),
                    it.get("fulfillment_type"),
                    it.get("url"), it.get("image_url"),
                ),
            )
            written += cur.rowcount  # rowcount=1 表示真正插入；0=已存在忽略

    logger.debug("save_listing_items: task_id=%d 写入 %d / %d 条", task_id, written, len(items))
    return written


# ─────────────────────────────────────────────────────────────────────────────
# webhook 回调（M4）
# ─────────────────────────────────────────────────────────────────────────────

def _fire_webhook(task_id: int, task_dict: dict, webhook_url: str) -> None:
    """POST 任务结果摘要到 webhook_url。失败只记录日志，不抛异常。

    使用 urllib（标准库）发送，避免引入额外依赖。
    """
    import urllib.request
    import urllib.error

    payload = {
        "task_id": task_id,
        "status": task_dict.get("status"),
        "type": task_dict.get("type"),
        "progress": task_dict.get("progress"),
        "total": task_dict.get("total"),
        "result_count": task_dict.get("result_count"),
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info(
                "webhook 回调成功 task_id=%d url=%s status=%d",
                task_id, webhook_url, resp.status,
            )
    except urllib.error.URLError as exc:
        logger.warning("webhook 回调失败（不阻塞任务）task_id=%d url=%s err=%s",
                       task_id, webhook_url, exc)
    except Exception as exc:
        logger.warning("webhook 回调异常 task_id=%d err=%s", task_id, exc)


def _maybe_fire_webhook(task_id: int, webhook_url: Optional[str] = None) -> None:
    """任务完成后触发 webhook（如果配置了 URL）。

    webhook_url 优先级：参数传入 > config.WEBHOOK_URL（.env 配置）。
    任何异常都只记录日志，不向上传播（webhook 失败不阻塞任务）。
    """
    # 如果参数未传，从 config 读取全局配置
    if webhook_url is None:
        from app import config as _config
        webhook_url = getattr(_config, "WEBHOOK_URL", "") or ""

    if not webhook_url:
        return  # 未配置，静默跳过

    from app.service.tasks import get_task
    task = get_task(task_id) or {}
    try:
        _fire_webhook(task_id, task, webhook_url)
    except Exception as exc:
        logger.warning("webhook 触发异常（不阻塞任务）task_id=%d err=%s", task_id, exc)


# ─────────────────────────────────────────────────────────────────────────────
# 失败重试辅助（M4）
# ─────────────────────────────────────────────────────────────────────────────

def _collect_with_retry(collector, product_id: str) -> dict:
    """带重试地采集单个商品详情。

    规则：
      - 封控（_status 在 BLOCKED_STATUSES 内）→ 不重试，直接返回
      - 瞬时失败（异常 or _status 非封控的失败）→ 最多重试 RETRY_MAX 次
      - 超过重试上限 → 返回 {"_status": "give_up", "product_id": product_id}

    Returns:
        采集结果 dict（含 _status 字段）
    """
    last_exc: Optional[Exception] = None
    for attempt in range(RETRY_MAX + 1):
        try:
            r = collector.collect_detail(product_id)
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "collect_detail 异常 product_id=%s attempt=%d/%d err=%s",
                product_id, attempt + 1, RETRY_MAX + 1, exc,
            )
            # 异常计为一次请求失败（非封控）
            bump_metric(total_requests=1)
            if attempt < RETRY_MAX:
                continue
            # 超过重试上限
            return {"_status": "give_up", "product_id": product_id,
                    "_error": str(exc)}

        st = r.get("_status", "")

        # 封控：直接返回，不重试；累计封控指标
        if st in BLOCKED_STATUSES:
            logger.info(
                "collect_detail 封控 product_id=%s status=%s，不重试",
                product_id, st,
            )
            # waiting_room 对应 429；其余为通用封控
            if st == "waiting_room":
                bump_metric(total_requests=1, total_429=1, total_blocked=1)
            else:
                bump_metric(total_requests=1, total_blocked=1)
            return r

        # 成功或 partial：记录成功指标
        if st in ("ok", "partial"):
            bump_metric(total_requests=1, total_success=1)
            return r

        # 其他失败状态（empty_page/no_next_data/give_up）：视为瞬时失败，重试
        bump_metric(total_requests=1)
        logger.debug(
            "collect_detail 非封控失败 product_id=%s status=%s attempt=%d/%d，重试",
            product_id, st, attempt + 1, RETRY_MAX + 1,
        )
        if attempt < RETRY_MAX:
            continue
        # 超过重试上限
        return r

    # 理论上不到这里，兜底
    if last_exc:
        return {"_status": "give_up", "product_id": product_id, "_error": str(last_exc)}
    return {"_status": "give_up", "product_id": product_id}


# ─────────────────────────────────────────────────────────────────────────────
# 三流程接入：create task → run collector → save results
# ─────────────────────────────────────────────────────────────────────────────

def _make_collector():
    """懒建 WalmartCollector（允许在测试中通过 monkeypatch 替换）。"""
    from app.engine.collector import WalmartCollector
    return WalmartCollector()


def run_ids(ids: list[str], with_detail: bool = True,
            collector=None, webhook_url: Optional[str] = None,
            resume_task_id: Optional[int] = None) -> int:
    """流程1：指定 product_id 列表，采详情并落库。

    M4 新增：
      - 断点续采：若 resume_task_id 不为 None，从该任务的 completed_ids
        中跳过已完成项（只采剩余的 IDs）；若为 None，则创建新任务。
      - 失败重试：每个 ID 最多重试 RETRY_MAX 次（封控不重试）。
      - webhook：完成时 POST 回调（若 webhook_url 或 config.WEBHOOK_URL 已配置）。

    Args:
        ids:             product_id 列表
        with_detail:     是否采详情（默认 True）
        collector:       可注入的 WalmartCollector 实例（测试用）
        webhook_url:     完成回调 URL（None=从 config 读取）
        resume_task_id:  续采的任务 ID（None=新建任务）
    Returns:
        task_id
    """
    if resume_task_id is not None:
        # 续采模式：复用已有任务 ID
        task_id = resume_task_id
        completed = get_completed_ids(task_id)
        remaining = [pid for pid in ids if pid not in completed]
        logger.info(
            "run_ids 续采 task_id=%d 共%d项，已完成%d项，剩余%d项",
            task_id, len(ids), len(completed), len(remaining),
        )
        update_status(task_id, "running")
    else:
        task_id = create_task("detail", {"ids": ids})
        update_status(task_id, "running")
        completed = set()
        remaining = list(ids)

    c = collector or _make_collector()
    result_count = len(ids) - len(remaining)  # 续采时已完成数计入结果

    try:
        for i, product_id in enumerate(remaining, 1):
            r = _collect_with_retry(c, product_id)

            # 封控：lane blocked，任务标 blocked 后返回
            if r.get("_status") in BLOCKED_STATUSES:
                update_status(task_id, "blocked",
                              error_msg=f"封控 product_id={product_id}")
                return task_id

            if save_product(r, task_id):
                result_count += 1

            # 标记该 item 已完成（无论是否成功入库）
            mark_item_done(task_id, product_id)

            update_progress(
                task_id,
                progress=len(completed) + i,
                total=len(ids),
                result_count=result_count,
            )

    except Exception as exc:
        logger.exception("run_ids task_id=%d 异常", task_id)
        update_status(task_id, "failed", error_msg=str(exc))
        return task_id

    update_status(task_id, "done")
    update_progress(task_id, progress=len(ids), total=len(ids),
                    result_count=result_count)
    logger.info("run_ids done task_id=%d ids=%d saved=%d", task_id, len(ids), result_count)

    # webhook 回调
    _maybe_fire_webhook(task_id, webhook_url)
    return task_id


def run_keyword(keyword: str, max_pages: int = 25,
                with_detail: bool = True,
                min_price: Optional[float] = None,
                max_price: Optional[float] = None,
                collector=None,
                webhook_url: Optional[str] = None) -> int:
    """流程2：关键词采集，列表落 listings，详情落 products。

    Args:
        keyword:     搜索关键词
        max_pages:   最多翻页数（沃尔玛硬上限 25）
        with_detail: 二段式：是否对列表项再采详情
        min_price:   价格下限（可选）
        max_price:   价格上限（可选）
        collector:   可注入的 WalmartCollector（测试用）
        webhook_url: 完成回调 URL（None=从 config 读取）
    Returns:
        task_id
    """
    params: dict[str, Any] = {
        "keyword": keyword,
        "max_pages": max_pages,
        "with_detail": with_detail,
    }
    if min_price is not None:
        params["min_price"] = min_price
    if max_price is not None:
        params["max_price"] = max_price

    task_id = create_task("keyword", params)
    update_status(task_id, "running")

    c = collector or _make_collector()
    result_count = 0
    listing: list = []
    try:
        result = c.collect_by_keyword(
            keyword,
            max_pages=max_pages,
            with_detail=with_detail,
            min_price=min_price,
            max_price=max_price,
        )
        listing = result.get("listing") or []
        details = result.get("details") or []

        # 列表落库
        written = save_listing_items(listing, task_id)
        update_progress(task_id, progress=len(listing),
                        total=len(listing), result_count=written)

        # 详情落库（二段式）
        for det in details:
            if save_product(det, task_id):
                result_count += 1

        update_progress(task_id, progress=len(listing),
                        total=len(listing), result_count=result_count or written)

    except Exception as exc:
        logger.exception("run_keyword task_id=%d 异常", task_id)
        update_status(task_id, "failed", error_msg=str(exc))
        return task_id

    update_status(task_id, "done")
    logger.info("run_keyword done task_id=%d keyword=%r listing=%d saved=%d",
                task_id, keyword, len(listing), result_count or written)

    # webhook 回调
    _maybe_fire_webhook(task_id, webhook_url)
    return task_id


def run_seller(seller_id: str, max_pages: int = 30,
               with_detail: bool = True,
               collector=None,
               webhook_url: Optional[str] = None) -> int:
    """流程3：卖家全店采集，列表落 listings，详情落 products。

    Args:
        seller_id:   卖家 ID（数字字符串）
        max_pages:   最多翻页数
        with_detail: 二段式：是否采详情
        collector:   可注入的 WalmartCollector（测试用）
        webhook_url: 完成回调 URL（None=从 config 读取）
    Returns:
        task_id
    """
    task_id = create_task("seller", {
        "seller_id": seller_id,
        "max_pages": max_pages,
        "with_detail": with_detail,
    })
    update_status(task_id, "running")

    c = collector or _make_collector()
    result_count = 0
    listing: list = []
    try:
        result = c.collect_by_seller(seller_id, max_pages=max_pages,
                                     with_detail=with_detail)
        listing = result.get("listing") or []
        details = result.get("details") or []

        written = save_listing_items(listing, task_id)
        update_progress(task_id, progress=len(listing),
                        total=len(listing), result_count=written)

        for det in details:
            if save_product(det, task_id):
                result_count += 1

        update_progress(task_id, progress=len(listing),
                        total=len(listing), result_count=result_count or written)

    except Exception as exc:
        logger.exception("run_seller task_id=%d 异常", task_id)
        update_status(task_id, "failed", error_msg=str(exc))
        return task_id

    update_status(task_id, "done")
    logger.info("run_seller done task_id=%d seller_id=%s listing=%d saved=%d",
                task_id, seller_id, len(listing), result_count or written)

    # webhook 回调
    _maybe_fire_webhook(task_id, webhook_url)
    return task_id
