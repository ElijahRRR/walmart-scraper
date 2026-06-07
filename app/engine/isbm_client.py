"""Walmart 卖家后台 isbm-search-by-id 直连客户端（权威 GTIN 高速通道）。

逆向自工具箱 ISBM_HOOK_JS：卖家中心 add-items 搜索时调用的内部 JSON 接口
  GET https://seller.walmart.com/aurora/v1/items/isbm-search-by-id?search=<id1,id2,...>
鉴权 = 登录态 cookie + x-xsrf-token + 少量 wm_* 头。拿到一次会话后即可用 curl_cffi
直接高速批量调用，**不需要 BitBrowser 渲染**，比逐页抓快一个量级，且返回的是
**目录权威 GTIN**（`conditions.<cond>.gtin`）+ 全变体 `gtins[]` + BuyBox 价。

★关键：调用必须走【该账号在 BitBrowser 里配的那个代理】，否则 Walmart 看到的 IP
  与账号日常不一致，会触发关联风控影响店铺。本模块强制带代理（缺代理则拒绝调用）。

工作模型：
  1) capture_session(browser_id) —— 偶尔开一次 BitBrowser，导出会话(cookie/xsrf/wm_*)+账号代理 → session.json
  2) IsbmClient(session).fetch(ids) —— 之后纯 curl_cffi 走账号代理批量取，会话失效再 capture 一次
"""
import os
import re
import json
import time
import uuid
import logging
from typing import Any, Dict, List, Optional

import requests
from curl_cffi import requests as cffi

logger = logging.getLogger(__name__)

ISBM_URL = "https://seller.walmart.com/aurora/v1/items/isbm-search-by-id"
BIT_API = "http://127.0.0.1:54345"
BIT_API_KEY = os.environ.get("BIT_API_KEY", "")  # BitBrowser Local API Token（放 .env，勿硬编码）
# 调用时携带的会话/上下文头（除 cookie/x-xsrf-token 外的必要 wm_* 等）
_KEEP_HEADERS = (
    "cookie", "x-xsrf-token", "wm_svc.name", "wm_aurora.market",
    "wm_seller_category_restricted", "accept", "accept-language",
    "user-agent", "referer", "sec-ch-ua", "sec-ch-ua-mobile",
    "sec-ch-ua-platform", "content-type",
)


class SessionExpired(RuntimeError):
    """isbm 会话失效（需重新 capture_session）。"""


class ProxyRequired(RuntimeError):
    """缺少账号代理，拒绝裸 IP 调用（防店铺关联）。"""


# ── GTIN 归一（纯函数，可测）────────────────────────────────────────────
def _digits(v: Any) -> Optional[str]:
    """清成纯数字串（非空、非全零），否则 None。用于后台原生 upc。"""
    if v is None:
        return None
    d = re.sub(r"\D", "", str(v))
    if not d or set(d) == {"0"}:
        return None
    return d


def split_gtin(g: Any):
    """任意 GTIN(12/13/14) → (gtin_raw, gtin13, upc)。
    upc 仅当是 UPC-A（GTIN-14 以 00 开头）时给出，真 EAN 返回 None。"""
    d = re.sub(r"\D", "", str(g or ""))
    if len(d) not in (12, 13, 14):
        return (None, None, None)
    if set(d) == {"0"}:
        return (None, None, None)
    g14 = d.zfill(14)
    gtin13 = g14[-13:] if g14[0] == "0" else g14         # 14 位带前导0 → 13
    upc = g14[2:] if g14[:2] == "00" else None           # 00+12位 → UPC-A
    return (d, gtin13, upc)


