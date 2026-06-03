# 沃尔玛采集服务 — 进度

## 当前状态
- Total: 28 features (M0–M7)
- Passing: 28 / 28 (100%) ✅ 项目全部完成
- Current: 全部完成

## 定稿决策（2026-06）
- 架构：单进程 FastAPI + 内置 lane 池（每 lane = 1 IP，串行+限速）
- UI：极简（任务提交 + 任务/进度 + 手动获取/切换IP按钮 + 基础统计）
- 定时重采：首版不做
- 换IP：默认关自动换；封控时停下报警，网页手动换 IP
- 不接 ERP，独立服务自跑测试

## 已有积木（服务前的引擎，已验证）
- parser.py：parse_product（16字段+卖家数量+buybox）/ parse_listing（搜索+卖家列表）/ parse_all_seller_offers（暂缓）
- collector.py：三流程 collect_by_ids / collect_by_keyword（含价格区间）/ collect_by_seller
- proxy.py：ProxyPool 单IP复用+磁盘缓存+限速
- 三流程已端到端跑通；GetAllSellerOffers 暂缓

## Session Log
### Session 1 — 计划定稿 + M0 启动
- 完成：feature_list.json（28项）、progress.md、目录骨架、git init
- Next：M0 #1-5（骨架/配置/引擎迁入/SQLite/表结构）

### Session 2 — M0 完成（2026-06-03）
- 完成：feature #1-5（全部通过）
- #1 骨架：app/{__init__,engine,service,web}/__init__.py 就位；requirements.txt 已含 fastapi/uvicorn/curl_cffi；.env.example 含全部配置项
- #2 config：app/config.py 内置 .env 加载器（不依赖 python-dotenv）；9 个配置项读取 + settings dict
- #3 引擎迁入：parser/proxy/collector 迁入 app/engine/，import 路径修正；test_parser.py 31项全过；proxy.py STATE_FILE 仍指向项目根 proxy_state.json；probe.py import 一并修正
- #4 db：app/db.py WAL 模式建库 + get_conn() 上下文管理器；DB_PATH 来自 config；首次 init_db() 幂等建表
- #5 models：app/models.py 5 张表 DDL（tasks/products/listings/proxy_log/metrics）+ 索引；products 含 16字段+卖家数量+buybox卖家；metrics 单行累积计数器
- 验证：python3 test_parser.py 全过；python3 -c "from app.config import *; from app.db import init_db; init_db()" 建库建表无报错；WAL 确认
- Issues：无
- Next：M1 #6-9（任务状态机/详情落库/列表落库/三流程接入）

### Session 3 — M1 完成（2026-06-03）
- 完成：feature #6-9（全部通过）
- #6 tasks.py：任务状态机 pending→running→done/failed/blocked；create_task/update_status/update_progress/get_task/list_tasks，全部落 tasks 表
- #7 products upsert：save_product(result, task_id)→ON CONFLICT(product_id) DO UPDATE；_status 非 ok/partial 不写入；models.py products.product_id 加 UNIQUE 约束
- #8 listings 入库：save_listing_items(items, task_id)→INSERT OR IGNORE 按 (task_id, product_id) 去重；关联 task_id
- #9 三流程接入：runner.run_ids/run_keyword/run_seller，支持 with_detail 二段式；collector 可注入（测试无真实网络请求）
- 测试：tests/test_m1_service.py，27 项断言全过；test_parser.py 31 项依然全过
- 验证：所有 collector 网络层通过 MagicMock 替换（不发真实请求）；SQLite 临时库隔离
- Issues：无
- Next：M2 #10-13（ProxyPool 升级 / Lane 池 / 防封 / proxy_log）

### Session 4 — M2 完成（2026-06-03）
- 完成：feature #10-13（全部通过）
- #10 ProxyPool 升级：get_status() 新增 born_at/age_sec 字段，返回完整IP状态（ip:port/寿命/产出计数/auto_rotate开关）；rotate() 语义确认正确（提新IP+覆盖磁盘缓存）；默认 auto_rotate=False
- #11 app/service/lanes.py：Lane（每 lane 一个独立 ProxyPool）+ LanePool（N 条 lane 管理器）；Lane.run_work() 串行执行；LanePool.get_idle_lane() 分发任务；全局单例 get_lane_pool() 从 config 读取参数
- #12 防封：Lane.notify_blocked(reason) 置 BLOCKED 状态+记日志，不自动换IP；BLOCKED lane 拒绝新任务；Lane.resume() 提供人工换IP后恢复入口；复用 collector._is_blocked 识别 403/429/waiting-room/验证码
- #13 proxy_log 落库：_log_proxy_event(ip, event, count, detail) 写 proxy_log 表；extract/block/yield 三种事件；notify_blocked 触发 block 记录；resume() 触发 yield（旧IP产出）+ extract（新IP）记录；notify_product_saved() 累计产出计数
- 测试：tests/test_m2_lanes.py，28 项断言全过；test_m1_service.py 27项无回归；test_parser.py 31项无回归
- 验证：ProxyPool._extract 全程打桩（不发真实网络请求）；Lane.pool.rotate 打桩；临时SQLite 隔离
- Issues：无
- Next：M3 #14-18（REST API）

