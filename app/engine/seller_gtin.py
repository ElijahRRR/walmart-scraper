"""沃尔玛卖家后台 GTIN 采集（改进3）—— 经 BitBrowser 指纹浏览器复用已登录 seller 会话。

为什么需要：公开消费页 `walmart.com/ip` 对很多 marketplace 商品**不暴露 GTIN**；
权威 GTIN 在卖家中心 `seller.walmart.com/catalog/add-items`（需登录卖家态）。
本模块通过 BitBrowser 本地 API 打开/连接一个**已登录 Walmart 卖家中心**的指纹浏览器
窗口，经 CDP 用 Playwright 接管该会话，导航 add-items 搜索页，注入 JS 从
React Fiber 内部状态（首选）/ DOM（兜底）提取每个 itemId 的 gtin。

逆向自「沃尔玛工具箱.exe」与「walmart_gtin_checker」的 BitBrowser+Playwright 实现，
JS 注入逻辑（FIBER_JS / DOM_JS）按反汇编原样移植。

依赖：playwright（仅作 CDP 客户端，连接的是 BitBrowser 自带的 Chrome，
**无需** `playwright install`）。未安装时本模块仍可 import，调用 fetch() 才报错。

用法：
    with SellerGtinFetcher(browser_id="xxxx", api_key="6180...") as f:
        gtin_map = f.fetch_gtin_map(["42379869", "20052121912"])
        # → {"42379869": "0046013460691", ...}

命令行：
    python -m app.engine.seller_gtin <browser_id> <id1> <id2> ...
"""
import os
import re
import time
import logging
from typing import Any, Callable, Dict, List, Optional

import requests

from app.engine.parser import _valid_gtin, _valid_upc  # 复用统一的 GTIN/UPC 校验

logger = logging.getLogger(__name__)

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:  # 未装 playwright 时不阻断整个包的导入
    sync_playwright = None  # type: ignore
    PLAYWRIGHT_AVAILABLE = False

# ── BitBrowser 本地 API ──────────────────────────────────────────────
BIT_API = os.environ.get("BIT_API", "http://127.0.0.1:54345")
# 开启鉴权控制后，调用需带请求头 x-api-key: <token>
BIT_API_KEY = os.environ.get("BIT_API_KEY", "")  # BitBrowser Local API Token（放 .env，勿硬编码）

SELLER_URL = "https://seller.walmart.com/catalog/add-items?search={ids}"

# ── 注入 JS（按反汇编原样移植；务必用 raw 字符串保留 \s \$ \d \n 等转义）──
# (a) React Fiber 深挖：沿 fiber.return 上溯 60 层取 memoizedProps.itemDetails 的 {itemId, gtin}
FIBER_JS = r"""
() => {
    function fkey(el) {
        return Object.keys(el).find(k =>
            k.startsWith('__reactFiber') ||
            k.startsWith('__reactInternalInstance'));
    }
    function flatten(d) {
        const out = {};
        if (!d || typeof d !== 'object') return out;
        for (const [k, v] of Object.entries(d)) {
            if (v == null) continue;
            if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') {
                out[k] = v;
            } else if (Array.isArray(v)) {
                if (v.length && (typeof v[0] === 'string' || typeof v[0] === 'number')) {
                    out[k] = v;
                }
            } else if (typeof v === 'object') {
                if (k === 'productTypeDetails') {
                    if (v.description) out['productTypeDescription'] = v.description;
                    if (v.productTypeGroupName) out['productTypeGroup'] = v.productTypeGroupName;
                    if (v.productCategoryName) out['productCategoryName'] = v.productCategoryName;
                    if ('sfs' in v) out['supportSfs'] = !!v.sfs;
                    if ('wfs' in v) out['supportWfs'] = !!v.wfs;
                }
            }
        }
        return out;
    }
    const togglers = document.querySelectorAll(
        'a[data-automation-id="more-info-link-toggler"]');
    const out = [];
    for (let i = 0; i < togglers.length; i++) {
        const tog = togglers[i];
        const k = fkey(tog);
        if (!k) { out.push({i, err: 'nofiber'}); continue; }
        let f = tog[k], depth = 0, found = null;
        while (f && depth < 60) {
            const p = f.memoizedProps;
            if (p && typeof p === 'object' && !Array.isArray(p)) {
                if (p.itemDetails && typeof p.itemDetails === 'object') {
                    found = p.itemDetails; break;
                }
                for (const v of Object.values(p)) {
                    if (v && typeof v === 'object' && !Array.isArray(v)
                        && v.itemId && v.gtin) { found = v; break; }
                }
                if (found) break;
            }
            f = f.return; depth++;
        }
        if (!found) { out.push({i, err: 'noprops'}); continue; }
        out.push({i, data: flatten(found)});
    }
    return out;
}
"""