def parse_isbm_payload(data: Any) -> Dict[str, Dict[str, Any]]:
    """isbm 响应 JSON → {item_id: {gtin, gtin13, upc, gtins[], wpid, brand, name, ...}}。

    结构：payload.items[].{productId, productName, brand, category, gtins[],
                          conditions: {New|Used|...: {gtin, itemId, buyBoxPrice, image}}}
    按每个 condition 的 itemId 建键（搜索用的就是 itemId）。
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(data, dict):
        return out
    items = (data.get("payload") or {}).get("items") or []
    for it in items:
        if not isinstance(it, dict):
            continue
        base = {
            "wpid": it.get("productId"),
            "name": it.get("productName"),
            "brand": it.get("brand"),
            "category": it.get("category"),
            "gtins": it.get("gtins"),
            "product_class": it.get("productClassType"),
            "variant_info": it.get("variantInfo"),
        }
        conds = it.get("conditions") or {}
        for cond_name, c in conds.items():
            if not isinstance(c, dict):
                continue
            iid = c.get("itemId")
            if iid is None:
                continue
            raw, g13, derived_upc = split_gtin(c.get("gtin"))
            # ★ 后台原生 upc 优先（conditions.New.upc，Walmart 目录登记的 UPC，
            #   多为 GTIN 去前导零；EAN 商品也给）；缺失才退回从 GTIN 派生的 UPC-A。
            native_upc = _digits(c.get("upc"))
            out[str(iid)] = {
                **base,
                "item_id": str(iid),
                "condition": cond_name,
                "gtin": raw,            # 目录权威 GTIN（原样，可上架）
                "gtin13": g13,
                "upc": native_upc or derived_upc,
                "buybox_price": c.get("buyBoxPrice"),
                "suggested_price": c.get("suggestedPrice"),
                "image_url": c.get("image"),
            }
    return out


# ── BitBrowser：取账号代理 + 导出会话 ────────────────────────────────────
def get_browser_proxy(browser_id: str, api_url: str = BIT_API,
                      api_key: str = BIT_API_KEY) -> Optional[Dict[str, Any]]:
    """从 BitBrowser 读该账号配置的代理（防关联必须用它）。返回 dict 或 None(直连)。"""
    resp = requests.post(f"{api_url}/browser/detail", json={"id": browser_id},
                         headers={"Content-Type": "application/json", "x-api-key": api_key},
                         timeout=20)
    resp.raise_for_status()
    d = (resp.json() or {}).get("data") or {}
    ptype = (d.get("proxyType") or "").lower()
    host, port = d.get("host"), d.get("port")
    if ptype in ("", "noproxy", "none") or not host or not port:
        return None
    return {"type": ptype, "host": host, "port": int(port),
            "user": d.get("proxyUserName") or "", "pass": d.get("proxyPassword") or ""}


def proxy_to_curl(proxy: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """账号代理 dict → curl_cffi proxies。socks5 用 socks5h（DNS 走代理，防泄露）。"""
    if not proxy:
        return None
    scheme = "socks5h" if proxy["type"].startswith("socks5") else \
             ("socks4" if proxy["type"].startswith("socks4") else "http")
    auth = f"{proxy['user']}:{proxy['pass']}@" if proxy.get("user") else ""
    url = f"{scheme}://{auth}{proxy['host']}:{proxy['port']}"
    return {"http": url, "https": url}


def capture_session(browser_id: str, seed_ids: str = "42379869,20052121912",
                    api_url: str = BIT_API, api_key: str = BIT_API_KEY,
                    save_path: Optional[str] = None) -> Dict[str, Any]:
    """开一次 BitBrowser，触发一次 isbm，导出会话 headers + 账号代理。

    会话 = {headers:{cookie,x-xsrf-token,wm_*...}, proxy:{...}, browser_id, captured_at}。
    save_path 给定则落盘 json。这是"偶尔"才做的发证步骤。
    """
    from app.engine.seller_gtin import SellerGtinFetcher, SELLER_URL
    grabbed: Dict[str, Any] = {}
    f = SellerGtinFetcher(browser_id, api_url=api_url, api_key=api_key)
    f._connect()
    pg = f._get_page()

    def on_request(req):
        if "isbm-search-by-id" in req.url and "headers" not in grabbed:
            try:
                grabbed["headers"] = req.all_headers()
            except Exception:
                pass

    pg.on("request", on_request)
    # 用两组不同 id 触发（避免 SPA 命中缓存不发请求）
    for ids in (seed_ids, "9044918012,10117963471"):
        try:
            pg.goto(SELLER_URL.format(ids=ids), wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        for _ in range(25):
            if "headers" in grabbed:
                break
            time.sleep(1)
        if "headers" in grabbed:
            break
    f._cleanup()

    if "headers" not in grabbed:
        raise RuntimeError("未能捕获 isbm 请求（页面未触发？检查登录态）")

    raw = grabbed["headers"]
    headers = {k: v for k, v in raw.items()
               if not k.startswith(":") and k.lower() in _KEEP_HEADERS}
    proxy = get_browser_proxy(browser_id, api_url, api_key)
    session = {"headers": headers, "proxy": proxy, "browser_id": browser_id,
               "captured_at": int(time.time())}
    if save_path:
        with open(save_path, "w", encoding="utf-8") as fh:
            json.dump(session, fh, ensure_ascii=False, indent=2)
    return session


# ── isbm 客户端 ─────────────────────────────────────────────────────────
class IsbmClient:
    """用导出的会话 + 账号代理，直连 isbm-search-by-id 批量取权威 GTIN。"""

    def __init__(self, session: Dict[str, Any], group_size: int = 20,
                 require_proxy: bool = True, retries: int = 2) -> None:
        self.headers = dict(session.get("headers") or {})
        self.proxy = session.get("proxy")
        self.proxies = proxy_to_curl(self.proxy)
        self.group_size = max(1, min(int(group_size), 50))
        self.retries = retries
        # 防关联：默认强制走账号代理
        if require_proxy and not self.proxies:
            raise ProxyRequired("isbm 调用必须走账号代理（BitBrowser 配置的代理），否则影响店铺")

    @classmethod
    def from_file(cls, path: str, **kw) -> "IsbmClient":
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh), **kw)

    def _req(self, ids: List[str]) -> Dict[str, Dict[str, Any]]:
        h = {k: v for k, v in self.headers.items() if not k.startswith(":")}
        h["wm_qos.correlation_id"] = str(uuid.uuid4())   # 每请求新 UUID
        url = f"{ISBM_URL}?search={','.join(ids)}"
        last = None
        for _ in range(self.retries + 1):
            try:
                r = cffi.get(url, headers=h, proxies=self.proxies,
                             impersonate="chrome", timeout=40)
            except Exception as exc:
                last = f"请求异常 {type(exc).__name__}: {exc}"
                continue
            if r.status_code in (401, 403):
                raise SessionExpired(f"isbm HTTP {r.status_code}（会话失效，需重新 capture_session）")
            if r.status_code == 200:
                txt = r.text
                if "px-captcha" in txt or "<html" in txt[:200].lower():
                    raise SessionExpired("isbm 返回挑战/登录页（会话失效）")
                try:
                    return parse_isbm_payload(r.json())
                except Exception as exc:
                    last = f"JSON 解析失败: {exc}"
                    continue
            last = f"HTTP {r.status_code}"
        logger.warning("isbm 取批失败 ids=%s: %s", ids, last)
        return {}

    def fetch(self, item_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """批量取权威 GTIN。返回 {item_id: {...}}（只含成功项）。"""
        ids = [str(x).strip() for x in item_ids if str(x).strip()]
        out: Dict[str, Dict[str, Any]] = {}
        for i in range(0, len(ids), self.group_size):
            group = ids[i:i + self.group_size]
            got = self._req(group)
            # 严格只保留请求过的 id（防无关结果混入）
            for k, v in got.items():
                if k in set(group):
                    out[k] = v
            logger.info("[isbm] 组 %d 个 → 命中 %d 个", len(group), sum(1 for k in got if k in set(group)))
        return out
