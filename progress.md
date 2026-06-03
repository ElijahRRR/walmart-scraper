# 沃尔玛采集服务 — 进度

## 当前状态
- Total: 28 features (M0–M7)
- Passing: 0 / 28 (0%)
- Current: M0 项目骨架

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
