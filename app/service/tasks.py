"""任务状态机 — 落库操作。

状态流转（单向）：
  pending → running → done
                    → failed
                    → blocked

公开 API：
  create_task(type_, params)      → task_id (int)
  update_status(task_id, status, error_msg=None)
  update_progress(task_id, progress, total=None, result_count=None)
  get_task(task_id)               → dict | None
  list_tasks(limit, offset)       → list[dict]

断点续采 API（M4 新增）：
  mark_item_done(task_id, product_id)         — 将某 product_id 标记为已完成
  get_completed_ids(task_id)                  → set[str] — 已完成 ID 集合
"""
import json
import logging
from typing import Any, Optional

from app.db import get_conn

logger = logging.getLogger(__name__)

# 合法状态集合
VALID_STATUSES = {"pending", "running", "done", "failed", "blocked"}

# 合法类型
VALID_TYPES = {"detail", "keyword", "seller"}


def reconcile_interrupted_tasks() -> int:
    """启动时调用：把残留的 pending/running 任务标记为 failed。

    后台任务线程随进程退出而死，服务重启后这些任务不会有人继续执行，
    却在库里留着 pending/running 状态（"僵尸任务"）。启动时统一复位，
    避免列表里出现永远"在跑"却早已死掉的任务。

    Returns:
        被复位的任务数。
    """
    from app.db import get_conn
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE tasks SET status='failed', "
            "error_msg='服务重启中断（未完成，请重新提交）', "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
            "WHERE status IN ('pending','running')"
        )
        n = cur.rowcount
    if n:
        logger.warning("启动复位：%d 个残留 pending/running 任务标记为 failed", n)
    return n


def create_task(type_: str, params: dict) -> int:
    """创建任务，初始状态 pending，返回 task_id。

    Args:
        type_:  "detail" | "keyword" | "seller"
        params: 任务参数 dict，将被序列化为 JSON 存储
                  detail  → {"ids": [...]}
                  keyword → {"keyword": "...", "max_pages": N, ...}
                  seller  → {"seller_id": "...", "max_pages": N, ...}
    Returns:
        新任务的 id（自增整数）
    """
    if type_ not in VALID_TYPES:
        raise ValueError(f"不合法的任务类型: {type_!r}，必须是 {VALID_TYPES}")

    params_json = json.dumps(params, ensure_ascii=False)
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO tasks(type, params, status, progress, total, result_count)"
            " VALUES(?, ?, 'pending', 0, ?, 0)",
            (type_, params_json, _guess_total(type_, params)),
        )
        task_id = cur.lastrowid

    logger.info("create_task id=%d type=%s params=%s", task_id, type_, params_json[:120])
    return task_id


def _guess_total(type_: str, params: dict) -> int:
    """尽量预估子任务总量（detail 类型可精确，其余先填 0 表示未知）。"""
    if type_ == "detail":
        ids = params.get("ids") or []
        return len(ids)
    return 0  # keyword/seller 翻页数量未知


# 终态集合：进入后不得被外部随意回退
_TERMINAL_STATUSES = {"done", "blocked", "failed"}

# 合法的前向转换表（不包含显式续采路径，见 allow_resume 参数）
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending":  {"running", "failed"},
    "running":  {"done", "failed", "blocked"},
    "done":     set(),        # 终态，禁止任何外部覆写
    "blocked":  {"done", "failed"},   # 换IP后可恢复或失败
    "failed":   set(),        # 终态，禁止任何外部覆写
}


