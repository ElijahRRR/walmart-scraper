"""表结构定义（DDL）。

5 张表：
  tasks       — 采集任务（状态机）
  products    — 商品快照（16采集字段 + 卖家数量 + buybox卖家信息）
  listings    — 列表项（搜索/卖家shopall的部分字段，关联任务）
  proxy_log   — 代理事件日志（提取/封控 + 产出计数）
  metrics     — 全局请求指标计数器（单行累积）
"""

# ── tasks 表 ────────────────────────────────────────────────────────────────
_CREATE_TASKS = """
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT    NOT NULL CHECK(type IN ('detail', 'keyword', 'seller')),
    params      TEXT    NOT NULL DEFAULT '{}',   -- JSON：ids列表 / keyword / seller_id 等
    status      TEXT    NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'running', 'done', 'failed', 'blocked')),
    progress    INTEGER NOT NULL DEFAULT 0,      -- 已完成的子项数
    total       INTEGER NOT NULL DEFAULT 0,      -- 预计子项总数（0=未知）
    result_count INTEGER NOT NULL DEFAULT 0,     -- 成功落库的商品数
    error_msg   TEXT,                            -- failed/blocked 时的错误说明
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)
"""

# ── products 表 ─────────────────────────────────────────────────────────────
# 16 采集字段（parse_product 输出） + seller_count / other_seller_count + buybox 卖家 + 快照时间
_CREATE_PRODUCTS = """
CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      TEXT    NOT NULL,            -- usItemId（沃尔玛商品ID）
    task_id         INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    -- ── 基础字段（16个采集字段） ──────────────────────────────────────────
    brand           TEXT,
    title           TEXT,
    category        TEXT,
    url             TEXT,
    -- 价格
    price           REAL,
    price_string    TEXT,
    was_price       REAL,
    currency        TEXT    DEFAULT 'USD',
    -- 运费
    ship_price      REAL,                        -- 0.0=免邮 / NULL=未知
    ship_info       TEXT,                        -- JSON
    order_limit     INTEGER,
    -- 履约
    fulfillment_channel TEXT,                    -- walmart_internal / wfs / seller_fulfilled / unknown
    is_wfs          INTEGER NOT NULL DEFAULT 0,  -- 0/1 布尔
    seller_fulfilled INTEGER NOT NULL DEFAULT 0,
    -- 评价
    rating          REAL,
    reviews         INTEGER,
    -- 标识
    upc             TEXT,
    gtin13          TEXT,
    -- 图片
    image_url       TEXT,
    images          TEXT,                        -- JSON 数组
    -- 描述
    long_description      TEXT,                  -- 原始 HTML
    long_description_text TEXT,
    product_details       TEXT,                  -- JSON [{name,value},...]
    -- ── buybox 卖家信息 ───────────────────────────────────────────────────
    seller_name     TEXT,
    seller_id       TEXT,
    seller_type     TEXT,
    catalog_seller_id INTEGER,
    seller_rating   REAL,
    seller_review_count INTEGER,
    -- ── 卖家数量 ─────────────────────────────────────────────────────────
    seller_count        INTEGER,                 -- 可售卖家总数（含 buybox）
    other_seller_count  INTEGER,                 -- 除 buybox 外的其他卖家数
    other_sellers       TEXT,                    -- JSON：SSR 内联的其他卖家报价
    -- ── 元数据 ───────────────────────────────────────────────────────────
    parse_status    TEXT    DEFAULT 'ok',        -- ok / partial / blocked / ...
    snapshot_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)
"""

_CREATE_PRODUCTS_IDX = [
    "CREATE INDEX IF NOT EXISTS idx_products_product_id ON products(product_id)",
    "CREATE INDEX IF NOT EXISTS idx_products_task_id ON products(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_products_snapshot_at ON products(snapshot_at)",
]

# ── listings 表 ─────────────────────────────────────────────────────────────
# 搜索/卖家列表项（部分字段），关联 task_id；不同任务可重复采同一商品
_CREATE_LISTINGS = """
CREATE TABLE IF NOT EXISTS listings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    product_id  TEXT    NOT NULL,                -- usItemId
    title       TEXT,
    brand       TEXT,
    price       REAL,
    rating      REAL,
    reviews     INTEGER,
    seller_name TEXT,
    seller_id   TEXT,
    fulfillment_type TEXT,                       -- FC / MARKETPLACE / ...
    url         TEXT,
    image_url   TEXT,
    snapshot_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)
"""

_CREATE_LISTINGS_IDX = [
    "CREATE INDEX IF NOT EXISTS idx_listings_task_id ON listings(task_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_listings_task_item ON listings(task_id, product_id)",
]

# ── proxy_log 表 ─────────────────────────────────────────────────────────────
_CREATE_PROXY_LOG = """
CREATE TABLE IF NOT EXISTS proxy_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ip          TEXT    NOT NULL,                -- ip:port（不含账密）
    event       TEXT    NOT NULL CHECK(event IN ('extract', 'block', 'yield')),
    -- extract: 提取新IP；block: 封控/失败；yield: 本轮产出记录
    count       INTEGER NOT NULL DEFAULT 0,      -- 产出商品数（event=yield 时有意义）
    detail      TEXT,                            -- 附加说明（封控原因等）
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)
"""

_CREATE_PROXY_LOG_IDX = [
    "CREATE INDEX IF NOT EXISTS idx_proxy_log_ip ON proxy_log(ip)",
    "CREATE INDEX IF NOT EXISTS idx_proxy_log_created_at ON proxy_log(created_at)",
]

# ── metrics 表 ─────────────────────────────────────────────────────────────
# 全局累积计数器，单行（id=1），每次请求后更新
_CREATE_METRICS = """
CREATE TABLE IF NOT EXISTS metrics (
    id              INTEGER PRIMARY KEY DEFAULT 1,
    total_requests  INTEGER NOT NULL DEFAULT 0,  -- 总请求次数
    total_success   INTEGER NOT NULL DEFAULT 0,  -- 200 且解析 ok 的次数
    total_429       INTEGER NOT NULL DEFAULT 0,  -- HTTP 429 次数
    total_blocked   INTEGER NOT NULL DEFAULT 0,  -- 封控次数（含 403/验证码等）
    total_products  INTEGER NOT NULL DEFAULT 0,  -- 成功入库的商品总数
    total_ip_used   INTEGER NOT NULL DEFAULT 0,  -- 累计使用过的 IP 数
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)
"""

_INSERT_METRICS_ROW = """
INSERT OR IGNORE INTO metrics(id) VALUES(1)
"""

# ── 导出：建表语句列表（db.py 遍历执行） ─────────────────────────────────────
DDL_STATEMENTS: list[str] = [
    _CREATE_TASKS,
    _CREATE_PRODUCTS,
    *_CREATE_PRODUCTS_IDX,
    _CREATE_LISTINGS,
    *_CREATE_LISTINGS_IDX,
    _CREATE_PROXY_LOG,
    *_CREATE_PROXY_LOG_IDX,
    _CREATE_METRICS,
    _INSERT_METRICS_ROW,
]
