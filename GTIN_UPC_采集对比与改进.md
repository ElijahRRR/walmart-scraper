# GTIN / UPC 采集：你的系统 vs 工具箱 —— 差距根因与改进

> 对比对象：逆向得到的「沃尔玛工具箱.exe」选品采集 + GTIN 获取链路（证据：`沃尔玛/_re/dis/工具箱/沃尔玛工具箱.dis`、`沃尔玛/_re/dis/操作台/walmart_gtin_checker.dis`）。
> 你的系统：`app/engine/parser.py`（`_fill_identifiers`）+ `app/engine/collector.py`（仅公开页 + all-offers GraphQL）。

---

## 一、结论先行：三条差距，按影响排序

| # | 差距 | 你现在 | 工具箱 | 影响 |
|---|---|---|---|---|
| **1** | **页面原生 `gtin13` 字段没取** | 假设"无原生字段"，**只取 `product.upc` 再 `zfill(13)` 派生** gtin13 | **直接正则 `"gtin13":"(.*?)"`**，原生优先，UPC 仅作兜底 | 凡"有 gtin13 无 upc"的商品**全漏**（marketplace/跟卖商品极常见）——"不全"头号原因 |
| **2** | **取值路径太窄** | 仅 `product.upc` / `idml.upc` / `idml.specifications` 三处 | 整页 HTML 原始正则，gtin13/upc 落在哪都能抓到 | upc/gtin 落在 variant/offer/productData 等非标准位置时漏取 |
| **3** | **没用卖家后台这个权威源** | 只抓 `walmart.com/ip` 公开消费页 | 公开页拿不到时，走 **`seller.walmart.com/catalog/add-items`**（BitBrowser 复用已登录 Session）拿权威 GTIN | 公开页**根本不暴露** GTIN 的商品（大量 marketplace 商品），只有卖家后台有 —— "不全"+"不准"的终极补全手段 |

> 你 parser 里的原话注释 `# 3. GTIN13 沃尔玛无原生字段，由 12 位 UPC 补前导零派生` —— **这个前提是错的**。工具箱在**同一个** `walmart.com/ip` 页面上用 `"gtin13":"(.*?)"` 直接抓，说明该字段确实存在于 `__NEXT_DATA__` 里（通常在 `idml` 或 product/offer 节点）。

---

## 二、工具箱的真实实现（反汇编还原）

### 2.1 公开页提取（`fetch_walmart_detail`，dis 3854-4549）

正则表（`_WM_RE`，dis 3685-3687）：
```python
'"upc":"(.*?)"'
'"gtin13":"(.*?)"'
'"allImages":\[(.*?)\],'
```
取值与回退（dis 4668-4740 字节码还原）：
```python
result['UPC']    = _wm_extract(html, 'upc')      # 整页原始HTML正则首个命中
result['GTIN13'] = _wm_extract(html, 'gtin13')   # ★ 原生 gtin13 优先
if not result['GTIN13'] and result['UPC']:        # ★ 仅当原生 gtin13 为空才派生
    result['GTIN13'] = result['UPC'].zfill(13)
# UPC 与 GTIN13 各自独立保留，互不覆盖
```
要点：
- **直接整页正则**，不做 JSON 路径导航（你做的是路径导航）。代价是可能抓到变体的值，但好处是 gtin13/upc 落在哪都抓得到，**召回率高得多**。
- gtin13 是**第一公民**，upc 只是 gtin13 缺失时的兜底来源。和你完全相反。

### 2.2 卖家后台权威 GTIN（公开页拿不到时的杀手锏）

工具箱 Tab3 + 操作台 `walmart_gtin_checker` 都走这条：
```
BitBrowser 127.0.0.1:54345 /browser/open  → 取 CDP 调试地址
Playwright connect_over_cdp(debug_url)     → 接管"已登录 seller.walmart.com"的真实浏览器
goto: https://seller.walmart.com/catalog/add-items?search={id1,id2,...}  (默认5/组,最大50)
```
三层提取（同一页面，互为后备）：
1. **DOM TreeWalker**：找以 `GTIN：`/`GTIN:` 开头的文本节点，正则 `GTIN[：:]\s*(\d+)`（dis 5687）
2. **React Fiber 注入**：沿 `__reactFiber*.return` 上溯 60 层，取 `memoizedProps.itemDetails` 里的 `{itemId, gtin}`（dis 5683）—— DOM 不渲染 GTIN 时也能拿到
3. **`isbm-search-by-id` fetch hook**：覆写 `window.fetch` 拦截内部接口的 URL+headers，后续组直接打这个内部 API 拿 JSON（dis 5685）—— **最干净，直接是结构化 GTIN**

为什么必须走卖家后台：`add-items` 接口需要**登录态卖家 Session**，公开抓不了；而它返回的是 Walmart 目录里**每个 item 的权威 GTIN**，公开消费页对很多 marketplace 商品根本不带这个字段。

---

## 三、改进方案

### 改进 1（必做｜准确无风险）：取原生 gtin13 + 扩展识别符键

