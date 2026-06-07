"""卖家后台 GTIN 对账补全的纯逻辑 + 优雅降级单测（不触网）。"""
from app.service.gtin_enrich import build_enriched_fields, enrich_product_gtins


def test_build_authoritative_override_and_mismatch():
    # 公开页是变体错码,后台主码不同 → 用后台,标 mismatch,留公开页值
    isbm = {"gtin": "06432341052907", "gtin13": "6432341052907", "upc": None,
            "gtins": ["06432341052907", "00693479990653"], "wpid": "1T6T5BWF0AN6"}
    g13, upc, meta = build_enriched_fields("0693479990653", "693479990653", isbm)
    assert g13 == "6432341052907"               # 后台权威
    assert meta["public_gtin13"] == "0693479990653"
    assert meta["mismatch"] is True
    assert meta["catalog_gtin"] == "06432341052907"
    assert meta["variants"] == ["06432341052907", "00693479990653"]
    assert meta["source"] == "seller_backend"
    assert upc is None   # 不一致时丢弃公开页错变体 upc（权威码无 UPC-A）


def test_build_consistent_no_mismatch():
    isbm = {"gtin": "00748545027938", "gtin13": "0748545027938", "upc": "748545027938",
            "gtins": ["00748545027938"]}
    g13, upc, meta = build_enriched_fields("0748545027938", "748545027938", isbm)
    assert g13 == "0748545027938"
    assert upc == "748545027938"
    assert meta["mismatch"] is False


def test_build_backend_missing_keeps_public():
    # 后台没给 gtin13 → 保留公开页值,不误判 mismatch
    g13, upc, meta = build_enriched_fields("0748545027938", "748545027938",
                                           {"gtin13": None, "upc": None, "gtins": []})
    assert g13 == "0748545027938"
    assert upc == "748545027938"
    assert meta["mismatch"] is False


def test_enrich_skips_without_session(monkeypatch):
    # 未配置会话 → 优雅跳过,不抛异常
    from app import config
    monkeypatch.setattr(config, "SELLER_SESSION_FILE", "", raising=False)
    stats = enrich_product_gtins(["123", "456"])
    assert stats["skipped"] == 2
    assert stats["enriched"] == 0
    assert "未配置" in stats["reason"]


def test_enrich_empty_ids():
    assert enrich_product_gtins([])["requested"] == 0
