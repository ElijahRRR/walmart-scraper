# 沃尔玛采集服务 — 对抗审查报告

> ✅ 全部已修复，见 FIXES.md

> 审查范围：FastAPI 后端（采集引擎 + 服务层）+ Nuxt3 前端
> 方法：只读审查 + grep/读源码核实；经 skeptic 对抗验证后保留确认项，附误报清单。
> 仓库：`~/Projects/沃尔玛采集`

---

## 执行摘要

| 指标 | 数量 |
|------|------|
| 确认问题总数 | 28 |
| P0 | 0 |
| P1 | 5 |
| P2 | 15 |
| P3 | 8 |
| 已推翻/误报 | 6 |

维度分布：后端逻辑 7、并发与代理 5、安全 6、API 契约 6、前端 4、采集数据 5（部分跨维度，按主维度计）。

### 最该先修的 3 条

1. **真实代理密钥进 git 历史且会被运行时消费**（P1，安全）— `.env.example:2` 含真实 `PROXY_API_KEY`，且 `config.py:15-17` 在无 `.env` 时 fallback 读取 `.env.example`。立即吊销密钥 + 清史 + 改占位符。
2. **partial 状态无条件覆写 ok 数据，关键字段被 NULL 抹掉**（P1，数据完整性）— `runner.py:227-266` 的 upsert 对 upc/gtin13/title 等用裸 `excluded.xxx`，瞬时解析异常会用 NULL 覆盖已入库的合法条码/标题，不可恢复。改用 COALESCE 或对 partial 跳过。
3. **pace() 无锁，多任务共享 ProxyPool 时限速完全失效**（P1，并发）— `proxy.py:163-170` 节速逻辑无锁 + `runner.py:491-506` 所有 collector 共享同一 pool，并发请求可同时绕过 3-7s 间隔，加速触发沃尔玛封控。加锁或改单 lane 串行消费。

---

## P1 — 安全/数据错（5 条）

### P1-1 真实 PROXY_API_KEY 进入 git 历史，且无 .env 时运行时会消费它
- **维度**：安全
- **位置**：`.env.example:2`（commit 6c3a510）；`app/config.py:15-17`
- **影响**：任何有仓库读权限者（含 git 历史）可拿到 cliproxy 账户 key `your_cliproxy_key_here`，消耗代理配额或绕过计费。加重项：`.gitignore` 未排除 `.env.example`，且 `config.py` 显式 fallback —— 克隆仓库直接启动（无 `.env`）会**无感知地用泄漏密钥认证**，无任何告警。
- **建议**：① 立即吊销/重置该 cliproxy key；② `.env.example` 值改为占位符（如 `PROXY_API_KEY=your_cliproxy_key_here`）；③ 用 `git filter-repo`/BFG 清除历史中的真实值；④ 评估移除 `config.py` 对 `.env.example` 的 fallback，缺配置时应报错而非静默使用示例密钥。

### P1-2 partial 状态结果无条件覆写 ok 数据，有效字段被 NULL 覆盖
- **维度**：采集数据 / 数据完整性
- **位置**：`app/service/runner.py:227-266`（ON CONFLICT DO UPDATE SET）；`app/engine/parser.py:81-94`；`runner.py:170`
- **影响**：反复采集时，瞬时解析异常（`_status=partial`）会用 NULL 覆盖已存入的有效商品数据（upc/gtin13/title/brand/image_url/long_description/images），造成不可恢复的数据完整性损失（旧值未存入任何 `prev_` 字段）。证据链：parser 的 `_fill_*` 链整体包在 try/except，中途抛异常即 `_status=partial` 且字段为 None；`save_product` 对 partial 不跳过（注释明写"允许写入"）；upsert 块对所有可选字段是裸 `excluded.xxx` 赋值，零 COALESCE 保护。
- **建议**：关键字段改用 `COALESCE(excluded.upc, products.upc)`（仅新值非空才覆盖）；或在 `save_product` 入口判断：若新 `_status=partial` 且旧 `_status=ok` 则跳过该次更新。

