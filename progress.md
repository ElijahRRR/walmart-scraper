# 沃尔玛采集服务 — 进度

## 当前状态
- Total: 28 features (M0–M7)
- Passing: 5 / 28 (18%)
- Current: M1 任务/落库逻辑

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
