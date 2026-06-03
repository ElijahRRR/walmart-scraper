"""沃尔玛采集探针：复用单个短效 IP 采集多个商品，仅在被封时才换 IP。
用法: python probe.py [URL ...]   不带参数则跑内置 3 个样品。
"""
import logging
import sys

from curl_cffi import requests as cffi

from parser import WalmartParser
from proxy import ProxyPool

SAMPLES = [
    "https://www.walmart.com/ip/seort/14469755459",   # WFS Shark
    "https://www.walmart.com/ip/seort/42379869",       # 自营 Lasko
    "https://www.walmart.com/ip/Straw-Hobo-Handbags-for-Women-Fashion-Woven-Straw-Bag-for-Beach-Holiday/20052121912",  # SFF
]

MAX_RETRY = 2  # 单个 URL 因封控换 IP 的最大重试次数


def fetch(url: str, pool: ProxyPool, parser: WalmartParser) -> dict:
    """带防封重试地采集一个 URL：被封则换 IP 重试。"""
    for attempt in range(MAX_RETRY + 1):
        pool.pace()                       # 温和限速
        proxy = pool.current()            # 复用当前 IP
        try:
            resp = cffi.get(url, impersonate="chrome", timeout=40,
                            proxies={"http": proxy, "https": proxy})
        except Exception as exc:          # 网络/代理失败 → 换 IP
            pool.rotate(f"请求异常 {type(exc).__name__}")
            continue
        r = parser.parse_product(resp.text)
        if resp.status_code == 200 and r["_status"] == "ok":
            return r
        # 非 200 或被拦 → 换 IP 重试
        pool.rotate(f"HTTP {resp.status_code} / {r['_status']}")
    return {"_status": "give_up", "url": url}


def main(urls: list[str]) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    pool, parser = ProxyPool(), WalmartParser()
    for url in urls:
        r = fetch(url, pool, parser)
        if r["_status"] == "ok":
            print(f"✅ {r['product_id']} | {r['brand']} | ${r['price']} "
                  f"运费{r['ship_price']} | {r['fulfillment_channel']} "
                  f"| UPC {r['upc']} GTIN13 {r['gtin13']} | 评分{r['rating']}")
        else:
            print(f"❌ {url} → {r['_status']}")
    print(f"\n成本统计：采集 {len(urls)} 个商品，共提取 {pool.stats['extractions']} 个 IP")


if __name__ == "__main__":
    main(sys.argv[1:] or SAMPLES)
