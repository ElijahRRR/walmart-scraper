# 沃尔玛商品采集服务

面向代发 ERP 场景的沃尔玛（Walmart.com）商品数据采集服务。提供 REST API、极简 Web UI、变动检测、断点续采和代理 IP 管理。

---

## 架构

```
┌─────────────────────────────────────────────────────┐
│                FastAPI 单进程                        │
│                                                     │
│  POST /collect/*  →  BackgroundTasks               │
│       │                                             │
│       ▼                                             │
│  runner.run_ids / run_keyword / run_seller          │
│       │                                             │
│       ▼                                             │
│  LanePool（N 条 Lane，N = LANES 配置项）             │
│    Lane 0  ←→  ProxyPool（单IP复用+磁盘缓存+限速）   │
│    Lane 1  ←→  ProxyPool                           │
│    ...                                              │
│       │                                             │
│       ▼                                             │
│  engine/collector.py  →  engine/parser.py          │
│       │                                             │
│       ▼                                             │
│  SQLite（WAL，6张表）                               │
└─────────────────────────────────────────────────────┘
```

关键设计决策：
- **单进程**：无 Celery/Redis，直接在 FastAPI `BackgroundTasks` 后台线程执行采集
- **Lane 池**：每条 Lane 持有一个独立 ProxyPool（= 一个 IP），Lane 内串行采集+限速
- **默认关自动换 IP**：封控时停下报警（lane 状态变为 BLOCKED），通过 Web UI 或 API 手动换 IP
- **增量迁移**：`init_db()` 启动时自动用 `ALTER TABLE ADD COLUMN` 补齐旧库缺失列，无需手动迁移

---

## 安装

### 方式一：本地运行

```bash
# 1. 克隆 / 进入项目目录
cd /path/to/walmart-scraper

# 2. 创建虚拟环境（推荐 Python 3.11+）
python3 -m venv .venv
source .venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 复制配置文件
cp .env.example .env
# 编辑 .env，至少填写 PROXY_API_KEY 和 API_KEY

# 5. 启动服务
python3 run_server.py
# 或：uvicorn app.api:app --host 0.0.0.0 --port 8900 --reload
```

### 方式二：Docker Compose

```bash
cp .env.example .env
# 编辑 .env

docker-compose up -d
# 查看日志
docker-compose logs -f
```

### 方式三：systemd（Linux 生产环境）

```bash
# 1. 部署代码到 /opt/walmart-scraper
sudo cp deploy/walmart-scraper.service /etc/systemd/system/
# 按实际路径修改 service 文件中的 WorkingDirectory / User
sudo systemctl daemon-reload
sudo systemctl enable --now walmart-scraper
sudo systemctl status walmart-scraper
```

---

## 配置（.env）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PROXY_API_KEY` | `""` | cliproxy 提取 API Key（生产必填） |
| `API_KEY` | `dev-key-change-me` | 服务 REST API 鉴权 Key，**生产必改** |
| `PORT` | `8900` | HTTP 服务端口 |
| `DB_PATH` | `data/walmart.db` | SQLite 数据库路径（相对项目根或绝对路径） |
| `LANES` | `1` | 并发 Lane 数（= 同时持有的 IP 数） |
| `AUTO_ROTATE` | `false` | 自动换 IP 开关；`false`=封控停下报警（推荐），`true`=自动轮换 |
| `PACE_MIN` | `3.0` | 最短请求间隔（秒） |
| `PACE_MAX` | `7.0` | 最长请求间隔（秒） |
| `IP_MAX_AGE_MIN` | `690` | 单 IP 安全上限（分钟），默认 690 min（11.5h） |
| `RETRY_MAX` | `2` | 单商品瞬时失败最大重试次数（封控不重试） |
| `WEBHOOK_URL` | `""` | 任务完成回调 URL（空=关闭） |

---

## 启动

```bash
# 直接启动
python3 run_server.py

# 后台运行（nohup）
nohup python3 run_server.py > logs/scraper.log 2>&1 &

# 开发模式（热重载）
uvicorn app.api:app --host 0.0.0.0 --port 8900 --reload
```

服务启动后访问：
- Web UI：`http://localhost:8900/`
- API 文档：`http://localhost:8900/docs`
- 健康检查：`http://localhost:8900/health`（免鉴权）

---

## API 速览

所有端点（除 `GET /health` 和 `GET /`）需要请求头 `X-API-Key: <API_KEY>`。

### 提交采集任务

```bash
# 按商品 ID 列表采集
curl -X POST http://localhost:8900/collect/ids \
  -H "X-API-Key: dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"ids": ["12345678", "87654321"], "with_detail": true}'

# 按关键词搜索采集
curl -X POST http://localhost:8900/collect/keyword \
  -H "X-API-Key: dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"keyword": "wireless earbuds", "max_pages": 5, "with_detail": true}'

# 按卖家 ID 采集
curl -X POST http://localhost:8900/collect/seller \
  -H "X-API-Key: dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"seller_id": "SELLER123", "max_pages": 10, "with_detail": true}'
```

响应：`{"task_id": 1, "status": "pending"}`

### 任务查询

```bash
# 列出所有任务（分页）
curl "http://localhost:8900/tasks?limit=20&offset=0" \
  -H "X-API-Key: dev-key-change-me"

# 查询单个任务状态
curl http://localhost:8900/tasks/1 \
  -H "X-API-Key: dev-key-change-me"
```

### 结果浏览