### P1-3 pace() 无锁：多任务共享同一 ProxyPool 时限速完全失效
- **维度**：并发与代理
- **位置**：`app/engine/proxy.py:163-170`（pace）；`app/service/runner.py:491-506`（_make_collector）
- **影响**：`pace()` 读写 `_last_req_at`/`_uses` 时不持 `self._lock`（同类的 `current()`/`rotate()`/`get_status()` 都加锁，唯独 pace 例外）；而 `_make_collector()` 让所有 collector 共享 `_lanes[0]._pool` 单实例。并发的两个 HTTP 请求（如两次 `POST /collect/ids`）各自后台线程可同时读到相同 `_last_req_at`，各自 sleep 后**同时发请求**，3-7s 节速归零，沃尔玛侧看到瞬时高频流量、加速封控；`_uses += 1` 非原子导致少计，`/proxy/status` 的 uses 字段失真。
- **建议**：方案一，给 `pace()` 加 `with self._lock`（`_last_req_at`/`_uses` 读写原子化）。方案二（架构）：runner 不让多任务共享同一 ProxyPool，每 lane 对应一个任务队列串行消费。
- **备注**：原报告称影响 `proxy_log.yield.count` 略有夸大——该字段来源于 `Lane._ip_product_count` 而非 `_uses`；真正受影响的是 `ProxyPool.stats["uses_on_current"]` → `/proxy/status` 的 uses 显示。

### P1-4 默认 API Key "dev-key-change-me" 硬编码于前后端，且实际 .env 仍在用
- **维度**：安全
- **位置**：`app/config.py:91`；`frontend/composables/useApiKey.ts:13`
- **影响**：后端 `API_KEY` 默认值与前端 `DEFAULT_KEY` 一致，且 `useApi.ts:50` 将其放入每个请求的 `X-API-Key`（浏览器 Network Tab 可见）。**实锤**：实际部署的 `.env`（非 `.env.example`）中 `API_KEY=dev-key-change-me`——运维直接复制示例未改。任何知道该公开默认值的人即可调用全部受保护端点（提交采集任务、轮换代理 IP、读取数据库）。该值也已提交进 `.env.example:13`（git 可见）。
- **建议**：后端启动检测 `API_KEY` 是否为默认值，若是则打 ERROR 告警并拒绝启动（或拒绝非 localhost 请求）；前端去除 `DEFAULT_KEY` 硬编码，改为"请先配置 API Key"提示。
- **备注**：定 P1 而非 P0，因服务按设计部署在内网（CORS 仅开 :3000）；但若 8900 端口有任何网络可达路径，该 key 即零防御。

### P1-5 ResultViewer 快速切换任务时数据错位：loading guard 阻断新任务加载
- **维度**：前端
- **位置**：`frontend/components/ResultViewer.vue:114` + watch `230-248`；`frontend/composables/useSelectedTask.ts:25-27`
- **影响**：静默的错误数据展示，用户无感知。两个具体故障：① 任务 A 的 fetch 在飞行中（`productsLoading=true`）时切到任务 B，watch 重置数据但**未重置 `productsLoading`**，`loadProducts(true)` 入口第114行 `if (productsLoading.value) return` 直接返回，任务 B 数据从未发出请求，`hasMore=false`，只显示"暂无数据"。② 任务 A 的 in-flight fetch 完成后仍 `productsItems.value.push(...)`，而面板标题已显示任务 B，造成"标题 B、内容 A"错位。`useSelectedTask.openTask` 无防抖/取消，快速切换 100% 触发。
- **建议**：watch 回调中在 `loadProducts(true)` 前显式置 `productsLoading.value = false`（及 `listingsLoading`）；或改用 AbortController 取消前一个 fetch。

---

## P2 — 数据错/功能障碍（15 条）

### 后端逻辑

