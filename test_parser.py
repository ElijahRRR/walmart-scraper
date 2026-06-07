"""parser.py 验证测试 — 用三类真实商品（2026-06 实测）的字段值构造 fixture。

覆盖 WFS 判定的全部三条分支 + 运费 null 语义 + GTIN13 派生 + 新品 null 评分。
运行: python test_parser.py
"""
import json

from app.engine.parser import WalmartParser


def _wrap(product: dict, idml: dict) -> str:
    """包成 __NEXT_DATA__ 标准路径的 JSON 字符串（parser 接受裸 JSON）。"""
    return json.dumps(
        {"props": {"pageProps": {"initialData": {"data": {
            "product": product, "idml": idml,
        }}}}}
    )


# ── 1) 沃尔玛自营 Lasko 落地扇 (sellerType=INTERNAL, 免邮) ──────────────────
LASKO = _wrap(
    {
        "usItemId": "42379869", "brand": "Lasko",
        "name": 'Lasko 16" Oscillating Pedestal Fan ... Black, S16500',
        "type": "Electric Household Fans",
        "canonicalUrl": "/ip/Lasko-16-...-S16500-New/42379869",
        "sellerName": "Walmart.com", "sellerId": "F55CDC31AB754BB68FE0B39041159D63",
        "sellerType": "INTERNAL", "fulfillmentType": None,
        "priceInfo": {"currentPrice": {"price": 29.97, "priceString": "$29.97",
                                        "currencyUnit": "USD"},
                      "wasPrice": {"price": 52}, "shipPrice": None},
        "shippingPrice": None,
        # 自营免邮：有 shippingOption 但 shipPrice 缺 → ship_price 应归一为 0.0
        "shippingOption": {"availabilityStatus": "AVAILABLE", "shipMethod": "STANDARD",
                           "deliveryDate": "2026-06-08T00:00:00.000Z",
                           "maxDeliveryDate": "2026-06-10T00:00:00.000Z"},
        "averageRating": 4.3, "numberOfReviews": 42916, "upc": "046013460691",
        "imageInfo": {"thumbnailUrl": "https://i5.walmartimages.com/seo/lasko.jpeg",
                      "allImages": [{"url": "https://i5.walmartimages.com/seo/lasko.jpeg"}]},
    },
    {"longDescription": "<p>Stay cool with the Lasko 16\" Pedestal Fan.</p>",
     "specifications": [{"name": "Speeds", "value": "3"},
                        {"name": "Weight", "value": "9.25 lb"}]},
)

# ── 2) 第三方走 WFS Shark 喷雾扇 (fulfillmentType=FC, 免邮) ──────────────────
SHARK = _wrap(
    {
        "usItemId": "14469755459", "brand": "Shark",
        "name": "Shark FlexBreeze HydroGo Misting Fan ... FA052 Grey",
        "type": "Portable Fans", "canonicalUrl": "/ip/Shark-Fan-Portable-Cordless-FA052/14469755459",
        "sellerName": "Omni Crest llc", "sellerId": "C2F9387D763546FE803E88B316BBEFA9",
        "sellerType": "EXTERNAL", "catalogSellerId": 101114637,
        "sellerAverageRating": 4.6, "sellerReviewCount": 10,
        "fulfillmentType": "FC",
        # 多卖家：17 个可售卖家，除 buybox 外 16 个
        "transactableOfferCount": 17, "additionalOfferCount": 16,
        "secondaryOffers": [
            {"offerId": "OF1", "sellerName": "Best Deals LLC", "sellerId": "ABC123",
             "priceInfo": {"currentPrice": {"price": 112.50}},
             "condition": {"text": "New"}, "sellerAverageRating": 4.2},
        ],
        "priceInfo": {"currentPrice": {"price": 109.99, "currencyUnit": "USD"},
                      "wasPrice": {"price": 149.99}, "shipPrice": None},
        "shippingOption": {"availabilityStatus": "AVAILABLE", "shipMethod": "STANDARD",
                           "deliveryDate": "2026-06-07T00:00:00.000Z",
                           "maxDeliveryDate": "2026-06-09T00:00:00.000Z"},
        "averageRating": 4.4, "numberOfReviews": 122, "upc": "622356666268",
        "imageInfo": {"thumbnailUrl": "https://i5.walmartimages.com/seo/shark.jpeg",
                      "allImages": [{"url": "https://i5.walmartimages.com/seo/shark.jpeg"}]},
    },
    {"longDescription": "", "specifications": []},
)

