"""数据库连接层：SQLite（WAL 模式）+ 首次启动自动建表 + 增量迁移补列。

使用方式：
    from app.db import get_conn, init_db
    init_db()           # 程序启动时调一次
    with get_conn() as conn:
        conn.execute(...)
"""
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.config import DB_PATH
from app.models import DDL_STATEMENTS, EXPECTED_COLUMNS

logger = logging.getLogger(__name__)


def _resolve_db_path() -> Path:
    """把 DB_PATH（相对项目根 or 绝对路径）解析为绝对路径。"""
    p = Path(DB_PATH)
    if not p.is_absolute():
        # 相对路径：相对于项目根目录（db.py 上两层）
        project_root = Path(__file__).parent.parent
        p = project_root / p
    return p


def backup_db(keep: int = 10) -> Path | None:
    """把当前 DB 备份到 data/backups/walmart_YYYYMMDD_HHMMSS.db，保留最近 keep 份。

    仅在 DB 存在且非空（>8KB，即已有数据/表）时备份。返回备份路径或 None。
    防呆：万一 DB 被误删/重置，可从备份恢复。
    """
    import shutil
    from datetime import datetime

    src = _resolve_db_path()
    if not src.exists() or src.stat().st_size < 8192:
        return None
    backup_dir = src.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = backup_dir / f"walmart_{ts}.db"
    try:
        shutil.copy2(src, dst)
    except OSError as exc:
        logger.warning("DB 备份失败（不影响启动）: %s", exc)
        return None
    # 清理旧备份，只留最近 keep 份
    backups = sorted(backup_dir.glob("walmart_*.db"))
    for old in backups[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass
    logger.info("DB 已备份：%s（保留最近 %d 份）", dst.name, keep)
    return dst


def _get_existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """用 PRAGMA table_info 读取表中现有列名，表不存在时返回空集合。"""
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {row[1] for row in rows}  # row[1] = name
    except Exception:
        return set()


def _migrate_add_missing_columns(conn: sqlite3.Connection) -> None:
    """增量迁移：对照 EXPECTED_COLUMNS，对每张表补充缺失列。

    只执行 ALTER TABLE ADD COLUMN，不删列，不改已有列类型——纯增量，幂等。
    补列失败记 warning 不抛异常（防御式，不影响整体启动）。
    """
    for table, columns in EXPECTED_COLUMNS.items():
        existing = _get_existing_columns(conn, table)
        if not existing:
            # 表不存在（还没建），跳过——由 DDL_STATEMENTS 负责建表
            continue
        for col_name, col_def in columns.items():
            if col_name not in existing:
                sql = f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"
                try:
                    conn.execute(sql)
                    logger.info("迁移：%s 补列 %s", table, col_name)
                except Exception as exc:
                    logger.warning("迁移补列失败（%s.%s）: %s", table, col_name, exc)


def init_db() -> None:
    """首次启动时建库建表（幂等），并对旧库执行增量迁移补列。

    执行顺序：
      1. PRAGMA 设置
      2. 增量迁移补列（先补，防止旧库缺列导致 CREATE INDEX 失败）
      3. DDL_STATEMENTS（CREATE TABLE/INDEX IF NOT EXISTS，幂等）
      4. COMMIT
    """
    db_path = _resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        # 开启 WAL 模式：并发读写性能更好
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")  # WAL 下可降级，性能好安全足够

        # 步骤 2：先做增量迁移（旧库补缺列），再建表/建索引
        _migrate_add_missing_columns(conn)

        # 步骤 3：CREATE TABLE/INDEX IF NOT EXISTS（新库建表，旧库索引幂等）
        for stmt in DDL_STATEMENTS:
            conn.execute(stmt)
        conn.commit()
    logger.info("数据库初始化完成：%s", db_path)


@contextmanager
def get_conn():
    """上下文管理器：获取连接，自动提交或回滚，用完关闭。

    使用方式：
        with get_conn() as conn:
            conn.execute("INSERT ...")
    """
    db_path = _resolve_db_path()
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row          # 支持按列名访问
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def bump_metric(
    total_requests: int = 0,
    total_success: int = 0,
    total_429: int = 0,
    total_blocked: int = 0,
    total_products: int = 0,
    total_ip_used: int = 0,
) -> None:
    """原子累加 metrics 表中的指标计数器。

    以 INSERT OR IGNORE 确保行存在，再 UPDATE 累加。
    设计为防御式：写失败只记 warning，不向上抛异常（不影响主采集流程）。
    """
    try:
        with get_conn() as conn:
            conn.execute("INSERT OR IGNORE INTO metrics(id) VALUES(1)")
            conn.execute(
                """
                UPDATE metrics SET
                    total_requests = total_requests + ?,
                    total_success  = total_success  + ?,
                    total_429      = total_429      + ?,
                    total_blocked  = total_blocked  + ?,
                    total_products = total_products + ?,
                    total_ip_used  = total_ip_used  + ?,
                    updated_at     = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                WHERE id = 1
                """,
                (
                    total_requests, total_success,
                    total_429, total_blocked,
                    total_products, total_ip_used,
                ),
            )
    except Exception as exc:
        logger.warning("bump_metric 写入失败（不影响采集）: %s", exc)