#### P2-1 封控时 Lane 状态机从未被驱动 / Lane 与 runner 完全断路
- **位置**：`app/service/runner.py:491-507, 556-562`；`app/service/lanes.py:130-151`
- **影响**：`_make_collector()` 只提取 `_lanes[0]._pool`，丢弃 `Lane` 引用；全项目 grep 确认 `notify_blocked`/`notify_product_saved`/`run_work` 只在 `lanes.py` 定义、仅被测试调用，runner 中零调用。封控后任务以 `status=done result_count=0` 正常完成，`lane.state` 恒为 IDLE、`total_products` 恒 0，运维无报警；`/proxy/status` 的 state/total_products 全部失真，无法据此做人工换 IP 决策。`give_up` 不在 `BLOCKED_STATUSES`，软封路径也不触发。测试 `test_m2_lanes.py:537` 注释自承"真实实现里 runner 会调 notify_blocked"——证明从未接通。
- **建议**：`_collect_with_retry` 检测到 `give_up` 且 `collector.last_block` 非空时调 `lane.notify_blocked(...)`，并在封控分支检查 `lane.state==BLOCKED` 后 `update_status(task_id,"blocked")`；`save_product` 成功后调 `lane.notify_product_saved()`。或在 `_make_collector` 同时返回 lane 引用。

#### P2-2 config.RETRY_MAX 环境变量无效——runner 用独立硬编码常量
- **位置**：`app/service/runner.py:35`；`app/config.py:107`
- **影响**：配置欺骗性。`runner.py:35` 定义模块级 `RETRY_MAX=2`，第433/444/476 行全引用此固定值；全文唯一的 `from app import config` 只读 `WEBHOOK_URL`，从不读 `config.RETRY_MAX`。运维设置 `.env` 的 `RETRY_MAX=0`（封控立即放弃）或 `=5`（增韧性）均被静默忽略，实际始终重试 2 次。
- **建议**：删除 runner 模块级常量，改 `from app.config import RETRY_MAX`。

#### P2-3 断点续采 result_count 虚增——give_up 项被计为成功
- **位置**：`app/service/runner.py:549, 565`；`app/service/tasks.py:135-160`
- **影响**：`mark_item_done` 在 `save_product` 返回 False（give_up/empty_page 等）后仍无条件写入 `completed_ids`；续采时 `result_count = len(ids) - len(remaining) = len(completed)`，把历史失败项也算进初始成功数。反例：首轮 100 ids（80 入库、20 give_up），续采 `result_count=100`，webhook/UI 报告 saved=100，实际入库 80。统计失真但不影响入库正确性。
- **建议**：`mark_item_done` 只在 `save_product` 返回 True 后调用；或新增 `saved_ids`/`done_ids` 分别追踪，续采初始化只把 `saved_ids` 计入 `result_count`。

#### P2-4 变动检测与 upsert 非原子——并发采集同一商品产生重复 product_changes 行
- **位置**：`app/service/runner.py:69-140, 190-299`
- **影响**：`_detect_changes`（读旧值，一个事务）、`save_product` upsert（第二个独立事务）、`_write_change_record`（第三个连接）三步跨三事务，read-detect-write 非原子。两个并发的独立任务（如两次 `POST /collect/ids`）含相同 product_id 时，可形成 A 读 old=10 → B 读 old=10 → A upsert+写变动 → B upsert+再写一条变动（old 仍 10）的竞态，`product_changes` 出现重复行，`/products/changes` 返回虚假变动记录。
- **建议**：将 detect 与 upsert + 变动写入合并进单连接 `BEGIN IMMEDIATE` 事务，再 COMMIT；或用 SQLite 触发器在 DB 层做变动检测。
- **备注**：修正原表述——同一文件内重复 ID 不触发（`/collect/import` type=ids 单线程串行）；触发条件是两个并发独立任务。