替换 `parser.py` 的 `_fill_identifiers` 及相关工具函数。核心变化：
- **先取页面原生 `gtin13`**（product / idml），再考虑 upc 派生
- 识别符键扩展到 `gtin13 / gtin14 / gtin / upc / ean`
- 在 **product + idml 子树**内做**受控深搜**（跳过 `variantsMap / secondaryOffers / conditionOffers / similarItems` 等易张冠李戴的子树——既提召回又保住你原本防串号的设计）
- upc ↔ gtin13 **双向互填**，各自独立落库，不再单向覆盖

```python
# ── 替换原 _fill_identifiers ──────────────────────────────────────────
_GTIN_KEYS = ("gtin13", "gtin14", "gtin")
_ID_NOISE_SUBTREES = {                       # 这些子树里的 upc/gtin 属于别的商品，跳过
    "variantsMap", "variants", "secondaryOffers", "conditionOffers",
    "similarItems", "relatedProducts", "carouselData", "moduleData",
    "additionalOffers", "sellerOffers", "offers", "recommendations",
}

def _fill_identifiers(self, r, p, idml=None):
    idml = idml if isinstance(idml, dict) else {}
    # 1) 原生 gtin13 优先（product → idml）
    gtin13 = _valid_gtin(p.get("gtin13")) or _valid_gtin(idml.get("gtin13"))
    upc    = _valid_upc(p.get("upc"))      or _valid_upc(idml.get("upc"))
    # 2) idml.specifications 里 name 含 UPC/GTIN/EAN 的条目
    if not (gtin13 and upc):
        sid = _ids_from_specs(idml.get("specifications"))
        gtin13 = gtin13 or sid.get("gtin13")
        upc    = upc    or sid.get("upc")
    # 3) 受控深搜（跳过变体/竞品子树）补漏
    if not (gtin13 and upc):
        for node in (p, idml):
            sub = _scan_identifiers(node)
            gtin13 = gtin13 or sub["gtin13"]
            upc    = upc    or sub["upc"]
            if gtin13 and upc:
                break
    # 4) 双向互填（各自独立保留）
    if gtin13 and not upc:
        upc = _gtin13_to_upc(gtin13)
    if upc and not gtin13:
        gtin13 = _to_gtin13(upc)
    r["upc"] = upc
    r["gtin13"] = gtin13


def _valid_gtin(val):
    """gtin/gtin13/gtin14 校验：纯数字且长度 8/12/13/14，全零无效。"""
    if val is None:
        return None
    d = re.sub(r"\D", "", str(val))
    if len(d) not in (8, 12, 13, 14):
        return None
    if set(d) == {"0"}:
        return None
    return d


def _ids_from_specs(specs):
    """从 specifications 同时取 gtin13 与 upc（name 含 GTIN/UPC/EAN）。"""
    out = {"gtin13": None, "upc": None}
    if not isinstance(specs, list):
        return out
    for s in specs:
        if not isinstance(s, dict):
            continue
        name = (s.get("name") or "").lower()
        v = s.get("value")
        if ("gtin" in name or "ean" in name) and not out["gtin13"]:
            g = _valid_gtin(v)
            if g:
                out["gtin13"] = g if len(g) >= 13 else _to_gtin13(g)
        if "upc" in name and not out["upc"]:
            u = _valid_upc(v)
            if u:
                out["upc"] = u
    return out


def _scan_identifiers(node, _depth=0):
    """在 product/idml 子树受控深搜 gtin*/upc，跳过变体/竞品子树防串号。"""
    found = {"gtin13": None, "upc": None}
    if _depth > 6 or not isinstance(node, (dict, list)):
        return found
    if isinstance(node, dict):
        for gk in _GTIN_KEYS:
            if not found["gtin13"]:
                g = _valid_gtin(node.get(gk))
                if g:
                    found["gtin13"] = g if len(g) >= 13 else _to_gtin13(g)
        if not found["upc"]:
            found["upc"] = _valid_upc(node.get("upc"))
        for k, v in node.items():
            if k in _ID_NOISE_SUBTREES:
                continue
            if found["gtin13"] and found["upc"]:
                break
            sub = _scan_identifiers(v, _depth + 1)
            found["gtin13"] = found["gtin13"] or sub["gtin13"]
            found["upc"]    = found["upc"]    or sub["upc"]
    elif isinstance(node, list):
        for v in node[:20]:                       # 列表只看前 20 项，避免误入大数组
            if found["gtin13"] and found["upc"]:
                break
            sub = _scan_identifiers(v, _depth + 1)
            found["gtin13"] = found["gtin13"] or sub["gtin13"]
            found["upc"]    = found["upc"]    or sub["upc"]
    return found


def _gtin13_to_upc(gtin13):
    """GTIN-13 → UPC-A：仅前导 0 的 GTIN-13/14 才有对应 UPC-A；真 EAN-13 返回 None。"""
    if not gtin13:
        return None
    d = re.sub(r"\D", "", str(gtin13))
    if len(d) == 12:
        return d
    if len(d) == 13 and d[0] == "0":
        return d[1:]
    if len(d) == 14 and d[:2] == "00":
        return d[2:]
    return None
```
（`_valid_upc` / `_to_gtin13` 沿用你现有实现即可。）

