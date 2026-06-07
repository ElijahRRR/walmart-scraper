"""卖家后台权威 GTIN 批量对账补全（opt-in，默认关闭）。

采集任务跑完后,若开启了"从沃尔玛后台查 UPC/GTIN",对该任务采到的 product_id
批量走 isbm-search-by-id(经账号代理),用**目录权威 GTIN** 覆盖公开页值,并记录
对账元数据(公开页原值 / 全变体 / 是否不一致)。

会话来源:config.SELLER_SESSION_FILE(由本地 capture_session 导出 + 同步到此机)。
会话缺失/失效 → 记 warning 跳过(优雅降级,不影响主采集),把缺失原因返回给调用方。
"""
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def build_enriched_fields(public_gtin13: Optional[str], public_upc: Optional[str],
                          isbm: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], dict]:
    """纯函数:合并公开页值与后台 isbm 结果 → (gtin13, upc, gtin_meta)。

    规则(用户既定):后台主码为权威 → gtin13/upc 用后台;公开页值留存到 meta;
    全变体留存;公开页与后台主码不一致时 mismatch=True。
    """
    cat_gtin13 = isbm.get("gtin13")        # 后台权威(13 位归一)
    cat_upc = isbm.get("upc")
    variants = isbm.get("gtins") or []
    # 不一致判定:两边都有值且 13 位归一后不同
    mismatch = bool(public_gtin13 and cat_gtin13 and public_gtin13 != cat_gtin13)
    meta = {
        "source": "seller_backend",
        "public_gtin13": public_gtin13,
        "public_upc": public_upc,
        "catalog_gtin": isbm.get("gtin"),   # 原样目录码(可上架)
        "variants": variants,
        "mismatch": mismatch,
        "wpid": isbm.get("wpid"),
    }
    # 权威值优先;后台没给则保留公开页
    gtin13 = cat_gtin13 or public_gtin13
    # upc：有后台 UPC-A 就用；否则——若与公开页不一致，公开页 upc 属于错变体，必须丢弃
    #（权威码常为 EAN-13 无 UPC-A，留空比留错码诚实）；一致才保留公开页 upc。
    if cat_upc:
        upc = cat_upc
    elif mismatch:
        upc = None
    else:
        upc = public_upc
    return gtin13, upc, meta


def _load_client(session_file: Optional[str], group_size: int = 30):
    """加载 IsbmClient;会话缺失/异常返回 (None, 原因)。"""
    from app import config
    path = session_file or getattr(config, "SELLER_SESSION_FILE", "")
    if not path:
        return None, "未配置 SELLER_SESSION_FILE"
    import os
    if not os.path.exists(path):
        return None, f"会话文件不存在: {path}（需本地 capture_session 导出并同步）"
    try:
        from app.engine.isbm_client import IsbmClient
        return IsbmClient.from_file(path, group_size=group_size), None
    except Exception as exc:
        return None, f"加载会话失败: {exc}"


def enrich_product_gtins(product_ids: List[str],
                         session_file: Optional[str] = None) -> Dict[str, Any]:
    """对一批 product_id 批量取后台权威 GTIN 并就地 UPDATE products 表。

    返回统计 {enriched, mismatched, missing, skipped, reason}。
    会话不可用时 skipped=len(ids) 并附 reason,不抛异常。
    """
    from app.engine.isbm_client import SessionExpired
    ids = [str(p).strip() for p in product_ids if str(p).strip()]
    stats = {"requested": len(ids), "enriched": 0, "mismatched": 0,
             "missing": 0, "skipped": 0, "reason": None}
    if not ids:
        return stats

    client, reason = _load_client(session_file)
    if client is None:
        stats["skipped"] = len(ids)
        stats["reason"] = reason
        logger.warning("后台GTIN对账跳过: %s", reason)
        return stats

    try:
        isbm_map = client.fetch(ids)
    except SessionExpired as exc:
        stats["skipped"] = len(ids)
        stats["reason"] = f"会话失效: {exc}（请在本地重新 capture_session）"
        logger.warning("后台GTIN对账中止: %s", stats["reason"])
        return stats

    from app.db import get_conn
    with get_conn() as conn:
        for pid in ids:
            entry = isbm_map.get(pid)
            if not entry or not entry.get("gtin13"):
                stats["missing"] += 1
                continue
            row = conn.execute(
                "SELECT gtin13, upc FROM products WHERE product_id=?", (pid,)
            ).fetchone()
            pub_g13 = row[0] if row else None
            pub_upc = row[1] if row else None
            gtin13, upc, meta = build_enriched_fields(pub_g13, pub_upc, entry)
            conn.execute(
                "UPDATE products SET gtin13=?, upc=?, gtin_meta=? WHERE product_id=?",
                (gtin13, upc, json.dumps(meta, ensure_ascii=False), pid),
            )
            stats["enriched"] += 1
            if meta["mismatch"]:
                stats["mismatched"] += 1

    logger.info("后台GTIN对账完成: 请求%d 补全%d 不一致%d 后台无%d",
                stats["requested"], stats["enriched"], stats["mismatched"], stats["missing"])
    return stats