#### P2-5 blocked/failed 退出时不触发 webhook
- **位置**：`app/service/runner.py:555-559, 574-577`（及 `run_keyword:650-653`、`run_seller:708-711`）
- **影响**：三条非正常退出路径（封控/异常）均 `update_status` 后直接 `return task_id`，跳过 `_maybe_fire_webhook`（只在正常完成路径调用）。配置 `WEBHOOK_URL` 的调用方在任务封控/失败时收不到任何通知，须轮询 `/tasks/{id}`，自动化场景下任务静默丢失。
- **建议**：在封控和异常退出的 `return` 前加 `_maybe_fire_webhook(task_id, webhook_url)`。

#### P2-6 LanePool 多 lane 架构不可用——submit_ids 不存在，_make_collector 硬绑 lane 0
- **位置**：`app/service/lanes.py:12`（docstring）；`app/service/runner.py:502`
- **影响**：`lanes.py:12` docstring 写 `pool.submit_ids(ids)`，但 `LanePool` 类无此方法（过时/未实现 API）；`_make_collector()` 硬编码 `_lanes[0]._pool`，runner 中无任何代码 `get_idle_lane()` 或分发到其他 lane。`LANES>1` 时其余 lane 的 IP 被初始化并展示于 `/proxy/status`，但从不被采集消费——运维以为多 IP 并发，实际只有单 IP（lane 0）。
- **建议**：若多 lane 并发为预期功能，实现 `LanePool.submit_ids` 或在 runner 层分配任务到各 lane；若暂不支持，`config.LANES` 强制 =1 并在启动时 warn。

### 并发与代理

#### P2-7 多 Lane 共享同一 STATE_FILE，IP 跨 Lane 污染
- **位置**：`app/engine/proxy.py:26`（STATE_FILE）、`66-111`（_load_state/_save_state/current）；`app/service/lanes.py:91-97`
- **影响**：`STATE_FILE` 是模块级固定单路径，所有 `ProxyPool` 实例读写同一 `proxy_state.json`、互相覆盖。`LANES>1` 时冷启动各 lane 从同一文件读到相同 IP，lane 隔离失效——所有 lane 收敛到同一 IP，并发度加倍但 IP 只有一个，封控风险与单 lane 相同，且内存中各 lane `_born_at` 不同但实际 IP 相同（状态不一致）。
- **建议**：`STATE_FILE` 路径含 lane_id（如 `proxy_state_{lane_id}.json`），在 `ProxyPool.__init__` 按参数设置，或由 Lane 创建时传入。
- **备注**：默认 `LANES=1` 不触发；当前 runner 只走 lane 0，但 lane 基础设施的 IP 隔离本身已损坏。

#### P2-8 Lane.resume() 在持有 Lane._lock 期间执行 15s 阻塞睡眠
- **位置**：`app/service/lanes.py:161-189`（resume）；`app/engine/proxy.py:114-123`（rotate）
- **影响**：`resume()` 在 `with self._lock` 内调 `self._pool.rotate()`，而 `rotate()` 在释放 `ProxyPool._lock` 后于第122行 `time.sleep(BLOCK_BACKOFF=15.0)`，此时 `rotate()` 尚未返回，`Lane._lock` 仍被持有。整个 15s 内任何其他线程调 `get_status()`/`notify_blocked()`/`notify_product_saved()` 全部阻塞。手动换 IP 期间 `/proxy/status` 查询该 lane 卡 15s。
- **建议**：将 `rotate()` 的 `sleep` 移到锁外（用局部变量保存新 proxy，释放锁后再 sleep），或将 `resume()` 中 `rotate()` 调用移出锁。

### 安全

