#!/usr/bin/env python3
"""单IP寿命压测脚本 — stress_ip_lifespan.py

⚠️  警告：真实运行会消耗代理 IP（可能被封控/扣费）。
    请确认已配置 PROXY_API_KEY 并了解费用后再运行。
    脚本使用 --dry-run 模式可以不发真实请求（mock 封控行为）。

用法：
    # 真实运行（消耗IP）：
    python scripts/stress_ip_lifespan.py --ids 123456 789012 ...
    python scripts/stress_ip_lifespan.py --keyword "phone case" --max-pages 5

    # Dry-run（mock 第 N 次请求触发封控，不发真实网络请求）：
    python scripts/stress_ip_lifespan.py --dry-run --ids 111 222 333 --block-at 3

    # 可选参数：
    --lane       使用哪个 lane（默认 0）
    --block-at   dry-run 时第几次请求触发封控（默认 5，即第5次）
    --output     结果输出到 JSON 文件路径（可选）

输出示例：
    {
      "ip": "1.2.3.4:8080",
      "total_requests": 12,
      "blocked_at_request": 12,
      "block_reason": "blocked",
      "products_saved": 10,
      "elapsed_sec": 42.3
    }
"""
import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

# 确保项目根在 sys.path 中
_SCRIPT_DIR = Path(__file__).parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("stress_ip_lifespan")

# BLOCKED_STATUSES 来自 runner，避免重复定义
from app.service.runner import BLOCKED_STATUSES


def _make_mock_collector(block_at: int):
    """构造一个 mock WalmartCollector：第 block_at 次调用 collect_detail 返回封控。

    前 block_at-1 次返回 {"_status": "ok", "product_id": ..., "price": ...}。
    第 block_at 次返回 {"_status": "blocked", "product_id": ...}。
    """
    call_count = [0]

    class _MockCollector:
        def collect_detail(self, product_id: str) -> dict:
            call_count[0] += 1
            n = call_count[0]
            if n >= block_at:
                return {"_status": "blocked", "product_id": product_id,
                        "_reason": f"mock 封控 at call #{n}"}
            return {
                "_status": "ok",
                "product_id": product_id,
                "title": f"Mock Product {product_id}",
                "price": 9.99 + n * 0.01,
                "seller_count": 3,
                "seller": {"name": "MockSeller", "id": "MOCK001",
                            "type": "wfs", "catalog_seller_id": None,
                            "rating": 4.5, "review_count": 100},
                "currency": "USD",
                "is_wfs": True,
                "seller_fulfilled": False,
            }

    return _MockCollector(), call_count


