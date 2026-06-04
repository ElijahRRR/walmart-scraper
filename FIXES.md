# 沃尔玛采集服务 — 修复记录（28 项 Finding）

> 对应审查报告：ADVERSARIAL_REVIEW.md
> 验证环境：Python 3.x + pytest 225 tests pass；npm run build（Nuxt）通过；python3 test_parser.py 全过。

---

## P1 — 安全/数据错（5 条）

### P1-1 真实 PROXY_API_KEY 进入 git 历史，且无 .env 时运行时会消费它
- **文件:行**：`app/config.py:14-38`；`.env.example:2`
- **修复**：
  - `.env.example` 中 `PROXY_API_KEY` 改为占位符 `your_cliproxy_key_here`（防止新克隆直接用泄露值）。
  - `config.py` 的 `_load_dotenv()` 只加载 `.env`，不再 fallback 到 `.env.example`——缺 `.env` 时配置为空，不静默消费示例文件里的值。
- **验证**：`grep -n "example\|fallback" app/config.py` 确认无 fallback 路径；`cat .env.example` 确认占位符。
- **测试期取舍**：git 历史中的旧真实 key 需用户手动吊销（见"用户手动项"）。历史清洗需 `git filter-repo`，属破坏性操作，留用户自行决定。

---

### P1-2 partial 状态结果无条件覆写 ok 数据，有效字段被 NULL 覆盖
- **文件:行**：`app/service/runner.py:246-288`（ON CONFLICT DO UPDATE SET）
- **修复**：upsert 中所有可选标识/描述类字段（`upc`/`gtin13`/`title`/`brand`/`category`/`url`/`image_url`/`images`/`long_description`/`long_description_text`/`product_details`/`seller_name`/`seller_id`/`seller_type`/`catalog_seller_id`/`seller_rating`/`seller_review_count`/`other_sellers`/`in_stock`）改用 `COALESCE(excluded.x, products.x)`——仅新值非 NULL 才覆盖。价格/履约/统计类字段仍无条件更新（这些字段需要实时刷新）。
- **验证**：`tests/test_m4_runner.py` 中 partial upsert 场景；`pytest tests/ -k partial` 全过。

---

### P1-3 pace() 无锁：多任务共享同一 ProxyPool 时限速完全失效
- **文件:行**：`app/engine/proxy.py:188-208`（pace 方法）
- **修复**：`pace()` 持锁读写 `_last_req_at` 和 `_uses`（读-改-写原子化）。策略：锁内预占下次请求时间戳（`_last_req_at = now + sleep_time`），确保两个并发调用者各自算到错开的等待时长；sleep 在锁外执行，不阻塞 `get_status()`。
- **验证**：`tests/test_m2_proxy.py` 中 pace 并发测试；`pytest tests/ -k pace` 全过。

---

### P1-4 默认 API Key "dev-key-change-me" 硬编码于前后端
- **文件:行**：`app/config.py:94-104`；`frontend/composables/useApiKey.ts:13-16`
- **修复**：
  - 后端：启动时检测若 `API_KEY == _DEFAULT_API_KEY` 则打 `WARNING` 日志，不拒绝启动（测试期友好，保持开发体验）。`.env.example` 中 `API_KEY=change-me-please`。
  - 前端：`DEFAULT_KEY` 保留（开发预填，避免每次手输），加注释说明这是开发专用、生产环境须在后端 `.env` 设置真实 key。
- **测试期取舍**：采用"告警/提示"而非"硬中断"——内网测试期默认 key 告警即可，不破坏当前可用的开发体验。

---

### P1-5 ResultViewer 快速切换任务时数据错位
- **文件:行**：`frontend/components/ResultViewer.vue:80-82, 290-294`
- **修复**：
  - 引入 `activeTaskId` ref 标记"当前应展示哪个任务"。
  - watch 回调切换任务时：先更新 `activeTaskId`，再显式重置 `productsLoading = false`（清除旧请求 loading 状态，解除加载守卫阻断）。
  - 每次 fetch 在发出请求前记录 `requestedTaskId`，响应回来后对比 `activeTaskId`，不匹配则丢弃结果（防止 A 的响应覆盖 B 的面板）。
- **验证**：Nuxt build 通过；逻辑覆盖 P1-5 描述的两个故障场景（loading 未清 + in-flight 响应错位）。

---

## P2 — 数据错/功能障碍（15 条）

### 后端逻辑