#### P2-9 proxy_state.json 含完整代理账密（user:password@ip:port）
- **位置**：`proxy_state.json:1`（项目根）；`app/engine/proxy.py:84-86`
- **影响**：`_save_state()` 将完整 `self._proxy`（`http://user:password@ip:port`）明文落盘，实测文件权限 0644（世界可读）。若服务器被入侵、目录被 tar/rsync/云同步意外打包、或同机用户读取，可直接获得完整代理账密。
- **缓解（已核实）**：`.gitignore` 已排除该文件（git 泄露路径关闭）；`/proxy/status` 经 `split("@")[-1]` 只返回 `ip:port`（网络泄露路径关闭）。
- **建议**：`chmod 600`；定期轮换凭据；评估只在内存持有凭据、仅 `born_at` 落盘，或落盘前加密/拆分。

#### P2-10 文件上传无大小限制 + 无 MIME 校验，openpyxl 解析不受信内容
- **位置**：`app/api.py:269-271`；`app/service/importer.py:36-46, 68`
- **影响**：`importer.py:36` 仅 `name.endswith(".xlsx")` 判断，无 magic bytes/MIME 校验；`api.py:269` `await file.read()` 无大小上限，整文件读入内存后 `load_workbook(...)` 解析——zip bomb（OOXML 高展开比）可在此阶段耗内存致 DoS。`requirements.txt` 仅 `openpyxl>=3.1` 不固定上限。
- **缓解**：端点有 API Key 鉴权（须先获有效 key）；`read_only=True` 关闭公式引擎。"代码执行"属推测（无当前可利用 CVE），故降为 P2。
- **建议**：① `len(content) > 10MB` 则 400；② 校验 magic bytes（`PK\x03\x04`）与扩展名一致；③ 保持 openpyxl 最新；④ 保留 `read_only=True`。

### API 契约

#### P2-11 GET /tasks 响应缺 total 字段，前端分页永久失效
- **位置**：`app/api.py:318` vs `frontend/components/TaskList.vue:60`
- **影响**：后端只返回 `{items, count, limit, offset}`，无 `total`。前端 `total.value = res.total ?? res.items.length` → 永远走右侧 = 本次条数（≤50）；`hasMore = tasks.length < total` 恒为 false，"加载更多"按钮永不出现。即使库内 200 条任务，前端也只能看到最新 50 条，无法翻页。
- **建议**：后端 `list_tasks_api` 加 `SELECT COUNT(*) FROM tasks` 返回 `total`；或前端改用 `count < limit` 推算 `hasMore`。

#### P2-12 GET /proxy/status lane_id 类型不一致，手动换 IP 输入必 422
- **位置**：`app/service/lanes.py:285-295` vs `frontend/components/ProxyPanel.vue:220, 169, 308`
- **影响**：后端返回 `lane_id` 为 Python int（JSON number），前端 `interface ProxyLane { lane_id: string }` 声明错误（运行时实为 number）。卡片快捷换 IP 路径正常（number 正确传给 `int` 字段）；但手动输入框 placeholder 提示"例如 lane_0"，用户按提示输入 `lane_0`（字符串）必定 422。TS 类型声明误导长期存在。
- **建议**：前端 `ProxyLane`/`rotatingLane` 改 number；`manualLaneId` 改 number input 或提交时 `parseInt` 校验、修正 placeholder。

#### P2-13 GET /products|/listings next_cursor 永不为 null，末页 hasMore 无法归零
- **位置**：`app/api.py:356, 387, 418` vs `frontend/components/ResultViewer.vue:98-99, 132, 162`
- **影响**：后端 `next_cursor = items[-1]["id"] if items else after_id`，空 items 时返回 `after_id`（int，默认 0），从不返回 null。前端 `res.next_cursor ?? null`——`??` 不处理 `0`，cursor 被赋值整数 → `hasMore = (cursor !== null)` 恒 true。空表场景"加载更多"始终可点，每点追加 0 条；有数据到末页时同样 cursor 非 null → 无限可点、持续无用请求。
- **建议**：后端末页（items 为空）返回 `next_cursor: null`；或前端改判 `res.count < PAGE_SIZE` 推断末页。
- **备注**：原报告"重复追加重复数据"略夸大——`push(...[])` 不追加重复，但无限请求成立。

### 采集数据

