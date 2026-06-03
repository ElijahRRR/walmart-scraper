"""M7 迁移健壮化测试。

场景：模拟旧 schema 数据库（products 缺 has_change/prev_* 列，tasks 缺 completed_ids 列），
调用 init_db() 后断言：
  - 不抛异常
  - 所有缺失列已被补齐
  - idx_products_has_change 索引可成功创建（不报 "no such column" 错误）
  - 服务可正常导入

注意：patch app.db._resolve_db_path 而非 app.config.DB_PATH，原因是 db.py 在模块级用
from app.config import DB_PATH 捕获了值，后续修改 config 属性不影响已导入的 db.py。
"""
import importlib
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch


# ── 工具：读取表的现有列名 ──────────────────────────────────────────────────────

def _get_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _get_indexes(conn: sqlite3.Connection) -> set[str]:
    """读取库中所有索引名称。"""
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    return {row[0] for row in rows}


# ── 旧库 DDL（故意缺列，模拟版本升级前的旧 schema）─────────────────────────────

_OLD_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT    NOT NULL CHECK(type IN ('detail', 'keyword', 'seller')),
    params      TEXT    NOT NULL DEFAULT '{}',
    status      TEXT    NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'running', 'done', 'failed', 'blocked')),
    progress    INTEGER NOT NULL DEFAULT 0,
    total       INTEGER NOT NULL DEFAULT 0,
    result_count INTEGER NOT NULL DEFAULT 0,
    error_msg   TEXT,
    -- 故意缺 completed_ids 列（M4 新增）
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)
"""

_OLD_PRODUCTS_DDL = """
CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      TEXT    NOT NULL UNIQUE,
    task_id         INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    brand           TEXT,
    title           TEXT,
    category        TEXT,
    url             TEXT,
    price           REAL,
    price_string    TEXT,
    was_price       REAL,
    currency        TEXT    DEFAULT 'USD',
    ship_price      REAL,
    ship_info       TEXT,
    order_limit     INTEGER,
    fulfillment_channel TEXT,
    is_wfs          INTEGER NOT NULL DEFAULT 0,
    seller_fulfilled INTEGER NOT NULL DEFAULT 0,
    rating          REAL,
    reviews         INTEGER,
    upc             TEXT,
    gtin13          TEXT,
    image_url       TEXT,
    images          TEXT,
    -- 故意缺 long_description / long_description_text / product_details
    -- 故意缺 seller_name/seller_id/seller_type/catalog_seller_id/seller_rating/seller_review_count
    -- 故意缺 seller_count / other_seller_count / other_sellers
    -- 故意缺 prev_price / prev_in_stock / prev_seller_count / has_change（M4 新增）
    parse_status    TEXT    DEFAULT 'ok',
    snapshot_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)
