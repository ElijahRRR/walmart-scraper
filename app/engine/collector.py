"""沃尔玛采集器 —— 三种采集流程的统一编排。

  1. collect_by_ids(ids)            指定产品ID → 详情
  2. collect_by_keyword(kw)         关键词 → 列表(翻页) → 二次采详情
  3. collect_by_seller(seller_id)   卖家ID → 全店列表(翻页) → 二次采详情

全部共用一个 ProxyPool（单 IP 复用到失效）+ 温和限速 + 遇封换 IP 重试。
列表页只有部分字段，详情页才有 UPC/GTIN/规格/精确运费。
"""
import re
import time
import random
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterable, List, Optional, Tuple
from urllib.parse import quote_plus, unquote

from curl_cffi import requests as cffi

from app.engine.parser import WalmartParser, parse_all_seller_offers, parse_listing
from app.engine.proxy import ProxyPool

logger = logging.getLogger(__name__)

WALMART = "https://www.walmart.com"
MAX_RETRY = 2          # 单次请求因封控换 IP 的最大重试
SEARCH_PAGE_CAP = 25   # 沃尔玛搜索硬上限
PAGE_RETRY = 2         # 翻页时单页取失败/被封的重试次数（软封多为瞬时，重试可恢复）
# 翻页停止容忍：连续 N 页"无新增"才停（学工具箱：避免单个重复/赞助页导致提前截断）。
EMPTY_PAGE_TOLERANCE = 2
# GetAllSellerOffers 持久化 query hash（Walmart 改版会变，到时从浏览器网络重新抓）
ALL_SELLERS_HASH = "234bb53653540400f507595cf2d5471b173cd0105323409ded5fffa05c083bce"

# ── Yahoo site: 发现通道（绕开沃尔玛搜索 25 页硬上限）─────────────────────
YAHOO = "https://search.yahoo.com/search"
YAHOO_PZ = 10  # Yahoo 每页结果数
# 从 Yahoo 结果页提 Walmart 商品 ID：RU= 重定向参数里的 /ip/<slug>/<数字ID>
_YH_RU_RE = re.compile(r"RU=(http[^&\"]+walmart[^&\"]+)")
_YH_IP_ID_RE = re.compile(r"/ip/[^/]*/(\d+)")
_YH_CAPTCHA_RE = re.compile(r"captcha|robot-check|are you a human", re.I)


def extract_walmart_ids_from_yahoo(html: str) -> List[str]:
    """从 Yahoo 搜索结果 HTML 提取 Walmart 商品 ID（去重保序）。

    Yahoo 结果链接是跳转 URL，真实目标在 `RU=` 参数里；解码后从
    `/ip/<slug>/<数字ID>` 取数字 ID。逆向自工具箱 yahoo_extract。
    """
    if not html:
        return []
    ids: List[str] = []
    for ru in _YH_RU_RE.findall(html):
        for mid in _YH_IP_ID_RE.findall(unquote(ru)):
            ids.append(mid)
    return list(dict.fromkeys(ids))  # 去重保序


class _AdaptiveThrottle:
    """详情并发的自适应失败率限流（逆向自工具箱 _yh_detail_worker 思路）。

    滑动窗口统计最近结果：失败率高→降速（拉大请求间抖动）；持续高→建议暂停。
    线程安全。纯逻辑、可单测（不触网）。
    """

    def __init__(self, base_jitter: Tuple[float, float] = (0.3, 1.0),
                 slow_jitter: Tuple[float, float] = (3.0, 6.0),
                 window: int = 20, warn_at: Tuple[int, int] = (10, 7),
                 stop_at: Tuple[int, int] = (20, 18)) -> None:
        import threading
        self._lock = threading.Lock()
        self._recent: List[bool] = []   # True=失败
        self.base_jitter = base_jitter
        self.slow_jitter = slow_jitter
        self.window = window
        self.warn_at = warn_at          # 近 warn_at[0] 次内 ≥warn_at[1] 失败 → 降速
        self.stop_at = stop_at          # 近 stop_at[0] 次内 ≥stop_at[1] 失败 → 建议停
        self.slowed = False

    def _fails(self, n: int) -> int:
        tail = self._recent[-n:]
        return sum(1 for f in tail if f)

    def record(self, failed: bool) -> None:
        with self._lock:
            self._recent.append(failed)
            if len(self._recent) > self.window:
                self._recent = self._recent[-self.window:]
            self.slowed = (len(self._recent) >= self.warn_at[0]
                           and self._fails(self.warn_at[0]) >= self.warn_at[1])

    def should_stop(self) -> bool:
        with self._lock:
            return (len(self._recent) >= self.stop_at[0]
                    and self._fails(self.stop_at[0]) >= self.stop_at[1])

    def jitter(self) -> float:
        lo, hi = self.slow_jitter if self.slowed else self.base_jitter
        return random.uniform(lo, hi)