#### P2-14 collect_by_seller 对第 1 页发起两次 HTTP 请求
- **位置**：`app/engine/collector.py:199-201`
- **影响**：第199行 `_get(page=1)` 仅取卖家信息（列表项被丢弃），第201行 `_paged_listing` 内 `range(1, max_pages+1)` 又从 page=1 抓一次。两次都是真实 HTTP 请求 + 各一次 `pace()`，固定多消耗 1 次 `_uses`（IP 寿命）和 3-7s 等待，加速封控风险；对仅 1 页的小卖家尤为浪费。不影响数据正确性（seen 去重）。
- **建议**：将首页 `first_html` 复用传入 `_paged_listing`，或在 `_paged_listing` 内提取卖家信息，避免重复抓取（让翻页从 page=2 开始）。

#### P2-15 in_stock 变动检测是永远不触发的死代码
- **位置**：`app/service/runner.py:84, 103, 187`；`app/models.py:108-110`
- **影响**：`old_in_stock`/`new_in_stock` 被硬编码为 None（注释自承"products 表无独立 in_stock 字段，占位"），第103行 `if old is not None and new is not None and old != new` 恒 False，`changed_fields` 永不含 `in_stock`，缺货/上架监控名存实亡。`product_changes.old_in_stock/new_in_stock` 列始终 NULL；`prev_in_stock` upsert 时自赋值 NULL→NULL。同时掩盖了真实库存信号（`availabilityStatus` 存在于 ship_info 但未映射独立列）。
- **建议**：从 parser 的 `ship_info.availability_status`（IN_STOCK/OUT_OF_STOCK/UNAVAILABLE）派生 `in_stock` 整数，传入 `_detect_changes` 和 `save_product`；products 表补 `in_stock` 列并在 detect 中查询替代 None 占位。

#### P2-16 importer._parse_text 不处理 tab 分隔符，TSV 解析为含 tab 的无效 token
- **位置**：`app/service/importer.py:53, 57`
- **影响**：`_parse_text` 只 `strip()` + 检测逗号 `split(",", 1)`，不处理行内 tab。TSV 行 `'1234567890\t商品名'` 无逗号 → 整串含 `\t` 作为 token。下游 `collect_detail` 将其嵌入 URL（`.../ip/1234567890\t商品名`），非法 URL 致请求失败，静默返回 `give_up`，无"文件格式有问题"提示。从 Excel 另存为 .txt/.csv（Windows 默认 Tab 分隔）的文件全部 ID 解析失败。
- **建议**：`if '\t' in line: line = line.split('\t', 1)[0].strip()`，与逗号判断并列；或统一用 `csv.reader` 解析。

---

## P3 — 小改进（8 条）

#### P3-1 任务状态机无单向转换守卫——done/blocked 可被覆写回 running
- **位置**：`app/service/tasks.py:70-88`
- **影响**：`update_status` 只校验 `status in VALID_STATUSES`，无当前态检查，与第3-7行"单向"注释矛盾。任何调用方可对 `done`/`blocked`/`failed` 任务调 `update_status("running")`，静默覆盖终态并将 `error_msg` 置 None。属潜在缺陷——当前唯一合法的 running 重置是 `runner.py:539` 的显式续采路径（设计内）；原报告称 `/collect/import` 重复提交同 task_id 的场景不存在（每次 `create_task` 产生新 rowid）。
- **建议**：`update_status` 先 SELECT 当前 status，拒绝 `done→running`/`done→pending` 等后退转换。

#### P3-2 API Key 普通字符串相等比较，存在计时侧信道
- **位置**：`app/api.py:92`（require_api_key）
- **影响**：用 `x_api_key != config.API_KEY` 明文比较而非 `hmac.compare_digest`，违反恒时比较最佳实践。但部署模型为内网/本机（CORS 仅 localhost），HTTP+ASGI 路径噪声（百 μs 级）远高于字节比较耗时（ns 级），实际计时攻击难实施；若仍用默认 key 直接猜值即可。
- **建议**：`import hmac; if not x_api_key or not hmac.compare_digest(x_api_key, config.API_KEY): raise HTTPException(401, ...)`。

