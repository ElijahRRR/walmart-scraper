#!/usr/bin/env python3
"""本地会话上报脚本（在装有 BitBrowser 的本机运行）。

流程：打开指定 BitBrowser 账号窗口 → 触发一次 isbm 抓取登录态会话(cookie/x-xsrf-token/wm_*)
      + 读取该账号专属代理 → POST 上传到 DMIT 服务器的 /seller-session。

之后 DMIT 端"从沃尔玛后台查 UPC/GTIN"开关即可用该会话(走账号代理)批量取权威 GTIN。
配合本机 cron 定时跑本脚本即可保持会话新鲜(时效自行观察后定间隔)。

用法：
  python scripts/upload_session.py \
      --browser-id <BitBrowser窗口ID> \
      --dmit-url https://your-dmit-host:8900 \
      --dmit-key <DMIT 的 API_KEY>

也可用环境变量：BROWSER_ID / DMIT_URL / DMIT_KEY / BIT_API / BIT_API_KEY
退出码：0=成功上传；非 0=失败(供 cron 判断/告警)。
"""
import os
import sys
import json
import time
import argparse
from pathlib import Path

# 允许从仓库根直接 `python scripts/upload_session.py` 运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="导出 BitBrowser 账号会话并上传到 DMIT")
    ap.add_argument("--browser-id", default=os.environ.get("BROWSER_ID", ""),
                    help="BitBrowser 窗口 ID")
    ap.add_argument("--dmit-url", default=os.environ.get("DMIT_URL", ""),
                    help="DMIT 服务地址，如 https://host:8900")
    ap.add_argument("--dmit-key", default=os.environ.get("DMIT_KEY", ""),
                    help="DMIT 的 API_KEY（X-API-Key）")
    ap.add_argument("--bit-api", default=os.environ.get("BIT_API", "http://127.0.0.1:54345"),
                    help="BitBrowser 本地 API 地址")
    ap.add_argument("--bit-key", default=os.environ.get("BIT_API_KEY", ""),
                    help="BitBrowser Local API Token（或 .env / BIT_API_KEY 环境变量）")
    ap.add_argument("--save-local", default="", help="可选：同时把会话存到本地此路径")
    ap.add_argument("--timeout", type=int, default=30, help="上传超时秒")
    args = ap.parse_args()

    missing = [n for n, v in (("--browser-id", args.browser_id),
                              ("--dmit-url", args.dmit_url),
                              ("--dmit-key", args.dmit_key)) if not v]
    if missing:
        print(f"[错误] 缺少参数: {', '.join(missing)}", file=sys.stderr)
        return 2

    # 1) 开窗导出会话（需本机 BitBrowser 已运行且该账号登录态有效）
    try:
        from app.engine.isbm_client import capture_session
    except Exception as exc:
        print(f"[错误] 导入失败（在仓库根运行，且已 pip install -r requirements.txt + playwright）: {exc}",
              file=sys.stderr)
        return 3

    print(f"[1/2] 打开 BitBrowser 窗口 {args.browser_id} 导出会话…")
    try:
        session = capture_session(args.browser_id, api_url=args.bit_api, api_key=args.bit_key)
    except Exception as exc:
        print(f"[错误] 导出会话失败（BitBrowser 没开 / 账号登录态失效？需手动登录一次）: {exc}",
              file=sys.stderr)
        return 4

    cookie_len = len((session.get("headers") or {}).get("cookie", ""))
    proxy = session.get("proxy") or {}
    print(f"      会话 OK：cookie {cookie_len}B，代理 {proxy.get('type')}://{proxy.get('host')}:{proxy.get('port')}")
    if not proxy:
        print("[警告] 该账号未配置代理！上传后 DMIT 调用会因 require_proxy 被拒（防店铺关联）。", file=sys.stderr)

    if args.save_local:
        Path(args.save_local).write_text(json.dumps(session, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
        print(f"      已存本地副本: {args.save_local}")

    # 2) 上传 DMIT
    url = args.dmit_url.rstrip("/") + "/seller-session"
    print(f"[2/2] 上传到 {url} …")
    try:
        r = requests.post(url, json=session, headers={"X-API-Key": args.dmit_key},
                          timeout=args.timeout)
    except Exception as exc:
        print(f"[错误] 上传请求失败: {exc}", file=sys.stderr)
        return 5
    if r.status_code != 200:
        print(f"[错误] DMIT 返回 {r.status_code}: {r.text[:300]}", file=sys.stderr)
        return 6

    data = r.json()
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(data.get("captured_at", 0)))
    print(f"[完成] 会话已上传 DMIT。captured_at={ts} cookie={data.get('cookie_len')}B "
          f"has_proxy={data.get('has_proxy')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