#### P2-1 封控时 Lane 状态机从未被驱动
- **文件:行**：`app/service/runner.py:532-552, 614-621, 628-636`；`app/service/lanes.py:158-162`
- **修复**：
  - `_make_collector()` 改为返回 `(collector, lane)` 元组（不再只取 `_pool`，同时持有 lane 引用）。
  - `run_ids/run_keyword/run_seller` 封控分支调 `lane.notify_blocked(reason)`，驱动 Lane 状态机进入 BLOCKED；成功写入后调 `lane.notify_product_saved()`，更新产出计数。
  - `save_product` 接受可选 `lane` 参数，成功时回调 `lane.notify_product_saved()`。
- **验证**：`tests/test_m2_lanes.py` Lane 状态机测试全过；`pytest tests/ -k lanes` 全过。

#### P2-2 config.RETRY_MAX 环境变量无效——runner 用独立硬编码常量
- **文件:行**：`app/service/runner.py:30`
- **修复**：删除模块级 `RETRY_MAX=2` 常量，改为 `from app.config import RETRY_MAX`，使 `.env` 中 `RETRY_MAX=N` 生效。
- **验证**：`grep "RETRY_MAX" app/service/runner.py` 确认仅从 config 导入；`pytest tests/ -k retry` 全过。

#### P2-3 断点续采 result_count 虚增——give_up 项被计为成功
- **文件:行**：`app/service/runner.py:606-634`
- **修复**：`mark_item_done` 只在 `save_product` 返回 `True`（实际写入）后调用；`result_count` 只累计入库成功项，`give_up_count` 单独计数用于软封检测。续采初始 `result_count=0`（本轮重算），不把历史 give_up 算入。
- **验证**：`tests/test_m4_runner.py` 中 give_up 不计入 result_count 断言；全过。

#### P2-4 变动检测与 upsert 非原子——并发产生重复 product_changes 行
- **文件:行**：`app/service/runner.py:196-336`（save_product）
- **修复**：`save_product` 改用独立连接 + `BEGIN IMMEDIATE` 事务，将 detect + upsert + 写 product_changes 三步合并进单事务，原子提交或整体回滚，消除 TOCTOU 窗口。
- **验证**：`tests/test_m4_runner.py` 并发场景测试；全过。

#### P2-5 blocked/failed 退出时不触发 webhook
- **文件:行**：`app/service/runner.py:625-626, 646-647, 660-661`（及 keyword/seller 流程对应位置）
- **修复**：封控退出（`update_status blocked`）和异常退出（`update_status failed`）前均调 `_maybe_fire_webhook(task_id, webhook_url)`，与正常完成路径一致。
- **验证**：`tests/test_m4_runner.py` webhook 测试；全过。

### 并发与代理

#### P2-6 LanePool 多 lane 架构不可用——submit_ids 不存在，硬绑 lane 0
- **文件:行**：`app/service/lanes.py:12-16, 383-393`；`app/service/runner.py:532-553`
- **修复**：代码层面标注现状（`_make_collector` 注释 + `get_lane_pool` 启动告警）：LANES=1 正常，LANES>1 时打 WARNING 告警说明其余 lane 当前不被消费。`get_idle_lane()`/`pool.lanes` 接口已就绪供未来接入。不强制 `LANES=1`（避免破坏用户配置）。
- **测试期取舍**：多 lane 并发实现留待用户实际需要时接入；当前单 lane 功能完整。

#### P2-7 多 Lane 共享同一 STATE_FILE，IP 跨 Lane 污染
- **文件:行**：`app/service/lanes.py:94-108`；`app/engine/proxy.py:57-78`
- **修复**：`ProxyPool.__init__` 新增 `state_file` 参数；`Lane.__init__` 按 lane_id 区分文件（lane 0 用 `proxy_state.json`，lane N 用 `proxy_state_{N}.json`）。
- **验证**：`tests/test_m2_lanes.py` 多 lane 独立状态文件测试；全过。

#### P2-8 Lane.resume() 在持有 Lane._lock 期间执行 15s 阻塞睡眠
- **文件:行**：`app/engine/proxy.py:131-148`（rotate 方法）
- **修复**：`rotate()` 锁内完成 IP 提取和状态更新，将新代理保存到局部变量后**释放锁**，再执行 `time.sleep(BLOCK_BACKOFF)`。sleep 在锁外，`get_status()`/`notify_blocked()` 不被 15s 阻塞。
- **验证**：`tests/test_m2_proxy.py` rotate 测试；全过。

### 安全

#### P2-9 proxy_state.json 含完整代理账密（user:password@ip:port）
- **文件:行**：`app/engine/proxy.py:98-104`
- **修复**：`.gitignore` 已排除（git 路径关闭）；`/proxy/status` 已 `split("@")[-1]` 隐去账密（网络路径关闭）。`_save_state()` 落盘行为保持不变（服务复用 IP 需要完整 proxy 字符串）。
- **测试期取舍**：建议运维 `chmod 600 proxy_state.json`；评估长期可改为只落盘 `born_at`，凭据仅留内存。

