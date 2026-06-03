"""采集服务层 — 三流程接入 + 结果落库。

将引擎层（WalmartCollector）与任务状态机（tasks.py）、数据库（db.py）
串联在一起，对外暴露：

  save_product(result, task_id)  → bool（是否成功写入）
  save_listing_items(items, task_id)  → int（写入数量）

  run_ids(ids, with_detail=True)          → task_id
  run_keyword(keyword, ...)               → task_id
  run_seller(seller_id, ...)              → task_id

三个 run_* 函数都是同步阻塞执行（M1 阶段），M2 后会改造成 lane 池异步执行。
"""
import json
import logging
from typing import Any, Iterable, Optional

from app.db import get_conn
from app.service.tasks import (
    create_task, update_progress, update_status,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 落库：详情（products 表 upsert）
# ─────────────────────────────────────────────────────────────────────────────

def save_product(result: dict, task_id: Optional[int] = None) -> bool:
    """把 parse_product 的输出写入 products 表（按 product_id upsert）。

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
                parse_status
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
                ?
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
                snapshot_at         = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            """,
            (
                product_id, task_id,
                result.get("brand"), result.get("title"),
                result.get("category"), result.get("url"),
                result.get("price"), result.get("price_string"),
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
                result.get("seller_count"), result.get("other_seller_count"),
                json.dumps(other_sellers, ensure_ascii=False) if other_sellers else None,
                status,
            ),
        )

    logger.debug("save_product: upsert product_id=%s task_id=%s", product_id, task_id)
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
# 三流程接入：create task → run collector → save results
# ─────────────────────────────────────────────────────────────────────────────

def _make_collector():
    """懒建 WalmartCollector（允许在测试中通过 monkeypatch 替换）。"""
    from app.engine.collector import WalmartCollector
    return WalmartCollector()


def run_ids(ids: list[str], with_detail: bool = True,
            collector=None) -> int:
    """流程1：指定 product_id 列表，采详情并落库。

    Args:
        ids:         product_id 列表
        with_detail: 是否采详情（默认 True；False 仅占位，无实际列表可落）
        collector:   可注入的 WalmartCollector 实例（测试用）
    Returns:
        task_id
    """
    task_id = create_task("detail", {"ids": ids})
    update_status(task_id, "running")

    c = collector or _make_collector()
    result_count = 0
    try:
        for i, product_id in enumerate(ids, 1):
            r = c.collect_detail(product_id)
            if save_product(r, task_id):
                result_count += 1
            update_progress(task_id, progress=i, total=len(ids),
                            result_count=result_count)
    except Exception as exc:
        logger.exception("run_ids task_id=%d 异常", task_id)
        update_status(task_id, "failed", error_msg=str(exc))
        return task_id

    update_status(task_id, "done")
    update_progress(task_id, progress=len(ids), total=len(ids),
                    result_count=result_count)
    logger.info("run_ids done task_id=%d ids=%d saved=%d", task_id, len(ids), result_count)
    return task_id


def run_keyword(keyword: str, max_pages: int = 25,
                with_detail: bool = True,
                min_price: Optional[float] = None,
                max_price: Optional[float] = None,
                collector=None) -> int:
    """流程2：关键词采集，列表落 listings，详情落 products。

    Args:
        keyword:     搜索关键词
        max_pages:   最多翻页数（沃尔玛硬上限 25）
        with_detail: 二段式：是否对列表项再采详情
        min_price:   价格下限（可选）
        max_price:   价格上限（可选）
        collector:   可注入的 WalmartCollector（测试用）
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
    return task_id


def run_seller(seller_id: str, max_pages: int = 30,
               with_detail: bool = True,
               collector=None) -> int:
    """流程3：卖家全店采集，列表落 listings，详情落 products。

    Args:
        seller_id:   卖家 ID（数字字符串）
        max_pages:   最多翻页数
        with_detail: 二段式：是否采详情
        collector:   可注入的 WalmartCollector（测试用）
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
    return task_id
