"""
沃尔玛商品采集系统 - 页面解析模块

与亚马逊不同：沃尔玛商品页把全部数据以 JSON 注入在
`<script id="__NEXT_DATA__">` 里（Next.js），所以解析 = 取 JSON 路径，
而不是抠 HTML 选择器。本模块只依赖标准库（json / re）。

字段路径与 null 语义均经真实页面验证（2026-06，自营 / WFS / SFF 三类商品）：
  - 自营   Lasko 落地扇     sellerType=INTERNAL
  - WFS    Shark 喷雾扇     fulfillmentType="FC"
  - SFF    草编包(中国卖家)  fulfillmentType="MARKETPLACE", 运费 $5.99

关键坑（已规避）：
  1. 运费/到货时间在 product.shippingOption，**不是** priceInfo.shipPrice（恒 null）。
  2. null 不等于免邮：有 shippingOption 节点但无 shipPrice 才是免邮。
  3. GTIN13 沃尔玛无原生字段，由 12 位 UPC 补前导零派生。
  4. Rating/Reviews 为 null 表示新品无评价，不是解析失败。
"""
import re
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

WALMART_BASE = "https://www.walmart.com"

_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL,
)
# 反爬拦截特征（PerimeterX / 验证码 / 机器人页）
_BLOCK_MARKERS = (
    "px-captcha",
    "Robot or human",
    "/blocked",
    "Verify your identity",
)


