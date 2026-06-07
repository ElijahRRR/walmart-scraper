"""seller_gtin.py 单测 —— 纯逻辑（不依赖浏览器/BitBrowser）。

覆盖 FIBER_JS 结果 → {item_id: gtin} 的映射、gtin 归一、非法项过滤，
以及模块在未安装 playwright 时仍可安全 import。
运行: pytest tests/test_seller_gtin.py  或  python tests/test_seller_gtin.py
"""
from app.engine.seller_gtin import (
    _map_fiber_results, _apply_gtin_backfill, FIBER_JS, DOM_JS,
    SellerGtinFetcher, BIT_API,
)


def test_map_fiber_basic_and_normalize():
    entries = [
        {"i": 0, "data": {"itemId": "42379869", "gtin": "046013460691"}},  # 12位 → 补0到13
        {"i": 1, "data": {"itemId": 20052121912, "gtin": "0660317094101"}},  # itemId 为 int
    ]
    m = _map_fiber_results(entries)
    assert m["42379869"]["gtin"] == "0046013460691"
    assert m["20052121912"]["gtin"] == "0660317094101"  # int itemId 转 str 键
    assert m["42379869"]["source"] == "fiber"


def test_map_fiber_filters_invalid():
    entries = [
        {"i": 0, "err": "nofiber"},                                   # 无 data
        {"i": 1, "data": {"itemId": "X", "gtin": "not-a-gtin"}},      # gtin 非法
        {"i": 2, "data": {"gtin": "0046013460691"}},                  # 缺 itemId
        {"i": 3, "data": {"itemId": "Y", "gtin": "000000000000"}},    # 全零无效
        "garbage",                                                    # 非 dict
    ]
    assert _map_fiber_results(entries) == {}


def test_map_fiber_bad_input():
    assert _map_fiber_results(None) == {}
    assert _map_fiber_results({}) == {}


def test_js_constants_intact():
    # raw 字符串必须保留 JS 关键标记与正则转义（不可被 Python 误转义）
    assert "memoizedProps" in FIBER_JS and "more-info-link-toggler" in FIBER_JS
    assert "itemDetails" in FIBER_JS
    assert "createTreeWalker" in DOM_JS
    assert r"\s*(\d+)" in DOM_JS          # GTIN 正则未被吞掉
    assert r"split('\n')" in DOM_JS       # JS 的 split('\n') 字面量仍是反斜杠n（未被 Python 转义成换行）
    assert r"\$(\d" in DOM_JS             # 价格正则 \$ 保留


def test_fetcher_headers_auth():
    f = SellerGtinFetcher("bid", api_key="test-bit-key-xyz")
    h = f._headers()
    assert h["x-api-key"] == "test-bit-key-xyz"
    assert h["Content-Type"] == "application/json"
    # 无 key 时不带鉴权头
    assert "x-api-key" not in SellerGtinFetcher("bid", api_key="")._headers()


def test_defaults():
    assert BIT_API.endswith(":54345")
    f = SellerGtinFetcher("bid", group_size=99)
    assert f.group_size == 50  # 卖家页一次最多 50，越界夹紧


def test_apply_backfill():
    results = [
        {"product_id": "42379869", "gtin13": None, "upc": None},   # 缺 → 回填
        {"product_id": "20052121912", "gtin13": "0660317094101"},  # 已有 → 不动
        {"product_id": "999", "gtin13": None},                      # 后台也没有 → 不动
    ]
    gmap = {"42379869": "0046013460691"}
    n = _apply_gtin_backfill(results, gmap)
    assert n == 1
    assert results[0]["gtin13"] == "0046013460691"
    assert results[0]["upc"] == "046013460691"            # 前导0 GTIN13 反推 UPC-A
    assert results[0]["_gtin_source"] == "seller_backend"
    assert results[1]["gtin13"] == "0660317094101"        # 原值未被覆盖
    assert "_gtin_source" not in results[1]
    assert results[2]["gtin13"] is None                   # 后台无 → 保持空


def test_apply_backfill_rich_native_upc():
    # gmap 为富结构时，优先用后台原生 upc（而非反推）
    results = [{"product_id": "20052121912", "gtin13": None, "upc": None}]
    gmap = {"20052121912": {"gtin": "00660317094101", "upc": "660317094101"}}
    n = _apply_gtin_backfill(results, gmap)
    assert n == 1
    assert results[0]["gtin13"] == "00660317094101"
    assert results[0]["upc"] == "660317094101"            # 原生 upc 直接用


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  [PASS] {name}")
    print("✅ 全部通过")