class WalmartCollector:
    def __init__(self, pool: Optional[ProxyPool] = None,
                 auto_rotate: bool = False,
                 detail_workers: int = 1) -> None:
        self.pool = pool or ProxyPool()
        self.parser = WalmartParser()
        # 自动换 IP 开关：False=封控/异常时不换 IP，停下并报封控（手动决定）
        self.auto_rotate = auto_rotate
        self.last_block: Optional[str] = None  # 最近一次封控原因（供外部查看）
        # 详情并发度：1=保持原串行（用 pool.pace 限速）；>1=并发(共用粘性IP)+自适应抖动限流。
        # 单粘性IP上并发越高越快但越易被封，建议 3-5；默认 1 保持原行为不变。
        self.detail_workers = max(1, int(detail_workers))

    # ── 封控判定：只有真封控才换 IP（省钱） ──────────────────────────
    @staticmethod
    def _is_blocked(status: int, text: str) -> bool:
        if status in (403, 429, 500, 503):
            return True
        return any(m in text for m in
                   ("px-captcha", "Robot or human", "Verify your identity", "/blocked"))

    # ── 底层：取页面 HTML。
    #   auto_rotate=True : 封控/异常→换 IP 重试（旧行为）
    #   auto_rotate=False: 封控/异常→不换 IP，记下封控原因并停（手动决定换不换）
    #   200 但无数据(非封控)→同 IP 再试一次即放弃，从不烧 IP
    def _get(self, url: str, accept: Callable[[str], bool],
             pace: bool = True) -> Optional[str]:
        for _ in range(MAX_RETRY + 1):
            if pace:
                self.pool.pace()       # 串行模式：全局 3-7s 间隔；并发模式由调用方自管抖动
            proxy = self.pool.current()
            try:
                resp = cffi.get(url, impersonate="chrome", timeout=40,
                                proxies={"http": proxy, "https": proxy})
            except Exception as exc:
                self.last_block = f"请求异常 {type(exc).__name__}"
                if self.auto_rotate:
                    self.pool.rotate(self.last_block)
                    continue
                logger.warning("请求异常且已关闭自动换IP：%s（保持当前IP，停止该URL）", self.last_block)
                return None
            if self._is_blocked(resp.status_code, resp.text):
                self.last_block = f"封控 HTTP{resp.status_code}"
                if self.auto_rotate:
                    self.pool.rotate(self.last_block)
                    continue
                logger.warning("命中封控且已关闭自动换IP：%s（保持当前IP，停止该URL）", self.last_block)
                return None
            if resp.status_code == 200 and accept(resp.text):
                return resp.text
            # 200 但无数据且非封控 → 多半是坏 URL/重定向，同 IP 再试一次即放弃
        return None

    # ── 流程1：指定 ID 采详情 ──────────────────────────────────────
    def collect_detail(self, product_id: str, url: Optional[str] = None,
                       with_all_sellers: bool = False, pace: bool = True) -> dict:
        # 优先用列表给的 canonicalUrl（完整 slug 更可靠），否则退回 bare /ip/{id}
        target = url or f"{WALMART}/ip/{product_id}"
        html = self._get(target, lambda h: '__NEXT_DATA__' in h, pace=pace)
        if html is None:
            return {"_status": "give_up", "product_id": product_id}
        r = self.parser.parse_product(html, product_id)
        # 可选：商品有其他卖家且 SSR 没给全 → 调 all-offers 接口补全竞品卖家
        if (with_all_sellers and r.get("_status") == "ok"
                and r.get("other_seller_count") and not r.get("other_sellers_complete")):
            offers = self.collect_all_sellers(r["product_id"])
            if offers:
                r["other_sellers"] = offers
                r["other_sellers_complete"] = True
        return r

    def collect_by_ids(self, ids: Iterable[str]) -> list[dict]:
        return self._collect_details([{"product_id": str(i), "url": None} for i in ids])

    # ── 全部其他卖家（跟卖竞品）：GetAllSellerOffers GraphQL ──────────
    # ⚠️ 实验性 / 暂缓（2026-06）：该端点有**独立且远比商品页严格**的限流，
    #    同一 IP 连刷几次就进 429 waiting-room 排队页（商品页此时仍正常）。
    #    端点/参数已确认可用，但 JSON 字段名未用真实 200 响应核对过，
    #    parse_all_seller_offers 是按 Walmart 惯例写的容错版。
    #    生产启用前需：①更克制的节奏/勤换 IP ②一次干净 200 响应核对字段名。
    #    默认不调用（collect_detail 的 with_all_sellers 默认 False）。
    #    当前"卖家信息"由 SSR 提供已够用：seller_count / buybox 卖家 / 内联 secondaryOffers。
    def collect_all_sellers(self, item_id: str, condition_codes: list[int] | None = None) -> list[dict]:
        """[实验性/暂缓] 拉取某商品的全部卖家报价（buybox 之外的竞品卖家）。
        命中 429 waiting-room 视为限流（返回空，外层可重试/换IP）。"""
        from json import dumps
        from urllib.parse import quote
        variables = {"itemId": str(item_id), "isSubscriptionEligible": True,
                     "conditionCodes": condition_codes or [1],
                     "allOffersSource": "MORE_SELLER_OPTIONS"}
        url = (f"{WALMART}/orchestra/home/graphql/GetAllSellerOffers/"
               f"{ALL_SELLERS_HASH}?variables={quote(dumps(variables))}")
        headers = {
            "accept": "application/json",
            "x-apollo-operation-name": "GetAllSellerOffers",
            "x-o-platform": "rweb", "x-o-bu": "WALMART-US",
            "x-o-mart": "B2C", "x-o-segment": "oaoh", "wm_mp": "true",
            "referer": f"{WALMART}/ip/{item_id}",
        }
        for _ in range(MAX_RETRY + 1):
            self.pool.pace()
            proxy = self.pool.current()
            try:
                resp = cffi.get(url, impersonate="chrome", timeout=40,
                                proxies={"http": proxy, "https": proxy}, headers=headers)
            except Exception as exc:
                self.last_block = f"请求异常 {type(exc).__name__}"
                if self.auto_rotate:
                    self.pool.rotate(self.last_block); continue
                return []
            if resp.status_code == 429 or "WaitingRoom" in resp.text or "waitingroom" in resp.text:
                self.last_block = "限流 waiting-room"
                logger.warning("GetAllSellerOffers 限流(429 排队页)：item=%s", item_id)
                if self.auto_rotate:
                    self.pool.rotate(self.last_block); continue
                return []
            if resp.status_code == 200:
                return parse_all_seller_offers(resp.text)
            return []
        return []

    def _collect_details(self, listing: list[dict]) -> list[dict]:
        if self.detail_workers <= 1:
            return self._collect_details_serial(listing)
        return self._collect_details_concurrent(listing)

    def _collect_details_serial(self, listing: list[dict]) -> list[dict]:
        out = []
        for i, it in enumerate(listing, 1):
            r = self.collect_detail(it["product_id"], it.get("url"))
            out.append(r)
            logger.info("[详情 %d/%d] %s → %s $%s", i, len(listing),
                        it["product_id"], r.get("_status"), r.get("price"))
        return out

    def _collect_details_concurrent(self, listing: list[dict]) -> list[dict]:
        """并发采详情（共用粘性 IP）+ 自适应失败率限流。

        速度来自 detail_workers 个并发请求；不走 pool.pace 全局串行间隔，
        改由每请求前的随机抖动（失败率高时自动拉大）控制节奏，保护粘性 IP。
        失败率持续过高（默认近 20 次≥18 失败）→ 提前停止（避免在死 IP 上空跑）。
        """
        n = len(listing)
        out: list[Optional[dict]] = [None] * n
        throttle = _AdaptiveThrottle()
        stop_flag = {"stop": False}
        done = {"k": 0}
        import threading
        cnt_lock = threading.Lock()

        def work(i: int, it: dict) -> None:
            if stop_flag["stop"]:
                out[i] = {"_status": "give_up", "product_id": it["product_id"]}
                return
            time.sleep(throttle.jitter())
            r = self.collect_detail(it["product_id"], it.get("url"), pace=False)
            ok = r.get("_status") == "ok"
            throttle.record(failed=not ok)
            out[i] = r
            with cnt_lock:
                done["k"] += 1
                k = done["k"]
            logger.info("[详情 %d/%d] %s → %s $%s%s", k, n,
                        it["product_id"], r.get("_status"), r.get("price"),
                        "  🐢降速" if throttle.slowed else "")
            if throttle.should_stop() and not stop_flag["stop"]:
                stop_flag["stop"] = True
                self.last_block = "失败率过高自动停止（建议换IP后重试失败项）"
                logger.warning("详情并发：%s", self.last_block)

        with ThreadPoolExecutor(max_workers=self.detail_workers) as ex:
            futs = [ex.submit(work, i, it) for i, it in enumerate(listing)]
            for f in as_completed(futs):
                f.result()
        # 未被填充的（理论上不会有）兜底
        return [r if r is not None else {"_status": "give_up"} for r in out]

    # ── 列表翻页通用：搜索页 / 卖家shopall页 ─────────────────────────
    def _paged_listing(self, url_fmt: str, max_pages: int,
                       first_html: Optional[str] = None) -> list[dict]:
        """按 ?page=N 翻页收集列表项，按 product_id 去重，列表为空即停。

        Args:
            url_fmt:    含 {page} 占位符的 URL 模板
            max_pages:  最多翻页数
            first_html: P2-14 优化——若调用方已拿到第1页 HTML，直接复用，
                        避免重复发请求（翻页从 page=2 起）。
        """
        seen, items = set(), []
        empty_streak = 0   # 连续"无新增"页计数（容忍重复/赞助页，避免提前截断）
        hard_max = max_pages
        start_page = 1

        def _ingest(res: dict, page_label: str) -> int:
            nonlocal empty_streak, hard_max
            fresh = [it for it in res["items"]
                     if it["product_id"] and it["product_id"] not in seen]
            for it in fresh:
                seen.add(it["product_id"])
            items.extend(fresh)
            # 用页面自报的 maxPage 收紧上限（避免翻到真实末页之后空请求）
            mp = res.get("max_page")
            if isinstance(mp, int) and mp > 0:
                hard_max = min(hard_max, mp)
            empty_streak = 0 if fresh else empty_streak + 1
            logger.info("[列表 %s] 本页 %d 件，新增 %d，累计 %d（count=%s maxPage=%s 空页连击=%d）",
                        page_label, len(res["items"]), len(fresh), len(items),
                        res.get("count"), res.get("max_page"), empty_streak)
            return len(fresh)

        if first_html is not None:
            _ingest(parse_listing(first_html), "p1（复用）")
            start_page = 2
            if empty_streak >= EMPTY_PAGE_TOLERANCE:
                return items, False

        truncated = False  # True = 翻页中途被封/请求失败而提前停（数据不完整）
        for page in range(start_page, hard_max + 1):
            # 软封多为瞬时（详情阶段能恢复说明列表页重试也能）→ 同页重试几次再放弃
            html = None
            for attempt in range(PAGE_RETRY + 1):
                html = self._get(url_fmt.format(page=page),
                                 lambda h: '__NEXT_DATA__' in h)
                if html is not None:
                    break
                if attempt < PAGE_RETRY:
                    logger.warning("[列表 p%d] 取页失败/被封，重试 %d/%d",
                                   page, attempt + 1, PAGE_RETRY)
            if html is None:
                # 重试仍失败 → 翻页被截断，数据不完整
                truncated = True
                logger.warning("[列表 p%d] 重试 %d 次仍失败，翻页中断，累计 %d 件（不完整）",
                               page, PAGE_RETRY, len(items))
                break
            _ingest(parse_listing(html), f"p{page}")
            # 连续 N 页无新增才停（学工具箱）：单个重复/赞助页不再误判到底
            if empty_streak >= EMPTY_PAGE_TOLERANCE:
                logger.info("[列表] 连续 %d 页无新增，判定到底（停翻）", empty_streak)
                break
        return items, truncated

    # ── 流程2：关键词采集 ─────────────────────────────────────────
    def collect_by_keyword(self, keyword: str, max_pages: int = 25,
                           with_detail: bool = True, sort: str = "best_seller",
                           min_price: float | None = None,
                           max_price: float | None = None) -> dict:
        """关键词采集。min_price/max_price 可选价格区间（沃尔玛 &min_price=&max_price=）；
        默认不限价（全量，受沃尔玛 25 页硬上限约束）。"""
        kw = quote_plus(keyword)
        url_fmt = f"{WALMART}/search?q={kw}&sort={sort}&affinityOverride=default"
        if min_price is not None:
            url_fmt += f"&min_price={min_price:g}"
        if max_price is not None:
            url_fmt += f"&max_price={max_price:g}"
        url_fmt += "&page={page}"
        listing, truncated = self._paged_listing(url_fmt, min(max_pages, SEARCH_PAGE_CAP))
        details = self._collect_details(listing) if with_detail else []
        return {"keyword": keyword, "price_range": (min_price, max_price),
                "listing": listing, "count_listing": len(listing),
                "details": details, "truncated": truncated}

    # ── 流程3：卖家全店采集 ───────────────────────────────────────
    def collect_by_seller(self, seller_id: str | int, max_pages: int = 30,
                          with_detail: bool = True) -> dict:
        url_fmt = (f"{WALMART}/seller/{seller_id}/cp/shopall"
                   f"?affinityOverride=default&page={{page}}")
        # P2-14：首页只发一次请求，同时提取卖家资料 + 列表项；_paged_listing 复用该 HTML。
        first = self._get(url_fmt.format(page=1), lambda h: '__NEXT_DATA__' in h)
        seller = parse_listing(first)["seller"] if first else None
        # 把已有的首页 HTML 传入 _paged_listing，翻页从第2页起，省一次请求
        listing, truncated = self._paged_listing(url_fmt, max_pages, first_html=first)
        details = self._collect_details(listing) if with_detail else []
        return {"seller_id": seller_id, "seller": seller, "listing": listing,
                "count_listing": len(listing), "details": details, "truncated": truncated}

    # ── 流程4：Yahoo site: 发现通道（绕开沃尔玛搜索 25 页硬上限）──────────
    def _yahoo_get(self, query: str, page: int) -> Optional[str]:
        """取一页 Yahoo 搜索结果 HTML（走代理 + 限速；验证码/封控按 auto_rotate 处理）。"""
        from urllib.parse import urlencode
        b = (page - 1) * YAHOO_PZ + 1
        url = YAHOO + "?" + urlencode({"p": query, "b": b, "pz": YAHOO_PZ, "ei": "UTF-8"})
        for _ in range(MAX_RETRY + 1):
            self.pool.pace()
            proxy = self.pool.current()
            try:
                resp = cffi.get(url, impersonate="chrome", timeout=30,
                                proxies={"http": proxy, "https": proxy})
            except Exception as exc:
                self.last_block = f"Yahoo 请求异常 {type(exc).__name__}"
                if self.auto_rotate:
                    self.pool.rotate(self.last_block); continue
                return None
            if resp.status_code == 200:
                if _YH_CAPTCHA_RE.search(resp.text):
                    self.last_block = "Yahoo 验证码"
                    if self.auto_rotate:
                        self.pool.rotate(self.last_block); continue
                    return None
                return resp.text
            if resp.status_code in (403, 429, 503):
                self.last_block = f"Yahoo 封控 HTTP{resp.status_code}"
                if self.auto_rotate:
                    self.pool.rotate(self.last_block); continue
                return None
        return None

    def collect_ids_via_yahoo(self, keyword: str, max_pages: int = 20,
                              target: Optional[int] = None) -> List[str]:
        """用 Yahoo `site:walmart.com/ip/ <关键词>` 发现 Walmart 商品 ID。

        优点：独立于沃尔玛搜索排名与 25 页硬上限，能挖到沃尔玛搜索翻不到的长尾。
        停止：连续 EMPTY_PAGE_TOLERANCE 页无新增 / 达 target / 取页失败。逆向自工具箱 _yh_worker。
        """
        query = f"site:walmart.com/ip/ {keyword}"
        all_ids: List[str] = []
        seen: set = set()
        empty_streak = 0
        for page in range(1, max_pages + 1):
            html = self._yahoo_get(query, page)
            if html is None:
                logger.warning("[Yahoo p%d] 取页失败/被封，停止翻页（累计 %d）", page, len(all_ids))
                break
            page_ids = extract_walmart_ids_from_yahoo(html)
            fresh = [i for i in page_ids if i not in seen]
            for i in fresh:
                seen.add(i)
            all_ids.extend(fresh)
            empty_streak = 0 if fresh else empty_streak + 1
            logger.info("[Yahoo p%d] 提到 %d 个，新增 %d，累计 %d（空页连击=%d）",
                        page, len(page_ids), len(fresh), len(all_ids), empty_streak)
            if target and len(all_ids) >= target:
                return all_ids[:target]
            if empty_streak >= EMPTY_PAGE_TOLERANCE:
                logger.info("[Yahoo] 连续 %d 页无新增，停止翻页", empty_streak)
                break
        return all_ids

    def collect_by_keyword_yahoo(self, keyword: str, max_pages: int = 20,
                                 target: Optional[int] = None,
                                 with_detail: bool = True) -> dict:
        """Yahoo 通道关键词采集：site: 发现 ID → 二次采详情（并发由 detail_workers 控制）。"""
        ids = self.collect_ids_via_yahoo(keyword, max_pages=max_pages, target=target)
        listing = [{"product_id": i, "url": None} for i in ids]
        details = self._collect_details(listing) if with_detail else []
        return {"keyword": keyword, "source": "yahoo", "ids": ids,
                "count_listing": len(ids), "details": details}