### Session 6 — M4 完成（2026-06-03）
- 完成：feature #19-22（全部通过）
- #19 断点续采：tasks 表新增 completed_ids（JSON 列表）字段；mark_item_done(task_id, product_id) 追加、get_completed_ids(task_id) 读取；run_ids 支持 resume_task_id 参数续采模式：跳过已完成项只采剩余，每采完一项 mark_item_done
- #20 失败重试：_collect_with_retry(collector, product_id) 函数：瞬时失败（non-blocked _status/异常）最多重试 RETRY_MAX=2 次；封控状态（blocked/waiting_room/captcha）直接返回不重试；超过重试上限返回 give_up，不中止整任务；单项 give_up 后任务继续处理下一项
- #21 变动检测：products 表新增 prev_price/prev_in_stock/prev_seller_count/has_change 字段；新增 product_changes 表记录每次变动（old/new 价格+卖家数）；save_product 每次 upsert 前调 _detect_changes 对比旧值；有变动写 product_changes 记录；API 新增 GET /products/changes 端点
- #22 webhook 回调：config 新增 WEBHOOK_URL（默认空=关闭）和 RETRY_MAX；_fire_webhook 用 urllib 标准库 POST 任务摘要；_maybe_fire_webhook 从参数/config 读取 URL；run_ids/run_keyword/run_seller 完成后触发；webhook 失败只记 warning 不抛异常（blocked 状态不触发）
- 测试：tests/test_m4_robustness.py 新增 30 项测试；M1 测试调整1项（test_run_ids_exception_marks_failed → test_run_ids_exception_give_up_task_done）以反映重试后行为
- 验证：全套 115 项测试（M1-M4 + parser）全过；全程无真实网络请求；webhook 用 monkeypatch 打桩
- Issues：无
- Next：M5 #23-25（极简前端）

### Session 8 — M6 完成（2026-06-03）
- 完成：feature #26-27（全部通过）
- #26 指标仪表：
  - app/db.py 新增 bump_metric()：原子 UPDATE 累加 metrics 表 6 个计数器（total_requests/success/429/blocked/products/ip_used）
  - app/service/runner.py：_collect_with_retry 每次请求后调 bump_metric（成功/429/封控/瞬时失败各自累加）；save_product 成功写库后 bump_metric(total_products=1)
  - app/service/lanes.py：_log_proxy_event(event='extract') 时额外调 bump_metric(total_ip_used=1)
  - app/api.py 新增 GET /metrics 端点：从 metrics 表取累积计数器，从 proxy_log yield 事件聚合 avg_yield_per_ip；计算 success_rate/rate_429/blocked_rate（分母为0返回null）；需要 X-API-Key 鉴权
  - app/web/index.html 新增「采集指标」面板（metrics-grid）：6个数字卡片（总请求/成功/429/封控/入库商品/累计IP数），含成功率/占比子标注；自动刷新（默认10s）
- #27 压测脚本：
  - scripts/stress_ip_lifespan.py：顶部注释写明用法和"会消耗IP"警告；run_stress(product_ids, dry_run, block_at, lane_id)函数；dry-run内建mock collector（第N次返回封控）；统计total_requests/blocked_at_request/block_reason/products_saved/elapsed_sec；CLI支持--ids/--keyword/--dry-run/--block-at/--lane/--output
- 测试：tests/test_m6_metrics.py，28项全过；全套171项（M1-M6）+ 31 parser = 202项全过；无回归
- 验证：所有指标计算用临时SQLite注入数据断言；压测脚本dry-run封控逻辑正确；零真实网络请求
- Issues：无
- Next：M7 #28（部署文档/systemd/docker）

