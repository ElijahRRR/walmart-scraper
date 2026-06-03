"""数据库连接层：SQLite（WAL 模式）+ 首次启动自动建表。

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

from app.config import DB_PATH
from app.models import DDL_STATEMENTS

logger = logging.getLogger(__name__)


def _resolve_db_path() -> Path:
    """把 DB_PATH（相对项目根 or 绝对路径）解析为绝对路径。"""
    p = Path(DB_PATH)
    if not p.is_absolute():
        # 相对路径：相对于项目根目录（db.py 上两层）
        project_root = Path(__file__).parent.parent
        p = project_root / p
    return p


def init_db() -> None:
    """首次启动时建库建表（幂等：表已存在不报错）。"""
    db_path = _resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        # 开启 WAL 模式：并发读写性能更好
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")  # WAL 下可降级，性能好安全足够
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