if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    c = WalmartCollector()
    mode = sys.argv[1] if len(sys.argv) > 1 else "help"

    if mode == "ids":
        print(json.dumps(c.collect_by_ids(sys.argv[2:]), ensure_ascii=False, indent=1))
    elif mode == "kw":
        # 用法: kw <关键词> [页数] [min_price] [max_price]
        mn = float(sys.argv[4]) if len(sys.argv) > 4 else None
        mx = float(sys.argv[5]) if len(sys.argv) > 5 else None
        r = c.collect_by_keyword(sys.argv[2], max_pages=int(sys.argv[3]) if len(sys.argv) > 3 else 1,
                                 with_detail=False, min_price=mn, max_price=mx)
        print(f"关键词「{sys.argv[2]}」价格区间{r['price_range']} 共 {r['count_listing']} 件")
        for it in r["listing"][:10]:
            print(f"  {it['product_id']} | {it['title'][:40]} | ${it['price']} | {it['seller_name']}")
    elif mode == "seller":
        r = c.collect_by_seller(sys.argv[2], max_pages=int(sys.argv[3]) if len(sys.argv) > 3 else 2,
                                with_detail=False)
        s = r["seller"] or {}
        print(f"卖家 {s.get('display_name') or s.get('name')} (id={r['seller_id']}) 共 {r['count_listing']} 件")
        for it in r["listing"][:10]:
            print(f"  {it['product_id']} | {it['title'][:40]} | ${it['price']}")
    else:
        print("用法: python -m app.engine.collector [ids <id>...| kw <关键词> [页数] | seller <id> [页数]]")
        print(f"成本统计：提取 {c.pool.stats['extractions']} 个 IP")
