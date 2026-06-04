"""沃尔玛采集器 —— 三种采集流程的统一编排。

  1. collect_by_ids(ids)            指定产品ID → 详情
  2. collect_by_keyword(kw)         关键词 → 列表(翻页) → 二次采详情
  3. collect_by_seller(seller_id)   卖家ID → 全店列表(翻页) → 二次采详情

全部共用一个 ProxyPool（单 IP 复用到失效）+ 温和限速 + 遇封换 IP 重试。
列表页只有部分字段，详情页才有 UPC/GTIN/规格/精确运费。
"""
import logging
from typing import Any, Callable, Iterable, Optional
from urllib.parse import quote_plus

from curl_cffi import requests as cffi

from app.engine.parser import WalmartParser, parse_all_seller_offers, parse_listing
from app.engine.proxy import ProxyPool

logger = logging.getLogger(__name__)

WALMART = "https://www.walmart.com"
MAX_RETRY = 2          # 单次请求因封控换 IP 的最大重试
SEARCH_PAGE_CAP = 25   # 沃尔玛搜索硬上限
# GetAllSellerOffers 持久化 query hash（Walmart 改版会变，到时从浏览器网络重新抓）
ALL_SELLERS_HASH = "234bb53653540400f507595cf2d5471b173cd0105323409ded5fffa05c083bce"


class WalmartCollector:
    def __init__(self, pool: Optional[ProxyPool] = None,
                 auto_rotate: bool = False) -> None:
        self.pool = pool or ProxyPool()
        self.parser = WalmartParser()
        # 自动换 IP 开关：False=封控/异常时不换 IP，停下并报封控（手动决定）
        self.auto_rotate = auto_rotate
        self.last_block: Optional[str] = None  # 最近一次封控原因（供外部查看）

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
    def _get(self, url: str, accept: Callable[[str], bool]) -> Optional[str]:
        for _ in range(MAX_RETRY + 1):
            self.pool.pace()
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
                       with_all_sellers: bool = False) -> dict:
        # 优先用列表给的 canonicalUrl（完整 slug 更可靠），否则退回 bare /ip/{id}
        target = url or f"{WALMART}/ip/{product_id}"
        html = self._get(target, lambda h: '__NEXT_DATA__' in h)
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
        out = []
        for i, it in enumerate(listing, 1):
            r = self.collect_detail(it["product_id"], it.get("url"))
            out.append(r)
            logger.info("[详情 %d/%d] %s → %s $%s", i, len(listing),
                        it["product_id"], r.get("_status"), r.get("price"))
        return out

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
        start_page = 1
        if first_html is not None:
            # 复用已有的第1页，不再请求
            res = parse_listing(first_html)
            fresh = [it for it in res["items"]
                     if it["product_id"] and it["product_id"] not in seen]
            for it in fresh:
                seen.add(it["product_id"])
            items += fresh
            logger.info("[列表 p1（复用）] 本页 %d 件，新增 %d，累计 %d（count=%s maxPage=%s）",
                        len(res["items"]), len(fresh), len(items),
                        res.get("count"), res.get("max_page"))
            if not fresh:
                return items
            start_page = 2  # 从第2页继续翻

        for page in range(start_page, max_pages + 1):
            html = self._get(url_fmt.format(page=page),
                             lambda h: '__NEXT_DATA__' in h)
            if html is None:
                break
            res = parse_listing(html)
            fresh = [it for it in res["items"]
                     if it["product_id"] and it["product_id"] not in seen]
            for it in fresh:
                seen.add(it["product_id"])
            items += fresh
            logger.info("[列表 p%d] 本页 %d 件，新增 %d，累计 %d（count=%s maxPage=%s）",
                        page, len(res["items"]), len(fresh), len(items),
                        res.get("count"), res.get("max_page"))
            if not fresh:  # 整页无新商品 → 到底
                break
        return items

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
        listing = self._paged_listing(url_fmt, min(max_pages, SEARCH_PAGE_CAP))
        details = self._collect_details(listing) if with_detail else []
        return {"keyword": keyword, "price_range": (min_price, max_price),
                "listing": listing, "count_listing": len(listing), "details": details}

    # ── 流程3：卖家全店采集 ───────────────────────────────────────
    def collect_by_seller(self, seller_id: str | int, max_pages: int = 30,
                          with_detail: bool = True) -> dict:
        url_fmt = (f"{WALMART}/seller/{seller_id}/cp/shopall"
                   f"?affinityOverride=default&page={{page}}")
        # P2-14：首页只发一次请求，同时提取卖家资料 + 列表项；_paged_listing 复用该 HTML。
        first = self._get(url_fmt.format(page=1), lambda h: '__NEXT_DATA__' in h)
        seller = parse_listing(first)["seller"] if first else None
        # 把已有的首页 HTML 传入 _paged_listing，翻页从第2页起，省一次请求
        listing = self._paged_listing(url_fmt, max_pages, first_html=first)
        details = self._collect_details(listing) if with_detail else []
        return {"seller_id": seller_id, "seller": seller, "listing": listing,
                "count_listing": len(listing), "details": details}


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