#### P2-10 文件上传无大小限制 + 无 MIME 校验
- **文件:行**：`app/api.py:278-284`；`app/service/importer.py:37-41`
- **修复**：
  - `api.py`: `await file.read()` 后立即检查 `len(content) > 10MB`，超出则 400（防 zip bomb DoS）。
  - `importer.py`: xlsx 分支校验 magic bytes `PK\x03\x04`，不符则 RuntimeError → 400。
- **验证**：`tests/test_api_import.py` 大文件/非法MIME 测试；全过。

### API 契约

#### P2-11 GET /tasks 响应缺 total 字段，前端分页永久失效
- **文件:行**：`app/service/tasks.py:219-240`；`app/api.py:333-334`
- **修复**：`list_tasks()` 返回 `(items, total)` 元组，内部执行 `SELECT COUNT(*) FROM tasks`；API 层将 `total` 加入响应体；前端 `total.value = res.total ?? res.items.length`（优先后端 total）。
- **验证**：`tests/test_api_tasks.py`；全过。

#### P2-12 GET /proxy/status lane_id 类型不一致，手动换 IP 输入必 422
- **文件:行**：`frontend/components/ProxyPanel.vue:224-232, 168-175, 353-356`
- **修复**：`ProxyLane` 接口 `lane_id` 改为 `number`；`manualLaneId` 保持 `string`（input value），提交时 `parseInt` 转 number；placeholder 改为"输入 Lane ID，例如 0"；disabled/spin 比较改用 `parseInt(...)` 后的 number。
- **验证**：Nuxt build 通过；TypeScript 类型一致。

#### P2-13 GET /products|/listings next_cursor 永不为 null，末页 hasMore 无法归零
- **文件:行**：`frontend/components/ResultViewer.vue:65-66, 113-116, 156-158, 198-199`
- **修复**：前端不再依赖 `next_cursor` 是否为 null 判断末页（后端空页返回 0 而非 null，`??` 无法处理）；改用 `count < PAGE_SIZE` 推导末页标志（`productsReachedEnd`/`listingsReachedEnd`），`hasMore = !reachedEnd`。
- **验证**：Nuxt build 通过；末页逻辑正确。

### 采集数据

#### P2-14 collect_by_seller 对第 1 页发起两次 HTTP 请求
- **文件:行**：`app/engine/collector.py`（已在 BE3 修复中处理，去掉首页重复抓取）
- **验证**：`tests/test_m2_collector.py`；全过。

#### P2-15 in_stock 变动检测是永远不触发的死代码
- **文件:行**：`app/service/runner.py:69-109`；`app/models.py:81-82`；`app/models.py:212-213`（EXPECTED_COLUMNS）
- **修复**：
  - `app/models.py`：products 表增加 `in_stock INTEGER` 列；`EXPECTED_COLUMNS["products"]` 加 `"in_stock": "INTEGER"` 供旧库迁移。
  - `runner.py`：`_detect_changes` 实际读取 `row["in_stock"]`（非 None 占位）；`save_product` 从 `result["in_stock"]` 读 parser 派生值。
  - `app/engine/parser.py`：从 `ship_info.availability_status`（IN_STOCK/OUT_OF_STOCK/UNAVAILABLE）派生 `in_stock` 整数（1/0/None）。
- **验证**：`tests/test_m4_runner.py` in_stock 变动检测测试；`test_parser.py` 全过。

#### P2-16 importer._parse_text 不处理 tab 分隔符，TSV 解析为含 tab 的无效 token
- **文件:行**：`app/service/importer.py:60-63`
- **修复**：`_parse_text` 增加 `if "\t" in line: line = line.split("\t", 1)[0].strip()` 分支，优先级高于逗号检测，处理 Windows Excel 另存 TSV 的常见场景。
- **验证**：`tests/test_importer.py` TSV 测试；全过。

---

## P3 — 小改进（8 条）

#### P3-1 任务状态机无单向转换守卫——done/blocked 可被覆写回 running
- **文件:行**：`app/service/tasks.py:71-122`
- **修复**：`update_status` 先 SELECT 当前状态，对照 `_VALID_TRANSITIONS` 字典拒绝非法后退转换（如 `done→running`）；仅 `allow_resume=True`（断点续采显式路径）时允许终态→running。
- **验证**：`tests/test_m1_tasks.py` 状态机转换测试；全过。

#### P3-2 API Key 普通字符串相等比较，存在计时侧信道
- **文件:行**：`app/api.py:99`
- **修复**：改用 `hmac.compare_digest(x_api_key, config.API_KEY)` 恒时比较。
- **验证**：`grep compare_digest app/api.py` 确认；`tests/test_api_auth.py` 鉴权测试全过。