```bash
# 浏览商品详情（keyset 分页）
curl "http://localhost:8900/products?task_id=1&after_id=0&limit=20" \
  -H "X-API-Key: dev-key-change-me"

# 查询有变动的商品
curl "http://localhost:8900/products/changes?limit=20" \
  -H "X-API-Key: dev-key-change-me"

# 浏览列表项（搜索/卖家列表）
curl "http://localhost:8900/listings?task_id=1" \
  -H "X-API-Key: dev-key-change-me"
```

### 代理 IP 管理

```bash
# 查看所有 Lane 的 IP 状态
curl http://localhost:8900/proxy/status \
  -H "X-API-Key: dev-key-change-me"

# 手动换 IP（lane 0）
curl -X POST http://localhost:8900/proxy/rotate \
  -H "X-API-Key: dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"lane_id": 0}'
```

### 指标

```bash
# 查看全局采集指标
curl http://localhost:8900/metrics \
  -H "X-API-Key: dev-key-change-me"
```

---

## 前端（Nuxt）

Nuxt 3 + TypeScript + Tailwind CSS 现代前端，运行在 `:3000`，通过 `/api` 代理到后端 `:8900`。

> 说明：`app/web/index.html` 是后端自带的极简回退页（直接访问 `:8900/` 仍可用），Nuxt 前端是推荐的主界面。

### 安装依赖

```bash
cd frontend
npm install
```

### 开发模式（热重载，`:3000`）

```bash
npm run dev
# 访问 http://localhost:3000
# 前端 /api/** 通过 Nitro routeRules 代理到 http://localhost:8900/**
```

### 生产构建

```bash
npm run build
# 产物位于 frontend/.output/
# 预览：node .output/server/index.mjs
```

### 功能面板

| 面板 | 说明 |
|------|------|
| 任务提交 | 三标签（按 ID / 关键词 / 卖家），支持从 txt/csv/xlsx 导入 |
| 任务列表 | 轮询 `/tasks`，状态徽章+进度条，点「查看结果」打开结果浮层 |
| 结果查看 | products / listings 两标签，keyset 分页，导出 CSV / Excel |
| 指标面板 | 6 个数字卡片（请求/成功率/429/封控/入库商品/IP 产出），自动刷新 |
| 代理面板 | Lane 状态卡片（封控高亮），一键切换 IP |

---

## Web UI 用法（旧版回退）

访问 `http://localhost:8900/` 可使用极简 Web UI（内置于后端，无需 npm）：

1. **提交采集**：切换 IDs / 关键词 / 卖家 三个标签，填写参数后提交
2. **任务列表**：查看所有任务的状态/进度/结果数，可设置自动刷新间隔
3. **查看结果**：点击任务的「查看结果」按钮，分页浏览商品详情和列表项
4. **IP 管理**：查看当前所有 Lane 的 IP 状态；Lane 封控时会高亮显示，填写 lane_id 点「获取/切换 IP」换 IP
5. **采集指标**：6 个数字卡片（总请求/成功率/429 占比/封控率/入库商品数/累计 IP 数），每 10 秒自动刷新

---

## 压测脚本用法

`scripts/stress_ip_lifespan.py` 用于测试单 IP 寿命和采集效率。

**重要：不加 `--dry-run` 会消耗真实代理 IP，请谨慎使用！**

```bash
# Dry-run 模式（用内建 mock，不消耗真实 IP）
python3 scripts/stress_ip_lifespan.py --dry-run --block-at 50

# 真实采集（会消耗 IP）
python3 scripts/stress_ip_lifespan.py \
  --ids B08N5WRWNW B07ZPKN6YR \
  --lane 0

# 结果输出到 JSON
python3 scripts/stress_ip_lifespan.py --dry-run --output result.json
```

输出指标：`total_requests` / `blocked_at_request` / `block_reason` / `products_saved` / `elapsed_sec`

---

## 数据库表结构

| 表名 | 说明 |
|------|------|
| `tasks` | 采集任务（状态机：pending→running→done/failed/blocked）|
| `products` | 商品详情快照（16 采集字段 + buybox 卖家 + 变动前值） |
| `product_changes` | 变动记录（价格/库存/卖家数每次变动一行） |
| `listings` | 搜索/卖家列表项（部分字段，关联 task_id） |
| `proxy_log` | 代理事件日志（extract/block/yield） |
| `metrics` | 全局请求指标计数器（单行累积） |

SQLite WAL 模式，`init_db()` 启动时自动建表并对旧库执行增量迁移（ALTER TABLE ADD COLUMN）。

---

## 运行测试

```bash
# 运行全套测试（不发真实网络请求）
python3 -m pytest tests/ test_parser.py -v

# 只运行迁移测试
python3 -m pytest tests/test_m7_migration.py -v
```

---

## 已知限制

1. **GetAllSellerOffers 暂缓**：`parser.parse_all_seller_offers()` 已实现但采集流程未接入，获取全部卖家报价需后续版本
2. **搜索上限 25 页**：Walmart 搜索结果最多 25 页（约 400 商品），`max_pages` 上限固定
3. **单 IP 寿命需实测**：cliproxy 官方标注 12h，实际封控可能更早（建议用 `stress_ip_lifespan.py` 测试）；`IP_MAX_AGE_MIN` 默认 690 分钟（11.5h）留有余量
4. **单进程架构**：单个 FastAPI 进程，LANES 配置项控制并发 IP 数；极高并发场景需调整
5. **Webhook 为最简实现**：`urllib` POST JSON，无签名、无 HMAC 验证，生产环境需按需加固