# ── 3) 第三方自发货 SFF 草编包 (fulfillmentType=MARKETPLACE, 运费$5.99, 新品无评价) ──
STRAW = _wrap(
    {
        "usItemId": "20052121912", "brand": "Generic",
        "name": "Straw Hobo Handbags for Women Fashion Woven Straw Bag for Beach Holiday",
        "type": "Handbags",
        "canonicalUrl": "/ip/Straw-Hobo-Handbags-.../20052121912",
        "sellerName": "chongqinghuanruchengdianzishangwuyouxiangongsi",
        "sellerId": "107327974CDD4F4FB2F8366A7CFE8510",
        "sellerType": "EXTERNAL", "catalogSellerId": 103057684,
        "sellerAverageRating": 0, "sellerReviewCount": 0,
        "sellerStoreFrontURL": "https://www.walmart.com/global/seller/103057684",
        "fulfillmentType": "MARKETPLACE",
        "priceInfo": {"currentPrice": {"price": 27.99, "priceString": "$27.99",
                                       "currencyUnit": "USD"},
                      "wasPrice": None, "shipPrice": None},
        "shippingPrice": None,
        # SFF 真实运费在 shippingOption.shipPrice —— 不是免邮！
        "shippingOption": {"availabilityStatus": "AVAILABLE",
                           "deliveryDate": "2026-06-12T21:59:00.000Z",
                           "maxDeliveryDate": "2026-06-18T21:59:00.000Z",
                           "shipMethod": "STANDARD",
                           "shipPrice": {"price": 5.99, "priceString": "$5.99"}},
        "fulfillmentOptions": [{"orderLimit": 12, "maxOrderQuantity": 12}],
        "averageRating": None, "numberOfReviews": None, "upc": "660317094101",
        "imageInfo": {"thumbnailUrl": "https://i5.walmartimages.com/seo/straw.jpeg",
                      "allImages": [{"url": "https://i5.walmartimages.com/seo/straw.jpeg"},
                                    {"url": "https://i5.walmartimages.com/seo/straw2.jpeg"}]},
    },
    {"longDescription": "<p>Woven straw beach bag.</p>",
     "specifications": [{"name": "Brand", "value": "Generic"},
                        {"name": "Material", "value": "Straw"}]},
)


def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    return cond


