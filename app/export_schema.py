"""导出列定义（中文表头）—— 要调整导出的列/顺序/名称，改这里即可。

每项为 (数据库列名, 中文表头)。导出时按此顺序输出，未列出的列不导出。
布尔类(是否WFS/是否有货等)会渲染为 是/否。
"""

# ── 商品详情(products)导出列 ─────────────────────────────────────────────
PRODUCT_COLUMNS: list[tuple[str, str]] = [
    ("product_id",            "产品ID"),
    ("title",                 "标题"),
    ("brand",                 "品牌"),
    ("category",              "类目"),
    ("price",                 "价格"),
    ("was_price",             "原价"),
    ("currency",              "货币"),
    ("ship_price",            "运费"),
    ("fulfillment_channel",   "履约渠道"),
    ("rating",                "评分"),
    ("reviews",               "评价数"),
    ("upc",                   "UPC"),
    ("gtin13",                "GTIN13"),
    ("seller_name",           "卖家名称"),
    ("catalog_seller_id",     "卖家数字ID"),
    ("seller_type",           "卖家类型"),
    ("seller_rating",         "卖家评分"),
    ("seller_review_count",   "卖家评价数"),
    ("seller_count",          "卖家总数"),
    ("image_url",             "主图"),
    ("images",                "全部图片"),
    ("url",                   "商品链接"),
    ("long_description_text", "详情描述"),
    ("product_details",       "规格参数"),
    ("weight",                "重量"),
    ("snapshot_at",           "采集时间"),
    ("task_id",               "任务ID"),
]

# ── 列表项(listings)导出列 ───────────────────────────────────────────────
LISTING_COLUMNS: list[tuple[str, str]] = [
    ("product_id",       "产品ID"),
    ("title",            "标题"),
    ("brand",            "品牌"),
    ("price",            "价格"),
    ("rating",           "评分"),
    ("reviews",          "评价数"),
    ("seller_name",      "卖家名称"),
    ("seller_id",        "卖家ID"),
    ("fulfillment_type", "履约类型"),
    ("url",              "商品链接"),
    ("image_url",        "主图"),
    ("snapshot_at",      "采集时间"),
    ("task_id",          "任务ID"),
]

# 按 kind 取列定义
COLUMNS_BY_KIND = {
    "products": PRODUCT_COLUMNS,
    "listings": LISTING_COLUMNS,
}

# 布尔类列：导出渲染为 是/否（值为 1/0）
_BOOL_COLUMNS = {"is_wfs", "in_stock", "has_change", "seller_fulfilled"}


def render_cell(col: str, value):
    """单元格值渲染：布尔列→是/否；None→空串；其余原样。"""
    if col in _BOOL_COLUMNS:
        if value in (1, "1", True):
            return "是"
        if value in (0, "0", False):
            return "否"
        return ""
    return "" if value is None else value