#### P3-3 /openapi.json、/docs、/redoc 未鉴权暴露
- **位置**：`app/api.py:58-63`
- **影响**：FastAPI 构造未传 `docs_url=None`/`redoc_url=None`/`openapi_url=None`，三端点默认暴露且 `require_api_key`（路由级依赖）无法覆盖内部 schema 路由。任何人无需 X-API-Key 即可读全部端点/参数/约束，降低攻击门槛。内网定位故定 P3。
- **建议**：构造时传 `docs_url=None, redoc_url=None, openapi_url=None`，或将 `openapi_url` 置于鉴权保护下。
- **备注**：原报告以 `api.py:326` 的 404 message 为"证据"有误导——真正泄漏路径是 `/openapi.json`，与该 404 文案无关。

#### P3-4 collect_by_seller 重复抓取第一页（资源浪费视角）
- **位置**：`app/engine/collector.py:199-201`
- **影响**：与 P2-14 同源，从"每次多 1 次 `_uses` + 3-7s 节速"的资源浪费角度独立计列。不影响数据正确性。
- **建议**：同 P2-14。

#### P3-5 _paged_listing 同页内重复 product_id 不去重
- **位置**：`app/engine/collector.py:162-166`；`app/engine/parser.py:412-421, 547`
- **影响**：parser 将自然位与赞助位 `+=` 拼接无去重，`res["items"]` 可含同一 product_id 两次；列表推导中 `seen` 在本次推导期间不变，两条重复项都通过 `not in seen` 进入 `fresh`（`seen.add` 在推导后才执行，无法回溯移除）。沃尔玛搜索赞助/自然结果重叠常见，导致同商品被二次 `collect_detail`（多一次网络请求 + 一次 upsert），upsert 幂等无数据错误。
- **建议**：列表推导改显式循环，迭代内即时 `seen.add`；或在 `_collect_listing_items`/`parse_listing` 层去重。

#### P3-6 POST /proxy/rotate 响应 lane_status 类型不匹配，UI 显示 [object Object]
- **位置**：`app/api.py:515-519` vs `frontend/components/ProxyPanel.vue:234, 311`
- **影响**：后端 `get_lane_status()` 返回 9 字段 dict 直接塞进 `lane_status`，前端声明 `lane_status: string` 并模板插值，运行时隐式 `toString()` → 用户看到"状态：[object Object]"。仅提示文案错误，不涉数据/安全。
- **建议**：后端改 `"lane_status": status["state"] if status else "unknown"`；或前端 `resp.lane_status?.state ?? resp.lane_status`。

#### P3-7 前端 SSR 模块级单例 + 类型缺口（一组 P3）
- **位置**：`frontend/composables/useApiKey.ts:16`、`useSelectedTask.ts:22, 11-19`；`frontend/composables/useApi.ts:132-137`；`frontend/components/TaskList.vue:136-143`
- **影响**：
  - **模块级单例**：`_apiKey`/`_selectedTask` 模块顶层 ref，SSR（`node-server` preset）跨请求共享，违反 Nuxt3 最佳实践，存在 hydration mismatch（服务端 = DEFAULT_KEY，客户端 = localStorage 值）。实际数据泄漏不成立（SSR 侧 `_apiKey` 确定性读 DEFAULT_KEY，`_selectedTask` 服务端永不写）。
  - **download() 无 SSR 保护**：`useApi.ts` 直接 `document.createElement('a')` 无 `import.meta.client` 守卫；当前调用均在 click handler（SSR 不执行）故不崩溃，属防御性缺失。
  - **typeLabel 键错位**：`TaskList.vue` 映射键用 `ids`/`import`，后端实际任务类型是 `detail`（`tasks.py:31`），detail 任务类型列显示原始英文，本地化失效。
  - **TaskItem 接口缺字段**：缺 `error_msg`/`updated_at`/`params`，后端 `SELECT *` 实际返回；strict TS 下访问会编译报错，blocked/failed 的 `error_msg` 无法展示。