def main():
    p = WalmartParser()
    ok = True

    print("● 1. 自营 Lasko (INTERNAL, 免邮)")
    r = p.parse_product(LASKO)
    ok &= check("status ok", r["_status"] == "ok")
    ok &= check("product_id=42379869", r["product_id"] == "42379869")
    ok &= check("brand=Lasko", r["brand"] == "Lasko")
    ok &= check("price=29.97", r["price"] == 29.97)
    ok &= check("was_price=52", r["was_price"] == 52)
    ok &= check("url 绝对路径", r["url"].startswith("https://www.walmart.com/ip/"))
    ok &= check("seller.is_walmart=True", r["seller"]["is_walmart"] is True)
    ok &= check("channel=walmart_internal", r["fulfillment_channel"] == "walmart_internal")
    ok &= check("is_wfs=False", r["is_wfs"] is False)
    ok &= check("ship_price 免邮=0.0", r["ship_price"] == 0.0)
    ok &= check("gtin13=0046013460691", r["gtin13"] == "0046013460691")
    ok &= check("rating=4.3 / reviews=42916", r["rating"] == 4.3 and r["reviews"] == 42916)
    ok &= check("product_details 2条", len(r["product_details"]) == 2)
    ok &= check("weight=9.25 lb", r["weight"] == "9.25 lb")

    print("● 2. 第三方 WFS Shark (FC, 免邮)")
    r = p.parse_product(SHARK)
    ok &= check("channel=wfs", r["fulfillment_channel"] == "wfs")
    ok &= check("is_wfs=True", r["is_wfs"] is True)
    ok &= check("seller.type=EXTERNAL", r["seller"]["type"] == "EXTERNAL")
    ok &= check("seller.rating=4.6", r["seller"]["rating"] == 4.6)
    ok &= check("catalog_seller_id=101114637", r["seller"]["catalog_seller_id"] == 101114637)
    ok &= check("ship_price 免邮=0.0", r["ship_price"] == 0.0)
    ok &= check("gtin13=0622356666268", r["gtin13"] == "0622356666268")
    ok &= check("seller_count=17", r["seller_count"] == 17)
    ok &= check("other_seller_count=16", r["other_seller_count"] == 16)
    ok &= check("buybox is_buybox=True", r["seller"]["is_buybox"] is True)
    ok &= check("other_sellers 解析1个", len(r["other_sellers"]) == 1)
    ok &= check("other_seller 价格112.5", r["other_sellers"][0]["price"] == 112.5)
    ok &= check("other_sellers_complete=False(还有15个未取)", r["other_sellers_complete"] is False)

    print("● 3. 第三方 SFF 草编包 (MARKETPLACE, $5.99, 新品无评价)")
    r = p.parse_product(STRAW)
    ok &= check("channel=seller_fulfilled", r["fulfillment_channel"] == "seller_fulfilled")
    ok &= check("is_wfs=False", r["is_wfs"] is False)
    ok &= check("seller_fulfilled=True", r["seller_fulfilled"] is True)
    ok &= check("ship_price=5.99 (非免邮!)", r["ship_price"] == 5.99)
    ok &= check("ship_info.max_delivery_date", r["ship_info"]["max_delivery_date"] == "2026-06-18T21:59:00.000Z")
    ok &= check("order_limit=12", r["order_limit"] == 12)
    ok &= check("was_price=None 未打折", r["was_price"] is None)
    ok &= check("rating=None 新品", r["rating"] is None)
    ok &= check("_has_reviews=False", r["_has_reviews"] is False)
    ok &= check("images 2张", len(r["images"]) == 2)
    ok &= check("gtin13=0660317094101", r["gtin13"] == "0660317094101")

    print("● 4. 异常输入降级")
    ok &= check("空页 → empty_page", p.parse_product("")["_status"] == "empty_page")
    ok &= check("拦截页 → blocked", p.parse_product("x" * 300 + "px-captcha")["_status"] == "blocked")
    ok &= check("无NEXT_DATA → no_next_data", p.parse_product("<html>" + "y" * 300 + "</html>")["_status"] == "no_next_data")

    print("● 5. 标识符提取（改进1：原生 gtin13 优先 / 缺 upc 不再漏 / 防张冠李戴）")
    _PAD = "x" * 220  # 填充使 JSON > 200 字符，绕过 parser 的空页守卫
    # 5a) 头号缺陷修复：有原生 gtin13、无 upc（marketplace 商品常见）
    r = p.parse_product(_wrap(
        {"usItemId": "1", "name": _PAD, "gtin13": "0193575030333"}, {}))
    ok &= check("5a 原生 gtin13 取到", r["gtin13"] == "0193575030333")
    ok &= check("5a upc 反推(去前导0)", r["upc"] == "193575030333")
    # 5b) 原生 gtin13 落在 idml
    r = p.parse_product(_wrap(
        {"usItemId": "2", "name": _PAD}, {"gtin13": "0840095857772", "specifications": []}))
    ok &= check("5b idml.gtin13 取到", r["gtin13"] == "0840095857772")
    # 5c) 原生 gtin13 优先于"由 upc 派生"（二者不同值时不被覆盖）
    r = p.parse_product(_wrap(
        {"usItemId": "3", "name": _PAD, "upc": "012345678905",
         "gtin13": "0840095857772"}, {}))
    ok &= check("5c gtin13 用原生非派生", r["gtin13"] == "0840095857772")
    ok &= check("5c upc 保留原值", r["upc"] == "012345678905")
    # 5d) 仅 upc → gtin13 仍按旧逻辑派生（向后兼容）
    r = p.parse_product(_wrap({"usItemId": "4", "name": _PAD, "upc": "046013460691"}, {}))
    ok &= check("5d 仅upc→gtin13派生", r["gtin13"] == "0046013460691")
    # 5e) 真 EAN-13（非前导0）→ 无对应 UPC-A，upc 应留空
    r = p.parse_product(_wrap({"usItemId": "5", "name": _PAD, "gtin13": "5012345678900"}, {}))
    ok &= check("5e EAN gtin13 取到", r["gtin13"] == "5012345678900")
    ok &= check("5e EAN 不反推 upc", r["upc"] is None)
    # 5f) 防张冠李戴：主商品无标识符，变体子树里的 upc 不应被抓
    r = p.parse_product(_wrap(
        {"usItemId": "6", "name": _PAD,
         "variantsMap": {"x": {"usItemId": "6-v", "upc": "111111111111"}}}, {}))
    ok &= check("5f 变体 upc 不抓(防串号)", r["upc"] is None and r["gtin13"] is None)
    # 5g) gtin13 从 specifications 取（name 含 GTIN）
    r = p.parse_product(_wrap(
        {"usItemId": "7", "name": _PAD},
        {"specifications": [{"name": "Global Trade Item Number (GTIN)",
                             "value": "00810116120475"}]}))
    ok &= check("5g specs.GTIN 取到", r["gtin13"] == "00810116120475")

    print()
    print("✅ 全部通过" if ok else "❌ 有失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