**风险控制**：深搜显式跳过 `variantsMap/secondaryOffers/...`，正是你担心的"抓到变体/竞品 UPC"的来源。这样既拿回召回率，又不破坏你原来的防张冠李戴设计。若想更保守，可只保留步骤 1+2，去掉步骤 3。

### 改进 2（验证）：用真实页面对账

抓 1 份「公开页有 gtin13 无 upc」的 marketplace 商品 HTML，确认改造前后：
- 改造前：`upc=None, gtin13=None`（全漏）
- 改造后：`gtin13=<原生值>, upc=<去前导0派生 或 None>`

可在 `tests/` 加一条断言：`__NEXT_DATA__` 含 `"gtin13"` 时，`result["gtin13"]` 必须非空。

### 改进 3（补全终极方案｜需卖家账号）：接卖家后台

公开页彻底没有 GTIN 的商品，唯一权威源是 `seller.walmart.com/catalog/add-items?search={ids}`。两种落地：

- **A. 复刻内部接口（推荐，无需浏览器）**：从浏览器网络面板抓一次 `isbm-search-by-id` 的请求（URL + headers + 登录 Cookie），用 `requests`/`curl_cffi` 直接打，返回 JSON 里就有每个 itemId 的 gtin。比 DOM 抓取干净、快、稳。Cookie 失效需定期从已登录会话刷新。
- **B. BitBrowser CDP（同工具箱）**：`POST 127.0.0.1:54345/browser/open` 拿 CDP 地址 → Playwright `connect_over_cdp` 接管已登录窗口 → goto add-items → 注入 React Fiber JS（dis 5683）取 `itemDetails.gtin`。重，但不碰接口签名。

> 注意：这条是"跟卖选品/补全 GTIN"的核心能力。如果你的业务是为 ERP 跟卖供数据，公开页 + 卖家后台**双源合并**（公开页拿价格/库存/卖家，卖家后台拿权威 GTIN）才是工具箱能做到"全且准"的真正原因。

---

## 三点五、改进3 实测验证（2026-06-07，真实 BitBrowser + 已登录卖家号 J024朵世存）

模块 `app/engine/seller_gtin.py` 已在本机真实跑通：
- BitBrowser 窗口（本地账号），browser_id=`<已脱敏>`，本地 API `127.0.0.1:54345` + `x-api-key`。
- 实测 `42379869`(Lasko) / `20052121912`(草编包) → **2/2 取到**，且 **FIBER_JS 返回的是金矿**（远超 GTIN）：
  ```
  42379869: gtin=00046013460691 upc=046013460691 brand=Lasko buybox=29.97 competitor=Target/$49.99 wfs=True wpid=1IOY8GNC0VVI gtins=[3个变体]
  20052121912: gtin=00660317094101 upc=660317094101 brand=Generic buybox=27.99
  ```
  → 卖家后台不仅给**权威 gtin+原生 upc**，还白送 **跟卖竞品价(competitor)/BuyBox价/WFS支持/变体全GTIN/WPID** —— 选品决策直接能用。

**实测踩到并已修复的 3 个坑（都写进了模块）：**
1. **冷启动竞态**：刚 CDP 接管的页面首次导航，结果约 t+10s 才渲染；`networkidle` 在该 SPA 几乎永不触发。→ 改用 `domcontentloaded` + 轮询 GTIN 文本(≤25s) + reload 重试一次。
2. **新建标签无指纹**：Playwright `new_page()` 建的标签缺 BitBrowser 指纹上下文，冷启动渲染不出。→ `_get_page` 改为**复用 BitBrowser 已有真实标签**（排除其 console 控制台标签），不新建。
3. **无效 id 毒化整组 + 张冠李戴**：一个无效/下架 id 混进逗号搜索，会让**整组**无结果；且无效 id 单独搜会退化成关键词搜索、显示**无关推荐商品**。→ ① 组内有缺失且组>1 时**逐个单独重试**隔离坏 id；② FIBER 结果**严格按 itemId 过滤到本次请求的 id**，丢弃无关项；③ **删除 DOM 位置兜底**（DOM 无 itemId，位置错位必然错配 GTIN——正是"不准"的根源）。

最终实测 `['42379869','20052121912','5689919']`(末位无效) → 精确 **2/3，无泄漏、无错配**，无效 id 如实留空。

## 四、一句话总结

工具箱"GTIN 全且准"靠两点，你都缺：① **公开页直接取原生 `gtin13`**（你错误地以为没有这个字段，只从 upc 派生）；② **公开页拿不到时回落到登录态卖家后台**（`add-items` / `isbm-search-by-id`）取权威 GTIN。改进 1 是零风险立刻能补的召回，改进 3 是彻底补全的权威源。