# (b) DOM TreeWalker 兜底：找以 GTIN：/GTIN: 开头的文本节点，向上 15 层找含 $ 和 添加/Add 的容器
DOM_JS = r"""
() => {
    const items = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
        acceptNode: function(node) {
            return node.textContent.trim().match(/^GTIN[：:]/) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
        }
    });
    while (walker.nextNode()) {
        const textNode = walker.currentNode;
        const gtinMatch = textNode.textContent.trim().match(/GTIN[：:]\s*(\d+)/);
        if (!gtinMatch) continue;
        const gtin = gtinMatch[1];
        let container = textNode.parentElement;
        let price = '', name = '', offerCount = '', imageUrl = '';
        for (let i = 0; i < 15; i++) {
            if (!container || !container.parentElement) break;
            container = container.parentElement;
            const text = container.innerText || '';
            if (text.includes('$') && (text.includes('添加') || text.includes('Add'))) {
                const pm = text.match(/\$(\d+\.?\d*)/);
                if (pm) price = pm[0];
                const om = text.match(/(\d+)\s*报价/) || text.match(/(\d+)\s*offer/i);
                if (om) offerCount = om[1];
                const lines = text.split('\n').map(l => l.trim()).filter(l =>
                    l && !l.startsWith('GTIN') && !l.startsWith('$') && l.length > 10 &&
                    !l.includes('Buy Box') && !l.includes('报价') && !l.includes('更多信息') &&
                    !l.includes('添加') && !l.includes('offer') && !l.includes('US Buy'));
                if (lines.length > 0) name = lines[0].substring(0, 200);
                const imgs = container.querySelectorAll('img');
                for (const img of imgs) {
                    if (img.src && img.src.includes('http') && !img.src.includes('icon') &&
                        !img.src.includes('svg') && img.width > 30) { imageUrl = img.src; break; }
                }
                break;
            }
        }
        items.push({ gtin, price, name, offerCount, imageUrl });
    }
    return items;
}
"""

GTIN_XPATH = 'xpath=//*[starts-with(normalize-space(text()), "GTIN")]'


class BitBrowserError(RuntimeError):
    """BitBrowser 本地 API 调用失败 / 未连接。"""


