"""isbm_client 纯逻辑单测（不触网/不依赖 BitBrowser）。
运行: pytest tests/test_isbm.py
"""
import pytest
from app.engine.isbm_client import (
    split_gtin, parse_isbm_payload, proxy_to_curl, IsbmClient, ProxyRequired,
)


def test_split_gtin():
    # GTIN-14 以 00 开头 → 有 UPC-A
    assert split_gtin("00705888962999") == ("00705888962999", "0705888962999", "705888962999")
    # GTIN-14 单前导0(EAN-13 7开头) → 无 UPC-A
    assert split_gtin("07451660746594") == ("07451660746594", "7451660746594", None)
    # 13 位
    assert split_gtin("0748545027938") == ("0748545027938", "0748545027938", "748545027938")
    # 12 位
    assert split_gtin("046013460691")[2] == "046013460691"
    # 非法 / 全零
    assert split_gtin("abc") == (None, None, None)
    assert split_gtin("000000000000") == (None, None, None)


def test_parse_isbm_payload():
    data = {"status": "200", "payload": {"totalRecords": 1, "items": [{
        "productId": "1T6T5BWF0AN6", "productName": "Shoulder Brace", "brand": "NOBRAND",
        "category": "Health", "productClassType": "VARIANT",
        "gtins": ["06432341052907", "00693479990653"],
        "conditions": {"New": {"gtin": "06432341052907", "upc": "6432341052907",
                               "itemId": "10699516058",
                               "buyBoxPrice": 15.59, "image": "http://x/a.jpg"}},
    }]}}
    out = parse_isbm_payload(data)
    r = out["10699516058"]
    assert r["gtin"] == "06432341052907"          # 目录权威码
    assert r["gtin13"] == "6432341052907"
    assert r["upc"] == "6432341052907"            # ★ 后台原生 upc 被取到（EAN 也给）
    assert r["gtins"] == ["06432341052907", "00693479990653"]
    assert r["buybox_price"] == 15.59
    assert r["wpid"] == "1T6T5BWF0AN6"
    assert r["condition"] == "New"


def test_parse_isbm_empty():
    assert parse_isbm_payload({}) == {}
    assert parse_isbm_payload({"payload": {"items": []}}) == {}
    assert parse_isbm_payload(None) == {}


def test_proxy_to_curl():
    p = {"type": "socks5", "host": "1.2.3.4", "port": 99, "user": "u", "pass": "p"}
    assert proxy_to_curl(p) == {"http": "socks5h://u:p@1.2.3.4:99",
                                "https": "socks5h://u:p@1.2.3.4:99"}
    # http 代理无账密
    p2 = {"type": "http", "host": "1.2.3.4", "port": 80, "user": "", "pass": ""}
    assert proxy_to_curl(p2)["https"] == "http://1.2.3.4:80"
    assert proxy_to_curl(None) is None


def test_client_requires_proxy():
    # 无代理 + require_proxy=True → 拒绝（防店铺关联）
    with pytest.raises(ProxyRequired):
        IsbmClient({"headers": {"cookie": "x"}, "proxy": None})
    # 显式放开才允许裸 IP
    c = IsbmClient({"headers": {"cookie": "x"}, "proxy": None}, require_proxy=False)
    assert c.proxies is None


def test_client_group_size_clamp():
    s = {"headers": {"cookie": "x"},
         "proxy": {"type": "socks5", "host": "h", "port": 1, "user": "u", "pass": "p"}}
    assert IsbmClient(s, group_size=999).group_size == 50
    assert IsbmClient(s, group_size=0).group_size == 1