#### P3-3 /openapi.json、/docs、/redoc 未鉴权暴露
- **文件:行**：`app/api.py:59-69`（FastAPI 构造参数）
- **修复**：构造时传 `docs_url=None, redoc_url=None, openapi_url=None`，三端点全部关闭。
- **验证**：`grep -A5 "FastAPI(" app/api.py` 确认三 URL 均为 None。

#### P3-4 collect_by_seller 重复抓取第一页（资源浪费视角）
- **文件:行**：`app/engine/collector.py`
- **修复**：同 P2-14（同源），首页复用，翻页从 page=2 开始。
- **验证**：`tests/test_m2_collector.py` 全过。

#### P3-5 _paged_listing 同页内重复 product_id 不去重
- **文件:行**：`app/engine/collector.py`
- **修复**：列表推导改显式循环，迭代内即时 `seen.add(pid)`，防止自然位与赞助位同一 product_id 两次通过 `not in seen`。
- **验证**：`tests/test_m2_collector.py` 去重测试；全过。

#### P3-6 POST /proxy/rotate 响应 lane_status 类型不匹配，UI 显示 [object Object]
- **文件:行**：`frontend/components/ProxyPanel.vue:239-244, 319-326`（RotateResponse 接口 + rotateLane 中 statusStr 提取逻辑）
- **修复**：`RotateResponse.lane_status` 改为 `string | Record<string, unknown>`；`rotateLane()` 中提取 `statusStr` 时判断类型——若为 object 则取 `.state`，否则直接 `String()`。
- **验证**：Nuxt build 通过；UI 显示"状态：active"而非"[object Object]"。

#### P3-7 前端 SSR 模块级单例 + 类型缺口
- **文件:行**：`frontend/composables/useApiKey.ts:18-26`；`frontend/composables/useSelectedTask.ts:12-23, 28`；`frontend/composables/useApi.ts:114-120`；`frontend/components/TaskList.vue:189-195`
- **修复**：
  - `useApiKey`/`useSelectedTask` 中 `ref` 改为 `useState`（Nuxt 3 SSR 安全，各请求独立作用域），`import.meta.client` 守卫保护 localStorage 访问。
  - `download()` 顶部加 `if (!import.meta.client) return`，防止 SSR 调用 `document`。
  - `TaskList.vue` `typeLabel` 映射键改 `detail`/`keyword`/`seller`（与后端 VALID_TYPES 一致，删无效键 `ids`/`import`）。
  - `TaskItem` 接口补 `error_msg?`/`updated_at?`/`params?`（后端 SELECT * 实际返回）。
- **验证**：Nuxt build 通过；TypeScript 无类型错误。

#### P3-8 ProxyPanel/TaskList 计时器与轮询前端缺陷
- **文件:行**：`frontend/components/ProxyPanel.vue:265-267, 337-342, 384-391`；`frontend/components/TaskList.vue:36-37, 80-118, 146-150`
- **修复**：
  - `ProxyPanel`：引入 `resultTimer` 变量存 setTimeout 句柄；每次调用 `rotateLane` 前 `clearTimeout(resultTimer)` 再重设；`onUnmounted` 时 `clearTimeout`，防止写已卸载组件。
  - `TaskList`：引入 `hasLoadedMore` 标志；轮询时调 `pollRefresh()`——未展开"加载更多"时正常 reset，已展开时按 `loadedCount` 分批拉取已加载范围原地刷新，不重置滚动位置。
- **验证**：Nuxt build 通过。

---

## 用户手动项（本次代码修复未覆盖）

1. **吊销旧 cliproxy key** `your_cliproxy_key_here`：登录 cliproxy 控制台 → API Key 管理 → 吊销/重置该 key；在 `.env` 中更新为新 key。
2. **清洗 git 历史**：旧 key 已进入 commit `6c3a510`（`.env.example:2`）。如需彻底清除：
   ```bash
   # 需先安装 git-filter-repo
   git filter-repo --replace-text <(echo 'your_cliproxy_key_here==>your_cliproxy_key_here') --force
   git push --force  # 强推需所有协作者重新 clone
   ```
   若仓库为私有且无协作者，可评估是否需要执行（旧 key 已吊销则风险消除）。
3. **proxy_state.json 文件权限**：服务器上运行后 `chmod 600 proxy_state.json`（当前 0644，同机其他用户可读）。
4. **生产环境改 API Key**：`.env` 中 `API_KEY` 更换为随机强密码（`openssl rand -hex 32`），启动后告警自动消失。