def run_stress(
    product_ids: list[str],
    dry_run: bool = False,
    block_at: int = 5,
    lane_id: int = 0,
) -> dict:
    """执行单IP寿命压测，返回统计结果 dict。

    Args:
        product_ids: 要采集的商品 ID 列表
        dry_run:     True=使用 mock collector（不发真实网络请求）
        block_at:    dry-run 时第几次请求触发封控
        lane_id:     使用哪个 lane

    Returns:
        {
          "ip": str,
          "total_requests": int,          # 共发出的采集请求数
          "blocked_at_request": int|None, # 第几次请求触发封控（None=未封控）
          "block_reason": str|None,       # 封控原因（_status）
          "products_saved": int,          # 成功落库商品数
          "elapsed_sec": float,
        }
    """
    from app.db import init_db
    init_db()

    start = time.time()
    products_saved = 0
    total_requests = 0
    blocked_at_request: Optional[int] = None
    block_reason: Optional[str] = None

    if dry_run:
        collector, call_count_ref = _make_mock_collector(block_at)
        ip_display = "mock-ip:8080"
        logger.info("[dry-run] 使用 mock collector，封控触发于第 %d 次请求", block_at)
    else:
        # 真实模式：从 LanePool 取 lane，确保有 IP
        from app.service.lanes import get_lane_pool
        from app.engine.collector import WalmartCollector
        pool = get_lane_pool()
        lane = pool.lane(lane_id)
        ip_display = lane.ensure_ip() or "unknown"
        collector = WalmartCollector(pool=lane.pool)
        logger.info("[real] 使用真实 collector，IP: %s", ip_display)

    for idx, pid in enumerate(product_ids, 1):
        logger.info("采集 [%d/%d] product_id=%s", idx, len(product_ids), pid)
        try:
            result = collector.collect_detail(pid)
        except Exception as exc:
            logger.warning("collect_detail 异常 product_id=%s err=%s", pid, exc)
            total_requests += 1
            continue

        total_requests += 1
        st = result.get("_status", "")

        if st in BLOCKED_STATUSES:
            blocked_at_request = total_requests
            block_reason = st
            logger.warning(
                "IP 寿命终止：在第 %d 次请求被封控（_status=%s）",
                total_requests, st,
            )
            break

        if st in ("ok", "partial"):
            # 落库（可选：真实压测时可跳过落库以加速）
            try:
                from app.service.runner import save_product
                if save_product(result):
                    products_saved += 1
            except Exception as exc:
                logger.warning("save_product 失败（不影响压测统计）: %s", exc)

    elapsed = round(time.time() - start, 2)

    summary = {
        "ip": ip_display,
        "total_requests": total_requests,
        "blocked_at_request": blocked_at_request,
        "block_reason": block_reason,
        "products_saved": products_saved,
        "elapsed_sec": elapsed,
    }

    logger.info("压测完成: %s", json.dumps(summary, ensure_ascii=False))
    return summary


def main():
    parser = argparse.ArgumentParser(description="单IP寿命压测脚本")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ids", nargs="+", metavar="ID", help="指定商品 ID 列表")
    group.add_argument("--keyword", metavar="KW", help="关键词（自动从搜索结果取 ID）")

    parser.add_argument("--max-pages", type=int, default=3,
                        help="关键词模式：最多翻页数（默认 3）")
    parser.add_argument("--dry-run", action="store_true",
                        help="使用 mock collector，不发真实网络请求")
    parser.add_argument("--block-at", type=int, default=5,
                        help="dry-run 时第几次请求触发封控（默认 5）")
    parser.add_argument("--lane", type=int, default=0,
                        help="使用哪个 lane（默认 0）")
    parser.add_argument("--output", metavar="FILE",
                        help="结果输出到 JSON 文件路径（可选）")

    args = parser.parse_args()

    if args.keyword and not args.dry_run:
        # 真实关键词模式：先跑一次关键词采集取 ID 列表（翻页，不取详情）
        from app.db import init_db
        from app.service.runner import run_keyword
        logger.info("关键词模式：先采列表获取 product_ids...")
        # 此处只取列表，不取详情，节约 IP
        # 真实使用场景：用 run_keyword 取 listing，再从 DB 读 product_id 列表
        logger.warning("关键词真实模式需要先跑列表采集，请自行实现或使用 --ids 模式。")
        sys.exit(1)
    elif args.keyword and args.dry_run:
        # dry-run 模式下用 keyword 生成虚假 ID 列表
        product_ids = [f"mock_{args.keyword.replace(' ', '_')}_{i}" for i in range(20)]
        logger.info("dry-run 关键词模式：生成 %d 个 mock ID", len(product_ids))
    else:
        product_ids = args.ids

    if not product_ids:
        logger.error("product_ids 为空，请提供 --ids 参数")
        sys.exit(1)

    result = run_stress(
        product_ids=product_ids,
        dry_run=args.dry_run,
        block_at=args.block_at,
        lane_id=args.lane,
    )

    print("\n" + "=" * 50)
    print("单IP寿命压测结果")
    print("=" * 50)
    for k, v in result.items():
        print(f"  {k:25s}: {v}")
    print("=" * 50)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已写入: {out_path}")


if __name__ == "__main__":
    main()
