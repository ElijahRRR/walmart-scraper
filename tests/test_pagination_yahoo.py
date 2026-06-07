"""翻页采集优化的纯逻辑单测（不触网）：
  - Yahoo 结果页 → Walmart ID 提取（site: 通道）
  - 自适应失败率限流 _AdaptiveThrottle
  - _paged_listing 连续空页容忍 + maxPage 收紧（用桩替换 _get/parse_listing）
运行: pytest tests/test_pagination_yahoo.py
"""
import app.engine.collector as col
from app.engine.collector import (
    extract_walmart_ids_from_yahoo, _AdaptiveThrottle, WalmartCollector,
    EMPTY_PAGE_TOLERANCE,
)


# ── Yahoo 提取 ──────────────────────────────────────────────────────────
def test_yahoo_extract_basic_dedup_order():
    html = (
        'a href="https://r.search.yahoo.com/RU=https%3a%2f%2fwww.walmart.com'
        '%2fip%2fLasko-Fan%2f42379869/RK=2" '
        'b href="...RU=https%3a%2f%2fwww.walmart.com%2fip%2fStraw-Bag%2f20052121912/RK=2" '
        'dup href="...RU=https%3a%2f%2fwww.walmart.com%2fip%2fLasko-Fan%2f42379869/RK=2"'
    )
    ids = extract_walmart_ids_from_yahoo(html)
    assert ids == ["42379869", "20052121912"]  # 去重保序


def test_yahoo_extract_ignores_non_walmart_and_empty():
    assert extract_walmart_ids_from_yahoo("") == []
    assert extract_walmart_ids_from_yahoo("RU=https%3a%2f%2fwww.amazon.com%2fdp%2fB01") == []


# ── 自适应限流 ──────────────────────────────────────────────────────────
def test_throttle_slowdown_and_stop():
    t = _AdaptiveThrottle(window=20, warn_at=(10, 7), stop_at=(20, 18))
    assert not t.slowed and not t.should_stop()
    for _ in range(7):                 # 近10次内7失败 → 降速
        t.record(failed=True)
    for _ in range(3):
        t.record(failed=False)
    assert t.slowed is True
    assert t.jitter() >= t.slow_jitter[0]
    for _ in range(20):                # 灌满失败 → 建议停
        t.record(failed=True)
    assert t.should_stop() is True


def test_throttle_recovers():
    t = _AdaptiveThrottle()
    for _ in range(10):
        t.record(failed=True)
    assert t.slowed
    for _ in range(10):                # 全成功冲刷窗口
        t.record(failed=False)
    assert t.slowed is False
    assert t.jitter() <= t.base_jitter[1]


# ── _paged_listing 停止逻辑（桩替换网络/解析）─────────────────────────────
def _make_collector_with_pages(pages):
    """pages: list[list[item_id]]，每页商品 id 列表（None 表示该页取页失败）。
    按 URL 里的 page= 取页（对重试稳定，不受调用次数影响）。"""
    import re as _re
    c = WalmartCollector.__new__(WalmartCollector)  # 不跑 __init__（不建代理）

    def fake_get(url, accept, pace=True):
        page = int(_re.search(r"page=(\d+)", url).group(1))
        idx = page - 1
        if idx >= len(pages) or pages[idx] is None:
            return None
        return f"PAGE::{idx}"

    def fake_parse(html):
        idx = int(html.split("::")[1])
        ids = pages[idx]
        return {"items": [{"product_id": str(x)} for x in ids],
                "count": None, "max_page": None}

    c._get = fake_get
    col.parse_listing = fake_parse  # 模块级函数桩
    return c


def test_paged_tolerates_one_dup_page(monkeypatch):
    orig = col.parse_listing
    try:
        # 第2页全是第1页的重复(无新增)，但第3页又有新→不能在第2页就停
        c = _make_collector_with_pages([[1, 2], [1, 2], [3, 4], [3, 4], [3, 4]])
        items, truncated = c._paged_listing("u?page={page}", max_pages=10)
        got = sorted(int(it["product_id"]) for it in items)
        assert got == [1, 2, 3, 4]      # 第3页的新品没被漏
        assert truncated is False
    finally:
        col.parse_listing = orig


def test_paged_stops_after_consecutive_empty(monkeypatch):
    orig = col.parse_listing
    try:
        # 连续 EMPTY_PAGE_TOLERANCE 页无新增 → 停
        c = _make_collector_with_pages([[1, 2], [1], [2], [9, 9], [9, 9]])
        items, _ = c._paged_listing("u?page={page}", max_pages=10)
        assert sorted(int(it["product_id"]) for it in items) == [1, 2]
        assert EMPTY_PAGE_TOLERANCE == 2
    finally:
        col.parse_listing = orig


def test_paged_truncated_on_fetch_fail(monkeypatch):
    orig = col.parse_listing
    try:
        c = _make_collector_with_pages([[1, 2], [3, 4], None])  # 第3页取页失败
        items, truncated = c._paged_listing("u?page={page}", max_pages=10)
        assert sorted(int(it["product_id"]) for it in items) == [1, 2, 3, 4]
        assert truncated is True
    finally:
        col.parse_listing = orig