class WalmartParser:
    """沃尔玛商品页面解析器（基于 __NEXT_DATA__ JSON）"""

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------
    def parse_product(self, html_text: str, item_id: str = "") -> Dict[str, Any]:
        """解析单个商品详情页。

        Args:
            html_text: 商品页完整 HTML（含 __NEXT_DATA__）。也可直接传已解析的 JSON 字符串。
            item_id:   期望的 usItemId（用于校验落版 / 调试），可空。

        Returns:
            统一结构的 result dict（见文件末 RESULT_SCHEMA 注释）。
            解析失败时返回带 _status / _error 的最小 dict，绝不抛异常。
        """
        result: Dict[str, Any] = {
            "product_id": item_id or None,
            "_status": "ok",
        }

        # 1) 反爬拦截检测
        if not html_text or len(html_text) < 200:
            result["_status"] = "empty_page"
            return result
        if any(m in html_text for m in _BLOCK_MARKERS):
            result["_status"] = "blocked"
            result["_error"] = "疑似 PerimeterX 拦截 / 验证码页"
            return result

        # 2) 取出 __NEXT_DATA__ JSON
        product, idml = self._extract_nodes(html_text)
        if product is None:
            result["_status"] = "no_next_data"
            result["_error"] = "未找到 __NEXT_DATA__ 或 product 节点"
            return result
        idml = idml or {}

        # 3) 逐字段解析（任一字段失败不影响其余字段）
        try:
            self._fill_basic(result, product)
            self._fill_seller(result, product)
            self._fill_price(result, product)
            self._fill_shipping(result, product)
            self._fill_fulfillment(result, product)
            self._fill_rating(result, product)
            self._fill_identifiers(result, product)
            self._fill_images(result, product)
            self._fill_descriptions(result, idml)
        except Exception as exc:  # 防御：任何意外都降级而非崩溃
            logger.exception("parse_product 异常 item_id=%s", item_id)
            result["_status"] = "partial"
            result["_error"] = f"{type(exc).__name__}: {exc}"

        return result

    # ------------------------------------------------------------------
    # __NEXT_DATA__ 提取
    # ------------------------------------------------------------------
    def _extract_nodes(self, html_text: str):
        """返回 (product_node, idml_node)，失败返回 (None, None)。"""
        data = self._load_next_data(html_text)
        if data is None:
            return None, None
        # 标准路径：props.pageProps.initialData.data.{product,idml}
        inner = _dig(data, "props", "pageProps", "initialData", "data", default=None)
        if not isinstance(inner, dict):
            # 兜底：深搜第一个含 usItemId 的对象
            product = _find_first_with_key(data, "usItemId")
            return product, None
        product = inner.get("product")
        if not isinstance(product, dict):
            product = _find_first_with_key(inner, "usItemId")
        return product, inner.get("idml")

    @staticmethod
    def _load_next_data(html_text: str) -> Optional[dict]:
        # 允许直接传 JSON 字符串
        stripped = html_text.lstrip()
        if stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass
        m = _NEXT_DATA_RE.search(html_text)
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            logger.warning("__NEXT_DATA__ JSON 解析失败")
            return None

    # ------------------------------------------------------------------
    # 各字段填充
    # ------------------------------------------------------------------
    def _fill_basic(self, r: Dict[str, Any], p: dict) -> None:
        r["product_id"] = p.get("usItemId") or r.get("product_id")
        r["brand"] = _clean_str(p.get("brand"))
        r["title"] = _clean_str(p.get("name"))
        r["category"] = _clean_str(p.get("type"))  # 例: "Electric Household Fans"
        canonical = p.get("canonicalUrl")
        r["url"] = (WALMART_BASE + canonical) if canonical else (
            f"{WALMART_BASE}/ip/{r['product_id']}" if r.get("product_id") else None
        )

    def _fill_seller(self, r: Dict[str, Any], p: dict) -> None:
        seller_type = p.get("sellerType")  # INTERNAL(自营) / EXTERNAL(第三方)
        # buybox 卖家（抢到购买框、当前展示价的那个）
        r["seller"] = {
            "name": _clean_str(p.get("sellerName")),
            "id": p.get("sellerId"),                    # 32位十六进制
            "type": seller_type,
            "catalog_seller_id": p.get("catalogSellerId"),  # 数字ID（API / 店铺URL 用）
            "rating": p.get("sellerAverageRating"),     # 0 = 新卖家无评分
            "review_count": p.get("sellerReviewCount"),
            "display_name": _clean_str(p.get("sellerDisplayName")),
            "storefront_url": p.get("sellerStoreFrontURL"),
            "is_walmart": seller_type == "INTERNAL",
            "is_buybox": True,
        }
        # 卖家数量（跟卖竞争激烈度）
        total = p.get("transactableOfferCount")
        extra = p.get("additionalOfferCount")
        if total is None and extra is not None:
            total = extra + 1
        if extra is None and isinstance(total, int):
            extra = max(total - 1, 0)
        r["seller_count"] = total                # 可售卖家总数（含 buybox）
        r["other_seller_count"] = extra          # 除 buybox 外的其他卖家数
        # 其他卖家明细：SSR 通常只给 secondaryOffers（多为空），完整列表需 all-offers GraphQL
        r["other_sellers"] = self._parse_secondary_offers(p)
        r["other_sellers_complete"] = (
            extra in (None, 0) or len(r["other_sellers"]) >= (extra or 0)
        )  # False 表示还有卖家未取全，需调 all-offers 接口

    @staticmethod
    def _parse_secondary_offers(p: dict) -> List[Dict[str, Any]]:
        """解析 SSR 内联的其他卖家报价（secondaryOffers / conditionOffers）。"""
        out: List[Dict[str, Any]] = []
        for off in (p.get("secondaryOffers") or []):
            if not isinstance(off, dict):
                continue
            out.append({
                "offer_id": off.get("offerId"),
                "seller_name": _clean_str(off.get("sellerName")),
                "seller_id": off.get("sellerId"),
                "catalog_seller_id": off.get("catalogSellerId"),
                "price": _to_float(_dig(off, "priceInfo", "currentPrice", "price", default=None)
                                   or off.get("price") or off.get("secondaryOfferPrice")),
                "condition": _dig(off, "condition", "text", default=None) or off.get("conditionName"),
                "seller_rating": off.get("sellerAverageRating"),
                "seller_review_count": off.get("sellerReviewCount"),
                "fulfillment_type": off.get("fulfillmentType"),
            })
        return out

    def _fill_price(self, r: Dict[str, Any], p: dict) -> None:
        pi = p.get("priceInfo") or {}
        r["price"] = _dig(pi, "currentPrice", "price", default=None)
        r["price_string"] = _dig(pi, "currentPrice", "priceString", default=None)
        r["was_price"] = _dig(pi, "wasPrice", "price", default=None)  # None = 未打折
        r["currency"] = _dig(pi, "currentPrice", "currencyUnit", default="USD")

    def _fill_shipping(self, r: Dict[str, Any], p: dict) -> None:
        """运费 + 到货窗口。正确来源是 product.shippingOption，
        priceInfo.shipPrice / product.shippingPrice 恒为 null，勿用。"""
        so = p.get("shippingOption") or {}
        ship_price = _dig(so, "shipPrice", "price", default=None)
        # 语义：有 shippingOption 但无 shipPrice → 免邮(0.0)；完全无该节点 → 未知(None)
        if ship_price is None and so:
            ship_price = 0.0
        r["ship_price"] = ship_price
        r["ship_info"] = {
            "delivery_date": so.get("deliveryDate"),        # 到货窗口起 (ISO)
            "max_delivery_date": so.get("maxDeliveryDate"),  # 到货窗口止 (ISO)
            "ship_method": so.get("shipMethod"),             # STANDARD / ...
            "availability_status": so.get("availabilityStatus"),
        } if so else None
        # 限购数量（fulfillmentOptions 里）
        fo = p.get("fulfillmentOptions") or []
        if fo and isinstance(fo, list):
            r["order_limit"] = fo[0].get("orderLimit") or fo[0].get("maxOrderQuantity")

    def _fill_fulfillment(self, r: Dict[str, Any], p: dict) -> None:
        """履约渠道判定（WFS 字段的来源）。
        fulfillmentType: "FC"=WFS仓发 / "MARKETPLACE"=卖家自发(SFF)
        sellerType=INTERNAL → 沃尔玛自营。"""
        ftype = p.get("fulfillmentType")
        seller_type = p.get("sellerType")
        if seller_type == "INTERNAL":
            channel = "walmart_internal"
        elif ftype == "FC":
            channel = "wfs"
        elif ftype == "MARKETPLACE":
            channel = "seller_fulfilled"
        else:
            channel = "unknown"
        r["fulfillment_channel"] = channel
        r["is_wfs"] = channel == "wfs"
        r["seller_fulfilled"] = channel == "seller_fulfilled"
        r["_fulfillment_type_raw"] = ftype  # 原始值留档，便于发现新取值

    def _fill_rating(self, r: Dict[str, Any], p: dict) -> None:
        # null 表示新品无评价，归一为 0 但用 _has_reviews 区分
        rating = p.get("averageRating")
        reviews = p.get("numberOfReviews")
        r["rating"] = rating
        r["reviews"] = reviews
        r["_has_reviews"] = bool(reviews)

    def _fill_identifiers(self, r: Dict[str, Any], p: dict) -> None:
        upc = p.get("upc")
        if not upc:  # 兜底深搜
            upc = _find_first_value(p, "upc")
        r["upc"] = upc
        r["gtin13"] = _to_gtin13(upc)

    def _fill_images(self, r: Dict[str, Any], p: dict) -> None:
        img = p.get("imageInfo") or {}
        all_images = img.get("allImages") or []
        urls = []
        for it in all_images:
            if isinstance(it, dict) and it.get("url"):
                urls.append(it["url"])
            elif isinstance(it, str):
                urls.append(it)
        r["image_url"] = img.get("thumbnailUrl") or (urls[0] if urls else None)
        r["images"] = urls

    def _fill_descriptions(self, r: Dict[str, Any], idml: dict) -> None:
        long_html = idml.get("longDescription") or ""
        r["long_description"] = long_html  # 保留原始 HTML
        r["long_description_text"] = _strip_html(long_html)
        # ProductDetails：[{name, value}, ...]
        specs = idml.get("specifications") or []
        r["product_details"] = [
            {"name": s.get("name"), "value": s.get("value")}
            for s in specs
            if isinstance(s, dict)
        ]


# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------
def _dig(obj: Any, *path: str, default: Any = None) -> Any:
    """安全多级取值：_dig(d, 'a', 'b', default=None)。"""
    cur = obj
    for key in path:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return default
    return cur


def _find_first_with_key(obj: Any, key: str) -> Optional[dict]:
    """深度优先找到第一个含 key 的 dict 节点。"""
    if isinstance(obj, dict):
        if key in obj:
            return obj
        for v in obj.values():
            found = _find_first_with_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_first_with_key(v, key)
            if found is not None:
                return found
    return None


def _find_first_value(obj: Any, key: str) -> Any:
    """深搜返回第一个 key 对应的非空值。"""
    node = _find_first_with_key(obj, key)
    return node.get(key) if node else None


def _to_gtin13(upc: Optional[str]) -> Optional[str]:
    """UPC → GTIN-13。沃尔玛多给 12 位 UPC-A，补前导零即标准 GTIN-13。"""
    if not upc:
        return None
    digits = re.sub(r"\D", "", str(upc))
    if not digits:
        return None
    if len(digits) >= 13:
        return digits[-13:]  # GTIN-14 取后13；已13位原样
    return digits.zfill(13)


def _to_float(val: Any) -> Optional[float]:
    """价格归一：'$23.98' / '1,299.00' / 23.98 → float。"""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    digits = re.sub(r"[^\d.]", "", str(val))
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def _clean_str(val: Any) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s or None