"""

_OLD_METRICS_DDL = """
CREATE TABLE IF NOT EXISTS metrics (
    id              INTEGER PRIMARY KEY DEFAULT 1,
    total_requests  INTEGER NOT NULL DEFAULT 0,
    total_success   INTEGER NOT NULL DEFAULT 0,
    total_429       INTEGER NOT NULL DEFAULT 0,
    total_blocked   INTEGER NOT NULL DEFAULT 0,
    total_products  INTEGER NOT NULL DEFAULT 0
)
"""

_OLD_DDL_STMTS = [
    _OLD_TASKS_DDL,
    _OLD_PRODUCTS_DDL,
    _OLD_METRICS_DDL,
]


# ── 辅助：在指定路径建"旧库" ─────────────────────────────────────────────────

def _build_old_db(db_path: str) -> None:
    """用旧 DDL 建一个缺列的旧 schema 数据库。"""
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        for stmt in _OLD_DDL_STMTS:
            conn.execute(stmt)
        conn.commit()


# ── 辅助：patch init_db 使用指定 db 路径 ──────────────────────────────────────

def _patch_db_path(db_file: str):
    """返回一个 patch context，使 app.db._resolve_db_path 返回指定路径。"""
    return patch("app.db._resolve_db_path", return_value=Path(db_file))


# ── 测试 1：init_db 对旧库不抛异常 ────────────────────────────────────────────

def test_init_db_on_old_schema_no_exception(tmp_path):
    """旧库存在缺列，调 init_db() 应不抛异常。"""
    db_file = str(tmp_path / "old.db")
    _build_old_db(db_file)

    import app.db as db_mod
    with _patch_db_path(db_file):
        db_mod.init_db()  # 不应抛异常


# ── 测试 2：has_change 列被补齐 ───────────────────────────────────────────────

def test_has_change_column_added(tmp_path):
    """旧库 products 缺 has_change，init_db 后该列应存在。"""
    db_file = str(tmp_path / "old.db")
    _build_old_db(db_file)

    # 确认旧库确实没有 has_change
    with sqlite3.connect(db_file) as conn:
        cols_before = _get_columns(conn, "products")
    assert "has_change" not in cols_before, "前提：旧库应缺 has_change"

    import app.db as db_mod
    with _patch_db_path(db_file):
        db_mod.init_db()

    with sqlite3.connect(db_file) as conn:
        cols_after = _get_columns(conn, "products")
    assert "has_change" in cols_after, "init_db 后 products.has_change 应被补齐"


# ── 测试 3：prev_* 列被补齐 ────────────────────────────────────────────────────

def test_prev_columns_added(tmp_path):
    """旧库 products 缺 prev_price/prev_in_stock/prev_seller_count，init_db 后应补齐。"""
    db_file = str(tmp_path / "old.db")
    _build_old_db(db_file)

    import app.db as db_mod
    with _patch_db_path(db_file):
        db_mod.init_db()

    with sqlite3.connect(db_file) as conn:
        cols = _get_columns(conn, "products")

    for col in ("prev_price", "prev_in_stock", "prev_seller_count"):
        assert col in cols, f"init_db 后 products.{col} 应被补齐"


# ── 测试 4：completed_ids 列被补齐 ───────────────────────────────────────────

def test_completed_ids_column_added(tmp_path):
    """旧库 tasks 缺 completed_ids，init_db 后应补齐。"""
    db_file = str(tmp_path / "old.db")
    _build_old_db(db_file)

    with sqlite3.connect(db_file) as conn:
        cols_before = _get_columns(conn, "tasks")
    assert "completed_ids" not in cols_before

    import app.db as db_mod
    with _patch_db_path(db_file):
        db_mod.init_db()

    with sqlite3.connect(db_file) as conn:
        cols_after = _get_columns(conn, "tasks")
    assert "completed_ids" in cols_after


# ── 测试 5：idx_products_has_change 索引成功建立 ──────────────────────────────

def test_has_change_index_created(tmp_path):
    """has_change 补列后，idx_products_has_change 索引应能建成功。"""
    db_file = str(tmp_path / "old.db")
    _build_old_db(db_file)

    import app.db as db_mod
    with _patch_db_path(db_file):
        db_mod.init_db()  # 不应因 "no such column: has_change" 报错

    with sqlite3.connect(db_file) as conn:
        indexes = _get_indexes(conn)
    assert "idx_products_has_change" in indexes, "idx_products_has_change 索引应存在"


# ── 测试 6：total_ip_used 列（metrics 表）被补齐 ─────────────────────────────

def test_total_ip_used_column_added(tmp_path):
    """旧库 metrics 缺 total_ip_used（M6 新增），init_db 后应补齐。"""
    db_file = str(tmp_path / "old.db")
    _build_old_db(db_file)

    with sqlite3.connect(db_file) as conn:
        cols_before = _get_columns(conn, "metrics")
    assert "total_ip_used" not in cols_before

    import app.db as db_mod
    with _patch_db_path(db_file):
        db_mod.init_db()

    with sqlite3.connect(db_file) as conn:
        cols_after = _get_columns(conn, "metrics")
    assert "total_ip_used" in cols_after


# ── 测试 7：新库（无旧数据）init_db 也正常 ────────────────────────────────────

def test_init_db_fresh_db_no_exception(tmp_path):
    """全新库（不存在）调 init_db() 也应正常建表，不抛异常。"""
    db_file = str(tmp_path / "fresh.db")
    assert not Path(db_file).exists()

    import app.db as db_mod
    with _patch_db_path(db_file):
        db_mod.init_db()

    assert Path(db_file).exists(), "init_db 应创建数据库文件"
    with sqlite3.connect(db_file) as conn:
        cols = _get_columns(conn, "products")
    assert "has_change" in cols, "新库 products 应含 has_change"


# ── 测试 8：init_db 幂等（连续调两次不报错） ─────────────────────────────────

def test_init_db_idempotent(tmp_path):
    """连续两次调 init_db() 不应抛异常（幂等性）。"""
    db_file = str(tmp_path / "idem.db")
    import app.db as db_mod
    with _patch_db_path(db_file):
        db_mod.init_db()
        db_mod.init_db()  # 第二次调用，所有 CREATE TABLE/INDEX IF NOT EXISTS 应无副作用


# ── 测试 9：旧库上 bump_metric 也能正常工作 ──────────────────────────────────

def test_bump_metric_on_migrated_db(tmp_path):
    """旧库迁移后，bump_metric() 应能正常累加计数器（包括补齐 updated_at 列）。"""
    db_file = str(tmp_path / "bumped.db")
    _build_old_db(db_file)

    import app.db as db_mod
    with _patch_db_path(db_file):
        db_mod.init_db()
        db_mod.bump_metric(total_requests=5, total_success=3)

        with db_mod.get_conn() as conn:
            row = conn.execute("SELECT * FROM metrics WHERE id=1").fetchone()

    assert row is not None
    assert row["total_requests"] == 5
    assert row["total_success"] == 3


# ── 测试 10：can import run_server and app.api without error ─────────────────

def test_can_import_run_server():
    """python3 -c 'import run_server' 级别：导入不抛异常。"""
    project_root = str(Path(__file__).parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    spec = importlib.util.spec_from_file_location(
        "run_server_test",
        Path(project_root) / "run_server.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # 不应抛异常（if __name__=="__main__" 不会执行）
    assert hasattr(mod, "uvicorn")


def test_can_import_app_api():
    """导入 app.api 不抛异常（FastAPI app 对象可访问）。"""
    from app.api import app as fastapi_app
    assert fastapi_app is not None
    assert fastapi_app.title == "Walmart 采集服务"


# ── 测试 11：seller_name 等 buybox 列也被补齐 ─────────────────────────────────

def test_buybox_columns_added(tmp_path):
    """旧库 products 缺 buybox 卖家字段，init_db 后应补齐。"""
    db_file = str(tmp_path / "old.db")
    _build_old_db(db_file)

    import app.db as db_mod
    with _patch_db_path(db_file):
        db_mod.init_db()

    with sqlite3.connect(db_file) as conn:
        cols = _get_columns(conn, "products")

    for col in ("seller_name", "seller_id", "seller_count", "other_seller_count"):
        assert col in cols, f"init_db 后 products.{col} 应被补齐"
