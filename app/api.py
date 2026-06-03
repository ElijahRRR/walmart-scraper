"""FastAPI 主应用 — M3 REST API。

端点清单：
  GET  /health                          — 健康检查（免鉴权）
  POST /collect/ids                     — 提交 ID 列表采集任务
  POST /collect/keyword                 — 提交关键词采集任务
  POST /collect/seller                  — 提交卖家采集任务
  GET  /tasks                           — 分页列出所有任务
  GET  /tasks/{task_id}                 — 按 ID 查单个任务
  GET  /products                        — keyset 分页浏览商品结果
  GET  /listings                        — keyset 分页浏览列表项结果
  POST /proxy/rotate                    — 手动换IP（指定 lane）
  GET  /proxy/status                    — 当前所有 lane 的IP/状态

鉴权：除 /health 外所有端点需要请求头 X-API-Key（与 config.API_KEY 比对）。
"""
import logging
import threading
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app import config
from app.db import init_db, get_conn
from app.service.tasks import create_task, get_task, list_tasks
from app.service.lanes import get_lane_pool

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 应用生命周期：启动时建库/建表
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化 SQLite 库。"""
    init_db()
    logger.info("应用启动，DB 就绪，端口 %d", config.PORT)
    yield
    logger.info("应用关闭")


app = FastAPI(
    title="Walmart 采集服务",
    description="沃尔玛商品数据采集 REST API",
    version="0.3.0",
    lifespan=lifespan,
)


# ─────────────────────────────────────────────────────────────────────────────
# 鉴权依赖
# ─────────────────────────────────────────────────────────────────────────────

def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> str:
    """依赖注入：验证 X-API-Key 请求头。

    正确 key 透传；错误/缺失返回 401。
    """
    if not x_api_key or x_api_key != config.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")
    return x_api_key


# ─────────────────────────────────────────────────────────────────────────────
# 请求体模型
# ─────────────────────────────────────────────────────────────────────────────

class CollectIdsRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1, description="商品 ID 列表（usItemId）")
    with_detail: bool = Field(True, description="是否采详情（二段式）")

    @field_validator("ids")
    @classmethod
    def ids_not_empty(cls, v):
        if not v:
            raise ValueError("ids 不能为空")
        return v


class CollectKeywordRequest(BaseModel):
    keyword: str = Field(..., min_length=1, description="搜索关键词")
    max_pages: int = Field(25, ge=1, le=25, description="最多翻页数（1-25）")
    min_price: Optional[float] = Field(None, ge=0, description="价格下限（可选）")
    max_price: Optional[float] = Field(None, ge=0, description="价格上限（可选）")
    with_detail: bool = Field(True, description="是否二段式采详情")


class CollectSellerRequest(BaseModel):
    seller_id: str = Field(..., min_length=1, description="卖家 ID")
    max_pages: int = Field(30, ge=1, description="最多翻页数")
    with_detail: bool = Field(True, description="是否二段式采详情")


class ProxyRotateRequest(BaseModel):
    lane_id: int = Field(0, ge=0, description="要换IP的 lane 编号（默认 0）")


# ─────────────────────────────────────────────────────────────────────────────
# 后台任务执行（在 BackgroundTasks 中运行 runner.*）
# ─────────────────────────────────────────────────────────────────────────────

def _bg_run_ids(ids: list[str], with_detail: bool) -> None:
    """在后台线程执行 ID 采集（不阻塞 HTTP 响应）。"""
    from app.service.runner import run_ids
    try:
        run_ids(ids, with_detail=with_detail)
    except Exception as exc:
        logger.exception("_bg_run_ids 异常: %s", exc)


def _bg_run_keyword(keyword: str, max_pages: int, with_detail: bool,
                    min_price: Optional[float], max_price: Optional[float]) -> None:
    from app.service.runner import run_keyword
    try:
        run_keyword(keyword, max_pages=max_pages, with_detail=with_detail,
                    min_price=min_price, max_price=max_price)
    except Exception as exc:
        logger.exception("_bg_run_keyword 异常: %s", exc)


def _bg_run_seller(seller_id: str, max_pages: int, with_detail: bool) -> None:
    from app.service.runner import run_seller
    try:
        run_seller(seller_id, max_pages=max_pages, with_detail=with_detail)
    except Exception as exc:
        logger.exception("_bg_run_seller 异常: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# 健康检查（免鉴权）
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["misc"])
def health():
    """健康检查，不需要 API Key。"""
    return {"status": "ok", "version": app.version}


# ─────────────────────────────────────────────────────────────────────────────
# 采集任务提交端点
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/collect/ids", tags=["collect"])
def submit_ids(body: CollectIdsRequest,
               background_tasks: BackgroundTasks,
               _key: str = Depends(require_api_key)):
    """提交 ID 列表采集任务，立即返回 task_id，后台执行。"""
    # 先建任务记录（状态=pending），再在后台执行
    task_id = create_task("detail", {"ids": body.ids, "with_detail": body.with_detail})
    background_tasks.add_task(_bg_run_ids, body.ids, body.with_detail)
    logger.info("submit_ids task_id=%d ids=%d", task_id, len(body.ids))
    return {"task_id": task_id, "status": "pending"}


@app.post("/collect/keyword", tags=["collect"])
def submit_keyword(body: CollectKeywordRequest,
                   background_tasks: BackgroundTasks,
                   _key: str = Depends(require_api_key)):
    """提交关键词搜索采集任务，立即返回 task_id，后台执行。"""
    params: dict[str, Any] = {
        "keyword": body.keyword,
        "max_pages": body.max_pages,
        "with_detail": body.with_detail,
    }
    if body.min_price is not None:
        params["min_price"] = body.min_price
    if body.max_price is not None:
        params["max_price"] = body.max_price

    task_id = create_task("keyword", params)
    background_tasks.add_task(
        _bg_run_keyword, body.keyword, body.max_pages, body.with_detail,
        body.min_price, body.max_price,
    )
    logger.info("submit_keyword task_id=%d keyword=%r", task_id, body.keyword)
    return {"task_id": task_id, "status": "pending"}


@app.post("/collect/seller", tags=["collect"])
def submit_seller(body: CollectSellerRequest,
                  background_tasks: BackgroundTasks,
                  _key: str = Depends(require_api_key)):
    """提交卖家采集任务，立即返回 task_id，后台执行。"""
    task_id = create_task("seller", {
        "seller_id": body.seller_id,
        "max_pages": body.max_pages,
        "with_detail": body.with_detail,
    })
    background_tasks.add_task(
        _bg_run_seller, body.seller_id, body.max_pages, body.with_detail,
    )
    logger.info("submit_seller task_id=%d seller_id=%s", task_id, body.seller_id)
    return {"task_id": task_id, "status": "pending"}


# ─────────────────────────────────────────────────────────────────────────────
# 任务查询端点
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/tasks", tags=["tasks"])
def list_tasks_api(
    limit: int = Query(20, ge=1, le=100, description="每页数量"),
    offset: int = Query(0, ge=0, description="偏移量（基于 offset 分页）"),
    _key: str = Depends(require_api_key),
):
    """分页列出任务（按 id 倒序，最新在前）。"""
    tasks = list_tasks(limit=limit, offset=offset)
    return {"items": tasks, "count": len(tasks), "limit": limit, "offset": offset}


@app.get("/tasks/{task_id}", tags=["tasks"])
def get_task_api(task_id: int, _key: str = Depends(require_api_key)):
    """按 ID 查询单个任务的状态/进度/结果计数。"""
    task = get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return task


# ─────────────────────────────────────────────────────────────────────────────
# 结果浏览端点
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/products", tags=["results"])
def list_products(
    task_id: Optional[int] = Query(None, description="按任务过滤（可选）"),
    after_id: int = Query(0, ge=0, description="keyset 分页游标（上次最后一条的 id）"),
    limit: int = Query(20, ge=1, le=200, description="每页数量"),
    _key: str = Depends(require_api_key),
):
    """keyset 分页浏览商品详情结果，可按 task_id 过滤。"""
    with get_conn() as conn:
        if task_id is not None:
            rows = conn.execute(
                "SELECT * FROM products WHERE id > ? AND task_id=?"
                " ORDER BY id ASC LIMIT ?",
                (after_id, task_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM products WHERE id > ? ORDER BY id ASC LIMIT ?",
                (after_id, limit),
            ).fetchall()

    items = [dict(r) for r in rows]
    next_cursor = items[-1]["id"] if items else after_id
    return {
        "items": items,
        "count": len(items),
        "next_cursor": next_cursor,
        "limit": limit,
    }


@app.get("/listings", tags=["results"])
def list_listings(
    task_id: Optional[int] = Query(None, description="按任务过滤（可选）"),
    after_id: int = Query(0, ge=0, description="keyset 分页游标"),
    limit: int = Query(20, ge=1, le=200, description="每页数量"),
    _key: str = Depends(require_api_key),
):
    """keyset 分页浏览搜索/卖家列表结果，可按 task_id 过滤。"""
    with get_conn() as conn:
        if task_id is not None:
            rows = conn.execute(
                "SELECT * FROM listings WHERE id > ? AND task_id=?"
                " ORDER BY id ASC LIMIT ?",
                (after_id, task_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM listings WHERE id > ? ORDER BY id ASC LIMIT ?",
                (after_id, limit),
            ).fetchall()

    items = [dict(r) for r in rows]
    next_cursor = items[-1]["id"] if items else after_id
    return {
        "items": items,
        "count": len(items),
        "next_cursor": next_cursor,
        "limit": limit,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 代理管理端点
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/proxy/rotate", tags=["proxy"])
def proxy_rotate(body: ProxyRotateRequest, _key: str = Depends(require_api_key)):
    """手动换IP：提取新 IP，恢复对应 lane 的 BLOCKED 状态。

    调用 LanePool.resume_lane(lane_id)，返回新IP信息。
    """
    pool = get_lane_pool()
    if body.lane_id >= pool.n_lanes:
        raise HTTPException(
            status_code=400,
            detail=f"lane_id {body.lane_id} 超出范围（共 {pool.n_lanes} 条 lane）",
        )
    new_ip = pool.resume_lane(body.lane_id)
    # resume_lane 内部调用 pool.rotate，可能因无 api_key 抛异常
    if new_ip is None:
        raise HTTPException(status_code=500, detail="换IP失败（请检查 PROXY_API_KEY 配置）")

    # 取换IP后的状态
    status = pool.get_lane_status(body.lane_id)
    return {
        "lane_id": body.lane_id,
        "new_ip": new_ip,
        "lane_status": status,
    }


@app.get("/proxy/status", tags=["proxy"])
def proxy_status(_key: str = Depends(require_api_key)):
    """查询所有 lane 的当前IP、寿命、产出、封控状态。"""
    pool = get_lane_pool()
    return {"lanes": pool.get_all_status()}