- **建议**：单例改 `useState('apiKey', () => '')` / `useState('selectedTask', () => null)`；`download()` 顶部加 `if (!import.meta.client) return/throw`；typeLabel 键改 `detail: 'ID采集'` 删无效键；`TaskItem` 补 `error_msg?`/`updated_at?`/`params?`。

#### P3-8 ProxyPanel/TaskList 计时器与轮询前端缺陷（一组 P2/P3，UX 视角合并列示）
> 注：以下两条原判 P2，因均为前端 UX 降级、无数据/安全影响，按维度归并于此组末尾补充（严重度以各条标注为准）。

- **ProxyPanel setTimeout 未清除（P2）** — `ProxyPanel.vue:323`：`setTimeout(() => rotateResult.value = null, 5000)` 不存句柄，`onUnmounted` 只清 interval 不清 timeout。卸载时回调写已卸载组件（Vue 警告）；5s 内连续换 IP 时旧计时器提前清除新结果，UI 看似无反馈。建议用 `let timer` 记句柄，新调用前 `clearTimeout` 再重设，`onUnmounted` 也 `clearTimeout`。
- **TaskList 轮询始终重置到第一页（P2）** — `TaskList.vue:72-74`：轮询无条件 `fetchTasks(true)`，`reset=true` 清空列表+offset。用户"加载更多"后 3-60s 内视图被静默重置回首页 50 条，任务量 >50 时尤其明显。建议轮询时不 reset，仅刷新已加载范围，或加标志位仅在未展开"加载更多"时 reset。

---

## 已推翻 / 误报清单

1. **mark_item_done 读-改-写非原子（并发续采丢 ID）** — TOCTOU 模式存在，但 `mark_item_done` 仅在 `run_ids` 串行循环单线程调用，`resume_task_id` 从未被任何 API 端点传入，`/proxy/rotate` 只换 IP 不重启任务；并发前提在现有代码中不可达，属潜在隐患非可重现 bug。

2. **/export/{kind} 路径参数 SQL 注入** — `api.py:439-440` 白名单 `if kind not in ("products","listings"): raise` 与 f-string SQL 同步顺序执行，raise 触发即 400 返回，注入路径不可达；原文措辞是"若将来移除白名单"，属假设性改动。

3. **CORS allow_credentials=True + allow_headers=["*"]** — Starlette 0.52.1 已正确处理该组合：`allow_all_headers + preflight_explicit_allow_origin` 下不写死 `*`，而是镜像回客户端 `Access-Control-Request-Headers`；且 origins 严格锁三个 localhost，响应永不出现违规组合，当前行为合规。

4. **collect_import 不限并发：大文件触发线程风暴绕过节速** — Starlette `BackgroundTasks.__call__` 是 `for task: await task()` 严格串行；1000 行文件只产生 1000 个串行任务，逐个执行，pace 串行生效，不存在并发爆发，核心假设与框架实现相悖。

5. **rotate() 锁外读 self._proxy：auto_rotate=True 并发返回错误 IP** — 每 Lane 持独立 ProxyPool + 所有 Lane `auto_rotate=False`（lanes.py:96）+ Lane 内串行执行；`rotate()` 唯一调用方 `resume()` 随即用 `get_status()` 取真实 IP 覆盖其返回值。竞争前提不可达，锁外读无运行时影响。

6. **_to_float 对负数字符串丢失符号** — `_to_float` 的入参始终是 JSON `.price` 数字字段（int/float），主商品价格根本不经过它，`price_string` 只原样存字符串从不传入；负价场景下值已是 float 走 `isinstance` 分支正确保号，原报告假设的"负价格字符串"路径在代码中不存在。