def _strip_html(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ----------------------------------------------------------------------
# RESULT_SCHEMA（解析输出的字段约定，供下游 normalize_result 对齐）
# ----------------------------------------------------------------------
# {
#   "product_id":      str   usItemId
#   "brand":           str
#   "title":           str
#   "category":        str   product.type
#   "url":             str   绝对链接
#   "seller":          {name,id,type,catalog_seller_id,rating,review_count,storefront_url,is_walmart}
#   "price":           float currentPrice.price
#   "price_string":    str   "$27.99"
#   "was_price":       float|None  None=未打折
#   "currency":        str
#   "ship_price":      float|None  0.0=免邮 / None=未知（无 shippingOption）
#   "ship_info":       {delivery_date,max_delivery_date,ship_method,availability_status}|None
#   "order_limit":     int|None
#   "fulfillment_channel": "walmart_internal"|"wfs"|"seller_fulfilled"|"unknown"
#   "is_wfs":          bool
#   "seller_fulfilled":bool
#   "rating":          float|None  None=新品无评价
#   "reviews":         int|None
#   "upc":             str
#   "gtin13":          str   由 upc 派生
#   "image_url":       str   主图
#   "images":          [str] 全部图
#   "long_description":      str  原始HTML
#   "long_description_text": str  纯文本
#   "product_details": [{name,value}]
#   "_status":         "ok"|"partial"|"blocked"|"empty_page"|"no_next_data"
# }


# ----------------------------------------------------------------------
# 列表页解析（搜索页 / 卖家 shopall 页通用）
# ----------------------------------------------------------------------
# 商品列表在两种页面里位置不同，统一从这两处收集：
#   搜索页   : initialData.searchResult.itemStacks[*].items
#   卖家shopall: initialData.contentLayout.modules[*].configs.itemStacks.itemStacks[*].items
# 列表项只含部分字段（无 UPC/GTIN/规格/精确运费），需拿 product_id 再走 parse_product。

def _init_data(html_text: str) -> Optional[dict]:
    """从 HTML 取出 props.pageProps.initialData。"""
    data = WalmartParser._load_next_data(html_text)
    if data is None:
        return None
    return _dig(data, "props", "pageProps", "initialData", default=None)


def _collect_listing_items(init: dict) -> List[dict]:
    items: List[dict] = []
    sr = init.get("searchResult") or {}
    for st in (sr.get("itemStacks") or []):
        items += [x for x in (st.get("items") or []) if isinstance(x, dict) and x.get("usItemId")]
    for mod in ((init.get("contentLayout") or {}).get("modules") or []):
        outer = (mod.get("configs") or {}).get("itemStacks") or {}
        for st in (outer.get("itemStacks") or []):
            items += [x for x in (st.get("items") or []) if isinstance(x, dict) and x.get("usItemId")]
    return items


def _normalize_list_item(it: dict) -> Dict[str, Any]:
    """列表项 → 部分字段记录（与 parse_product 输出字段名对齐）。"""
    pi = it.get("priceInfo") or {}
    price = _to_float(
        _dig(pi, "currentPrice", "price", default=None)
        or pi.get("linePrice")
        or it.get("price")
    )
    img = it.get("imageInfo") or {}
    canonical = it.get("canonicalUrl")
    return {
        "product_id": it.get("usItemId"),
        "title": _clean_str(it.get("name")),
        "brand": _clean_str(it.get("brand")),
        "price": price,
        "rating": it.get("averageRating"),
        "reviews": it.get("numberOfReviews"),
        "seller_name": _clean_str(it.get("sellerName")),
        "seller_id": it.get("sellerId"),
        "fulfillment_type": it.get("fulfillmentType"),
        "url": (WALMART_BASE + canonical) if canonical else None,
        "image_url": img.get("thumbnailUrl") or it.get("image"),
        "_partial": True,  # 标记：仅列表字段，缺 UPC/GTIN/规格
    }


def _normalize_seller(seller: Optional[dict]) -> Optional[Dict[str, Any]]:
    if not seller:
        return None
    return {
        "catalog_seller_id": seller.get("catalogSellerId"),
        "seller_id": seller.get("sellerId"),
        "name": _clean_str(seller.get("sellerName")),
        "display_name": _clean_str(seller.get("sellerDisplayName")),
        "email": seller.get("sellerEmail"),
        "phone": seller.get("sellerPhone"),
        "address": {k: seller.get(k) for k in
                    ("address1", "address2", "city", "state", "postalCode", "countryCode")},
        "reviews": seller.get("sellerReviews"),
        "has_badge": seller.get("hasSellerBadge"),
        "logo_url": seller.get("sellerLogoURL"),
        "type": seller.get("sellerType"),
    }


def parse_all_seller_offers(raw: Any) -> List[Dict[str, Any]]:
    """解析 GetAllSellerOffers GraphQL 响应 → 其他卖家报价列表。

    用通用深搜定位 offers 数组（不写死路径，抗 Walmart 改版）：
    第一个"元素含 sellerName/sellerId 且含价格/offerId"的数组即视为 offers。
    raw 可为 dict 或 JSON 字符串。
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    offers_arr = _find_offers_array(raw)
    return [_normalize_offer(o) for o in offers_arr]


def _find_offers_array(obj: Any) -> List[dict]:
    def looks_like_offer(x: Any) -> bool:
        return isinstance(x, dict) and (x.get("sellerName") or x.get("sellerId")
                                        or x.get("sellerDisplayName")) and (
            "priceInfo" in x or "offerId" in x or "price" in x)
    if isinstance(obj, list):
        if obj and looks_like_offer(obj[0]):
            return [x for x in obj if isinstance(x, dict)]
        for v in obj:
            found = _find_offers_array(v)
            if found:
                return found
    elif isinstance(obj, dict):
        for v in obj.values():
            found = _find_offers_array(v)
            if found:
                return found
    return []


def _normalize_offer(o: dict) -> Dict[str, Any]:
    return {
        "offer_id": o.get("offerId"),
        "seller_name": _clean_str(o.get("sellerName") or o.get("sellerDisplayName")),
        "seller_id": o.get("sellerId"),
        "catalog_seller_id": o.get("catalogSellerId"),
        "seller_type": o.get("sellerType"),
        "price": _to_float(_dig(o, "priceInfo", "currentPrice", "price", default=None)
                           or o.get("price")),
        "was_price": _to_float(_dig(o, "priceInfo", "wasPrice", "price", default=None)),
        "condition": _dig(o, "condition", "text", default=None) or o.get("conditionName"),
        "seller_rating": o.get("sellerAverageRating"),
        "seller_review_count": o.get("sellerReviewCount"),
        "fulfillment_type": o.get("fulfillmentType"),
        "ship_price": _to_float(_dig(o, "shippingOption", "shipPrice", "price", default=None)),
        "is_walmart": o.get("sellerType") == "INTERNAL",
    }


def parse_listing(html_text: str) -> Dict[str, Any]:
    """解析搜索页或卖家 shopall 页，返回部分字段商品列表 + 翻页信息 + 卖家资料。

    Returns:
        {
          "_status": "ok"|"blocked"|"no_next_data"|"empty_page",
          "items": [partial record, ...],   # 含 product_id，去重前
          "count": int|None,                # 结果总数（若有）
          "max_page": int|None,             # 最大页（搜索硬上限 25）
          "seller": {...}|None,             # 卖家资料（仅 shopall 页有）
        }
    """
    if not html_text or len(html_text) < 200:
        return {"_status": "empty_page", "items": []}
    if any(m in html_text for m in _BLOCK_MARKERS):
        return {"_status": "blocked", "items": []}
    init = _init_data(html_text)
    if not isinstance(init, dict):
        return {"_status": "no_next_data", "items": []}
    raw_items = _collect_listing_items(init)
    sr = init.get("searchResult") or {}
    return {
        "_status": "ok",
        "items": [_normalize_list_item(x) for x in raw_items],
        "count": sr.get("count"),
        "max_page": _dig(sr, "paginationV2", "maxPage", default=None),
        "seller": _normalize_seller(init.get("seller")),
    }


if __name__ == "__main__":
    # 冒烟测试：python parser.py <保存的商品页.html>
    import sys

    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 2:
        print("用法: python parser.py <商品页.html 或 __NEXT_DATA__.json>")
        raise SystemExit(1)
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        html = f.read()
    parsed = WalmartParser().parse_product(html)
    print(json.dumps(parsed, ensure_ascii=False, indent=2))