def update_status(
    task_id: int,
    status: str,
    error_msg: Optional[str] = None,
    allow_resume: bool = False,
) -> None:
    """更新任务状态，强制单向状态机转换。

    Args:
        task_id:      任务 id
        status:       目标状态（必须在 VALID_STATUSES 内）
        error_msg:    failed/blocked 时附加说明（可选）
        allow_resume: 若为 True，允许 runner 将终态任务重置为 running/pending
                      （断点续采显式路径专用，勿滥用）
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"不合法的状态: {status!r}，必须是 {VALID_STATUSES}")

    with get_conn() as conn:
        row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            logger.warning("update_status task_id=%d 不存在，跳过", task_id)
            return

        current = row["status"]
        allowed = _VALID_TRANSITIONS.get(current, set())

        if status not in allowed:
            if allow_resume and current in _TERMINAL_STATUSES and status in ("running", "pending"):
                # runner 续采路径：允许终态 → running/pending，记警告便于排查
                logger.warning(
                    "update_status task_id=%d %s → %s（allow_resume 显式续采）",
                    task_id, current, status,
                )
            else:
                logger.warning(
                    "update_status task_id=%d 拒绝非法转换 %s → %s，状态保持不变",
                    task_id, current, status,
                )
                return

        conn.execute(
            "UPDATE tasks SET status=?, error_msg=?,"
            " updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')"
            " WHERE id=?",
            (status, error_msg, task_id),
        )
    logger.info("update_status task_id=%d %s → %s", task_id, current, status)


def update_progress(task_id: int, progress: int,
                    total: Optional[int] = None,
                    result_count: Optional[int] = None) -> None:
    """更新进度计数。

    Args:
        task_id:      任务 id
        progress:     已完成的子项数（单调递增）
        total:        如已知预计子项总数，可在此更新（None=不改变）
        result_count: 成功落库的商品数（None=不改变）
    """
    sets = ["progress=?", "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')"]
    vals: list[Any] = [progress]

    if total is not None:
        sets.append("total=?")
        vals.append(total)
    if result_count is not None:
        sets.append("result_count=?")
        vals.append(result_count)

    vals.append(task_id)
    sql = f"UPDATE tasks SET {', '.join(sets)} WHERE id=?"
    with get_conn() as conn:
        conn.execute(sql, vals)


def get_task(task_id: int) -> Optional[dict]:
    """按 id 取任务记录，不存在返回 None。"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    # params 反序列化为 dict
    try:
        d["params"] = json.loads(d["params"])
    except (json.JSONDecodeError, TypeError):
        pass
    return d


# ── 断点续采 API（M4） ────────────────────────────────────────────────────────

def mark_item_done(task_id: int, product_id: str) -> None:
    """将指定 product_id 追加到任务的 completed_ids 集合（幂等）。

    completed_ids 存储为 JSON 列表（保持有序，方便调试）。
    设计上只追加，不删除。
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT completed_ids FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        if row is None:
            return

        try:
            ids_list: list[str] = json.loads(row["completed_ids"] or "[]")
        except (json.JSONDecodeError, TypeError):
            ids_list = []

        if product_id not in ids_list:
            ids_list.append(product_id)
            conn.execute(
                "UPDATE tasks SET completed_ids=?, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')"
                " WHERE id=?",
                (json.dumps(ids_list, ensure_ascii=False), task_id),
            )
            logger.debug("mark_item_done task_id=%d product_id=%s", task_id, product_id)


def get_completed_ids(task_id: int) -> set[str]:
    """返回任务已完成的 product_id 集合（空任务/不存在均返回空集合）。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT completed_ids FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
    if row is None:
        return set()
    try:
        return set(json.loads(row["completed_ids"] or "[]"))
    except (json.JSONDecodeError, TypeError):
        return set()


def list_tasks(limit: int = 20, offset: int = 0) -> tuple[list[dict], int]:
    """分页列出任务（按 id 倒序：最新的在前）。

    Returns:
        (items, total) — items 为当前页条目列表，total 为全表任务总数（用于前端分页）。
    """
    with get_conn() as conn:
        total_row = conn.execute("SELECT COUNT(*) AS cnt FROM tasks").fetchone()
        total: int = total_row["cnt"] if total_row else 0
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        try:
            d["params"] = json.loads(d["params"])
        except (json.JSONDecodeError, TypeError):
            pass
        out.append(d)
    return out, total