# ── 纯逻辑：可单测，不依赖浏览器 ────────────────────────────────────────
def _map_fiber_results(entries: Any) -> Dict[str, Dict[str, Any]]:
    """FIBER_JS 返回的 entries → {item_id: {gtin, name, price, ...}}（按 itemId 键，稳健）。

    每个 entry 形如 {i, data:{itemId, gtin, ...}} 或 {i, err:...}。
    只保留同时含 itemId 与合法 gtin 的条目。gtin 经 _valid_gtin 归一。
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(entries, list):
        return out
    for e in entries:
        if not isinstance(e, dict):
            continue
        data = e.get("data")
        if not isinstance(data, dict):
            continue
        item_id = data.get("itemId")
        gtin = _valid_gtin(data.get("gtin"))
        if item_id is None or not gtin:
            continue
        out[str(item_id)] = {
            "gtin": gtin,
            "upc": _valid_upc(data.get("upc")),            # 卖家后台原生 upc（最准）
            "wpid": data.get("productId"),                 # Walmart Product ID
            "name": data.get("productName") or data.get("name"),
            "brand": data.get("brand"),
            "buybox_price": data.get("buyBoxPrice"),
            "offer_count": data.get("offerCount"),
            "competitor_price": data.get("competitorPrice"),  # 跟卖竞品价（选品有用）
            "competitor_name": data.get("competitorName"),
            "support_wfs": data.get("supportWfs"),
            "support_sfs": data.get("supportSfs"),
            "image_url": data.get("image"),
            "gtins": data.get("gtins"),                    # 变体全部 GTIN
            "raw": data,
            "source": "fiber",
        }
    return out


class SellerGtinFetcher:
    """通过 BitBrowser + Playwright CDP 从卖家后台批量取 GTIN。"""

    def __init__(self, browser_id: str, *,
                 api_url: str = BIT_API, api_key: str = BIT_API_KEY,
                 group_size: int = 5, page_wait: float = 3.0,
                 group_delay: float = 2.0, nav_timeout: int = 30000,
                 log: Optional[Callable[[str], None]] = None) -> None:
        self.browser_id = browser_id
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.group_size = max(1, min(int(group_size), 50))  # 卖家页一次最多 50
        self.page_wait = page_wait
        self.group_delay = group_delay
        self.nav_timeout = nav_timeout
        self._log = log or (lambda m: logger.info(m))
        self.debug_url: Optional[str] = None
        self.pw = None
        self.browser = None
        self.page = None

    # ---- BitBrowser 本地 API ----
    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["x-api-key"] = self.api_key  # 开启鉴权控制后必带
        return h

    def open_browser(self) -> str:
        """POST /browser/open → 取 CDP 调试地址（data.http）。"""
        resp = requests.post(f"{self.api_url}/browser/open",
                             json={"id": self.browser_id},
                             headers=self._headers(), timeout=30)
        if resp.status_code != 200:
            raise BitBrowserError(f"/browser/open HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        if not body.get("success"):
            raise BitBrowserError(f"/browser/open 失败: {body.get('msg')}")
        data = body.get("data") or {}
        http = data.get("http") or data.get("ws")
        if not http:
            raise BitBrowserError(f"/browser/open 未返回 CDP 地址: {data}")
        self.debug_url = http if str(http).startswith("http") else f"http://{http}"
        self._log(f"BitBrowser 已打开窗口 {self.browser_id}，CDP={self.debug_url}")
        return self.debug_url

    def close_browser(self) -> None:
        """POST /browser/close（不强制成功）。"""
        try:
            requests.post(f"{self.api_url}/browser/close",
                          json={"id": self.browser_id},
                          headers=self._headers(), timeout=30)
        except Exception as exc:
            logger.warning("/browser/close 失败（忽略）: %s", exc)

    # ---- Playwright CDP ----
    def _connect(self) -> None:
        if not PLAYWRIGHT_AVAILABLE:
            raise BitBrowserError("未安装 playwright，请 `pip install playwright`（无需 playwright install）")
        if not self.debug_url:
            self.open_browser()
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.connect_over_cdp(self.debug_url)
        self._log("Playwright 已通过 CDP 接管 BitBrowser 实例")

    def _get_page(self):
        """选择要驱动的标签页。

        顺序：① 已在 seller.walmart.com 的标签 → ② BitBrowser 自带的真实标签
        （排除其 console 控制台标签）—— 复用它才带得上指纹/扩展上下文 → ③ 兜底新建。
        CDP 新建的标签缺指纹上下文，冷启动常渲染不出结果，故尽量复用已有标签。
        """
        ctx = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
        pages = list(ctx.pages)
        for pg in pages:                       # ① 卖家中心标签
            try:
                if "seller.walmart.com" in (pg.url or ""):
                    self.page = pg
                    self._safe_front(pg)
                    return pg
            except Exception:
                continue
        for pg in pages:                       # ② 默认真实标签（排除 BitBrowser console）
            u = (pg.url or "").lower()
            if "bitbrowser" not in u and "console" not in u:
                self.page = pg
                self._safe_front(pg)
                return pg
        self.page = ctx.new_page()             # ③ 兜底
        return self.page

    @staticmethod
    def _safe_front(pg) -> None:
        try:
            pg.bring_to_front()
        except Exception:
            pass

    def _cleanup(self) -> None:
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass
        try:
            if self.pw:
                self.pw.stop()
        except Exception:
            pass
        self.browser = self.pw = self.page = None

    # ---- 上下文管理 ----
    def __enter__(self) -> "SellerGtinFetcher":
        self._connect()
        self._get_page()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._cleanup()
        # 不自动 close_browser：保留已登录窗口，便于复用；如需关闭手动调 close_browser()

    # ---- 提取 ----
    def _wait_results(self, timeout: float = 25.0) -> bool:
        """轮询等待结果渲染（GTIN 文本出现）。该 SPA 几乎不进 networkidle，故用此法。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.page.evaluate(
                        '() => /GTIN[：:]/.test((document.body && document.body.innerText) || "")'):
                    return True
            except Exception:
                pass
            time.sleep(1)
        return False

    def _scrape_group(self, group: List[str]) -> Dict[str, Dict[str, Any]]:
        """导航一组 id 的 add-items 搜索页，返回 {item_id: {...}}。

        冷启动（刚 CDP 接管的页面）首次导航常渲染不出结果，故失败重试一次（reload）。
        """
        url = SELLER_URL.format(ids=",".join(group))
        for attempt in range(2):
            if attempt == 0:
                self.page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout)
            else:
                self._log("  结果未出，reload 重试一次…")
                try:
                    self.page.reload(wait_until="domcontentloaded", timeout=self.nav_timeout)
                except Exception:
                    self.page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout)
            time.sleep(self.page_wait)
            if self._wait_results():
                break
        else:
            self._log(f"  组 {group} 未检测到 GTIN 文本（可能无此商品/未登录）")
        # 触发懒加载
        try:
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(0.8)
            self.page.evaluate("window.scrollTo(0, 0)")
            time.sleep(0.5)
        except Exception:
            pass

        # React Fiber 提取（按真实 itemId 键，稳健）。
        # **只保留本组请求的 id**：无效/下架 id 会让卖家页退化成关键词搜索、显示推荐位
        # 等无关商品，必须按 itemId 过滤掉，否则会张冠李戴（把别的商品 GTIN 当成它的）。
        # 不用 DOM 位置兜底——DOM 无 itemId，位置错位时会错配 GTIN（正是要避免的不准）。
        result: Dict[str, Dict[str, Any]] = {}
        want = set(group)
        try:
            entries = self.page.evaluate(FIBER_JS)
            for iid, data in _map_fiber_results(entries).items():
                if iid in want:
                    result[iid] = data
        except Exception as exc:
            self._log(f"  FIBER_JS 异常: {exc}")
        return result

    def fetch(self, item_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """批量取 GTIN（自动分组）。返回 {item_id: {gtin, name, source, ...}}。"""
        if self.browser is None:
            raise BitBrowserError("未连接，请用 `with SellerGtinFetcher(...) as f:` 或先调用 _connect()")
        ids = [str(x).strip() for x in item_ids if str(x).strip()]
        out: Dict[str, Dict[str, Any]] = {}
        groups = [ids[i:i + self.group_size] for i in range(0, len(ids), self.group_size)]
        for gi, group in enumerate(groups):
            self._log(f"第 {gi + 1}/{len(groups)} 组（{len(group)} 个）...")
            try:
                got = self._scrape_group(group)
            except Exception as exc:
                self._log(f"  组 {group} 采集异常: {exc}")
                got = {}
            out.update(got)
            # 关键：卖家后台对"含无效/下架 id 的整组搜索"会整组无结果。
            # 故组内有未取到的且组 >1 个时，逐个单独重试，隔离坏 id、救回好的。
            missing = [pid for pid in group if pid not in got]
            if missing and len(group) > 1:
                self._log(f"  组内 {len(missing)} 个未取到 → 逐个重试隔离: {missing}")
                for pid in missing:
                    try:
                        out.update(self._scrape_group([pid]))
                    except Exception as exc:
                        self._log(f"    {pid} 重试异常: {exc}")
            if gi < len(groups) - 1 and self.group_delay > 0:
                time.sleep(self.group_delay)
        found = sum(1 for v in out.values() if v.get("gtin"))
        self._log(f"完成：{found}/{len(ids)} 个取到 GTIN")
        return out

    def fetch_gtin_map(self, item_ids: List[str]) -> Dict[str, str]:
        """便捷接口：返回 {item_id: gtin}（只含成功项）。"""
        return {k: v["gtin"] for k, v in self.fetch(item_ids).items() if v.get("gtin")}


def _apply_gtin_backfill(results: List[Dict[str, Any]],
                         gmap: Dict[str, Any]) -> int:
    """纯逻辑：把卖家后台结果回填到列表中 gtin13 为空的项（原地修改）。

    gmap 支持两种形态：{item_id: gtin_str} 或 {item_id: {"gtin":..., "upc":...}}。
    缺 upc 时优先用后台原生 upc，否则按前导零规则反推；打标 _gtin_source='seller_backend'。
    返回成功回填条数。
    """
    from app.engine.parser import _gtin13_to_upc
    n = 0
    for r in results:
        if not isinstance(r, dict) or r.get("gtin13"):
            continue
        pid = r.get("product_id")
        entry = gmap.get(str(pid)) if pid is not None else None
        if not entry:
            continue
        if isinstance(entry, dict):
            g = _valid_gtin(entry.get("gtin"))
            native_upc = _valid_upc(entry.get("upc"))
        else:
            g = _valid_gtin(entry)
            native_upc = None
        if not g:
            continue
        r["gtin13"] = g
        if not r.get("upc"):
            r["upc"] = native_upc or _gtin13_to_upc(g)
        r["_gtin_source"] = "seller_backend"
        n += 1
    return n


def backfill_gtin(results: List[Dict[str, Any]], browser_id: str, *,
                  api_key: str = BIT_API_KEY,
                  log: Optional[Callable[[str], None]] = None,
                  **kw: Any) -> int:
    """对公开页采集结果批量回填缺失 GTIN —— 改进3 的一站式入口。

    仅对 `gtin13` 为空且有 `product_id` 的项查卖家后台，原地写回 gtin13/upc。
    需要一个已登录 Walmart 卖家中心的 BitBrowser 窗口（browser_id）。
    返回回填条数。
    """
    missing = [r for r in results
               if isinstance(r, dict) and r.get("product_id") and not r.get("gtin13")]
    if not missing:
        return 0
    ids = [str(r["product_id"]) for r in missing]
    with SellerGtinFetcher(browser_id, api_key=api_key, log=log, **kw) as f:
        data = f.fetch(ids)  # 富结构（含原生 upc / wpid / 竞品价等）
    return _apply_gtin_backfill(missing, data)


def _main(argv: List[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(argv) < 2:
        print("用法: python -m app.engine.seller_gtin <browser_id> <id1> [id2 ...]")
        return 1
    browser_id, ids = argv[0], argv[1:]
    with SellerGtinFetcher(browser_id) as f:
        gtin_map = f.fetch_gtin_map(ids)
    import json
    print(json.dumps(gtin_map, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