### Session 7 — M5 完成（2026-06-03）
- 完成：feature #23-25（全部通过）
- #23 任务提交表单：app/web/index.html 单页 UI，三标签切换（ids/keyword/seller）；ids 多行 textarea；keyword 含 max_pages/min_price/max_price/with_detail；seller 含 seller_id/max_pages/with_detail；提交调 /collect/* 端点显示 task_id
- #24 任务列表+进度+结果：任务列表表格（状态 badge/进度条/结果数）；自动刷新下拉（3s/10s/30s/关闭）；点「查看结果」调 /products?task_id + /listings?task_id keyset 分页，两标签切换；「加载更多」按钮；结果表格展示关键字段
- #25 当前IP+换IP按钮：lane 卡片网格显示当前IP/寿命/使用次数/总采集商品数/封控原因；BLOCKED 状态高亮橙色卡片+警告文字；lane_id 输入框+「获取/切换 IP」调 POST /proxy/rotate，换完自动刷新显示
- app/api.py：新增 GET /（返回 index.html，免鉴权）；挂载 StaticFiles(/static → app/web/)；import 补充 HTMLResponse/StaticFiles/Path
- 测试：tests/test_m5_ui.py 28 项全过；全套 143 项（M1-M5）+ 31 parser = 174 项全过；无回归
- Issues：无
- Next：M6 #26-27（指标仪表/压测脚本）

### Session 9 — M7 完成（2026-06-03）— BUG修复 + 部署
- 完成：feature #28（部署），并修复 init_db 迁移缺陷
- Bug修复（启动迁移健壮化）：
  - app/models.py 新增 EXPECTED_COLUMNS（每张表期望新增列及其 DDL 默认值）
  - app/db.py init_db() 改造：步骤顺序调整为「先补列→再建表/索引」；新增 _migrate_add_missing_columns()（PRAGMA table_info 读现有列，缺失列执行 ALTER TABLE ADD COLUMN，防御式 warning 不抛异常）
  - 修复场景：旧库缺 has_change/prev_*/completed_ids/total_ip_used/updated_at 列时，init_db 先补列，再 CREATE INDEX IF NOT EXISTS，不会报 "no such column"
  - 同步修复 feature_list.json 中预存在的 Unicode 智能引号导致的 JSON 解析错误
- #28 部署文件：
  - deploy/walmart-scraper.service：systemd unit 示例（User/WorkingDirectory/ExecStart/EnvironmentFile/Restart 配置）
  - Dockerfile：python:3.11-slim，VOLUME /app/data，HEALTHCHECK，CMD python3 run_server.py
  - docker-compose.yml：单服务，volumes 持久化 data/，env_file，healthcheck
  - README.md：项目简介、架构图、安装（本地/Docker/systemd 三种方式）、配置表、启动命令、API 速览（含 curl 示例）、Web UI 用法、压测脚本用法、已知限制
- 测试：tests/test_m7_migration.py（12项）：旧库建库→init_db→断言补列/索引/bump_metric 正常；全套183项测试+31 parser 全过，零回归
- Issues：feature_list.json 中 Unicode 智能引号历史遗留问题一并修复（不影响任何代码逻辑，纯文件问题）
- Next：项目完成

### Session 5 — M3 完成（2026-06-03）
- 完成：feature #14-18（全部通过）
- #14 三采集端点：POST /collect/ids|keyword|seller，Pydantic 校验请求体，BackgroundTasks 后台执行，先建 task 记录返回 task_id（立即响应）；参数错误 422
- #15 任务查询：GET /tasks 分页列表（limit/offset）+ GET /tasks/{id} 单任务（含 status/progress/total/result_count）；不存在返回 404
- #16 结果浏览：GET /products 和 GET /listings，keyset 分页（after_id 游标）+ task_id 可选过滤；空任务过滤返回空列表不报错
- #17 代理管理：POST /proxy/rotate（调 LanePool.resume_lane，返回新IP + lane 状态）+ GET /proxy/status（返回所有 lane 快照）；lane_id 越界返回 400
- #18 API Key 鉴权：依赖注入 require_api_key，请求头 X-API-Key；GET /health 豁免；默认 dev-key-change-me，可 .env 覆盖
- 新增文件：app/api.py（FastAPI 主应用）、run_server.py（uvicorn 启动脚本）、tests/test_m3_api.py（30 项断言）
- 验证：85 项测试（M1 27 + M2 28 + M3 30）全过；test_parser.py 31 项无回归；全程无真实网络请求
- Issues：无
- Next：M4 #19-22（断点续采/失败重试/变动检测/webhook）
