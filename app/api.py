"""FastAPI 主应用 — M3/M4/M5 REST API + 前端。

端点清单：
  GET  /                                — 极简 Web UI（免鉴权，M5）
  GET  /health                          — 健康检查（免鉴权）
  POST /collect/ids                     — 提交 ID 列表采集任务
  POST /collect/keyword                 — 提交关键词采集任务
  POST /collect/seller                  — 提交卖家采集任务
  GET  /tasks                           — 分页列出所有任务
  GET  /tasks/{task_id}                 — 按 ID 查单个任务
  GET  /products                        — keyset 分页浏览商品结果
  GET  /products/changes                — 查询有变动的商品（M4）
  GET  /listings                        — keyset 分页浏览列表项结果
  POST /proxy/rotate                    — 手动换IP（指定 lane）
  GET  /proxy/status                    — 当前所有 lane 的IP/状态

鉴权：除 /health 和 GET / 外所有端点需要请求头 X-API-Key（与 config.API_KEY 比对）。
      可在 .env 设置 REQUIRE_API_KEY=false 完全关闭鉴权（仅限可信内网）。
"""
import hmac
import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import (
    BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from app import config
from app.db import init_db, get_conn
from app.service.tasks import create_task, get_task, list_tasks, delete_tasks
from app.service.lanes import get_lane_pool
from app.service.importer import parse_upload

# 前端静态文件目录（app/web/）
_WEB_DIR = Path(__file__).parent / "web"

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 应用生命周期：启动时建库/建表
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时备份 DB + 初始化 SQLite 库 + 复位重启中断的僵尸任务。"""
    from app.db import backup_db
    backup_db()        # 防呆：启动先备份现有数据库（保留最近10份）
    init_db()
    # 服务重启会杀掉后台采集线程，残留的 pending/running 任务需复位为 failed
    from app.service.tasks import reconcile_interrupted_tasks
    reconcile_interrupted_tasks()
    # 预热代理：复用磁盘缓存的 IP（**不提取新IP**），让 /proxy/status 重启后立即可见、确认跨重启复用
    try:
        from app.service.lanes import get_lane_pool
        pool0 = get_lane_pool()._lanes[0]._pool
        if pool0._load_state():
            st = pool0.get_status()
            logger.info("复用磁盘缓存代理 IP（跨重启）：%s（存活 %.0f 分钟）",
                        st.get("proxy"), st.get("age_min", 0))
        else:
            logger.info("无可复用的代理缓存，将在首次采集时提取")
    except Exception:
        logger.warning("代理预热失败（不影响启动）", exc_info=True)
    logger.info("应用启动，DB 就绪，端口 %d", config.PORT)
    yield
    logger.info("应用关闭")


app = FastAPI(
    title="Walmart 采集服务",
    description="沃尔玛商品数据采集 REST API",
    version="0.3.0",
    lifespan=lifespan,
    # 关闭内置文档端点：/docs、/redoc、/openapi.json 默认暴露且无鉴权，
    # 降低攻击者发现端点结构的门槛。内网服务无需对外暴露接口文档。
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# CORS 中间件：允许 Nuxt 前端 (localhost:3000) 及本地开发访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件（app/web/ 目录，路径 /static）
if _WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR)), name="static")


# ─────────────────────────────────────────────────────────────────────────────
# 鉴权依赖
# ─────────────────────────────────────────────────────────────────────────────

def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> str:
    """依赖注入：验证 X-API-Key 请求头。

    使用恒时比较（hmac.compare_digest）防止计时侧信道攻击。
    正确 key 透传；错误/缺失返回 401。
    当 config.REQUIRE_API_KEY=False 时完全跳过校验（内网免鉴权模式）。
    """
    if not config.REQUIRE_API_KEY:
        return ""  # 鉴权已关闭（REQUIRE_API_KEY=false）
    if not x_api_key or not hmac.compare_digest(x_api_key, config.API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")
    return x_api_key


# ─────────────────────────────────────────────────────────────────────────────
# 请求体模型
# ─────────────────────────────────────────────────────────────────────────────

class CollectIdsRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1, description="商品 ID 列表（usItemId）")
    with_detail: bool = Field(True, description="是否采详情（二段式）")
    backend_gtin: bool = Field(False, description="从沃尔玛后台查权威 UPC/GTIN（更准/较慢，默认关）")

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
    backend_gtin: bool = Field(False, description="从沃尔玛后台查权威 UPC/GTIN（更准/较慢，默认关）")


class CollectSellerRequest(BaseModel):
    seller_id: str = Field(..., min_length=1, description="卖家 ID")
    max_pages: int = Field(30, ge=1, description="最多翻页数")
    with_detail: bool = Field(True, description="是否二段式采详情")
    backend_gtin: bool = Field(False, description="从沃尔玛后台查权威 UPC/GTIN（更准/较慢，默认关）")


class ProxyRotateRequest(BaseModel):
    lane_id: int = Field(0, ge=0, description="要换IP的 lane 编号（默认 0）")


# ─────────────────────────────────────────────────────────────────────────────
# 后台任务执行（在 BackgroundTasks 中运行 runner.*）
# ─────────────────────────────────────────────────────────────────────────────

def _bg_run_ids(ids: list[str], with_detail: bool, task_id: Optional[int] = None,
                backend_gtin: bool = False) -> None:
    """在后台线程执行 ID 采集（不阻塞 HTTP 响应）。复用 API 已建的 task_id。"""
    from app.service.runner import run_ids
    try:
        run_ids(ids, with_detail=with_detail, task_id=task_id, backend_gtin=backend_gtin)
    except Exception as exc:
        logger.exception("_bg_run_ids 异常: %s", exc)


def _bg_run_keyword(keyword: str, max_pages: int, with_detail: bool,
                    min_price: Optional[float], max_price: Optional[float],
                    task_id: Optional[int] = None, backend_gtin: bool = False) -> None:
    from app.service.runner import run_keyword
    try:
        run_keyword(keyword, max_pages=max_pages, with_detail=with_detail,
                    min_price=min_price, max_price=max_price, task_id=task_id,
                    backend_gtin=backend_gtin)
    except Exception as exc:
        logger.exception("_bg_run_keyword 异常: %s", exc)


def _bg_run_seller(seller_id: str, max_pages: int, with_detail: bool,
                   task_id: Optional[int] = None, backend_gtin: bool = False) -> None:
    from app.service.runner import run_seller
    try:
        run_seller(seller_id, max_pages=max_pages, with_detail=with_detail,
                   task_id=task_id, backend_gtin=backend_gtin)
    except Exception as exc:
        logger.exception("_bg_run_seller 异常: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# 前端 Web UI（M5）— 免鉴权，页面内 JS 自带 X-API-Key 调后端
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, tags=["ui"], include_in_schema=False)
def ui_index():
    """返回极简前端页面（app/web/index.html）。"""
    html_file = _WEB_DIR / "index.html"
    if not html_file.exists():
        raise HTTPException(status_code=404, detail="前端文件不存在")
    return HTMLResponse(content=html_file.read_text(encoding="utf-8"))


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
    task_id = create_task("detail", {"ids": body.ids, "with_detail": body.with_detail,
                                     "backend_gtin": body.backend_gtin})
    background_tasks.add_task(_bg_run_ids, body.ids, body.with_detail, task_id,
                             body.backend_gtin)
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
        "backend_gtin": body.backend_gtin,
    }
    if body.min_price is not None:
        params["min_price"] = body.min_price
    if body.max_price is not None:
        params["max_price"] = body.max_price

    task_id = create_task("keyword", params)
    background_tasks.add_task(
        _bg_run_keyword, body.keyword, body.max_pages, body.with_detail,
        body.min_price, body.max_price, task_id, body.backend_gtin,
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
        "backend_gtin": body.backend_gtin,
    })
    background_tasks.add_task(
        _bg_run_seller, body.seller_id, body.max_pages, body.with_detail, task_id,
        body.backend_gtin,
    )
    logger.info("submit_seller task_id=%d seller_id=%s", task_id, body.seller_id)
    return {"task_id": task_id, "status": "pending"}


# ─────────────────────────────────────────────────────────────────────────────
# 文件批量导入端点（txt / csv / xlsx）
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/collect/import", tags=["collect"])
async def collect_import(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="txt/csv/xlsx，每行/每列一个条目"),
    type: str = Form(..., description="ids | keyword | seller"),
    with_detail: bool = Form(True),
    max_pages: int = Form(25),
    min_price: Optional[float] = Form(None),
    max_price: Optional[float] = Form(None),
    backend_gtin: bool = Form(False, description="从沃尔玛后台查权威 UPC/GTIN（默认关）"),
    _key: str = Depends(require_api_key),
):
    """上传文件批量提交采集任务。

    - type=ids     ：解析出的所有 ID 合并为**一个**详情采集任务。
    - type=keyword ：每行一个关键词，**每个**关键词建一个任务。
    - type=seller  ：每行一个卖家ID，**每个**卖家建一个任务。
    返回解析条数、创建任务数、task_id 列表。
    """
    if type not in ("ids", "keyword", "seller"):
        raise HTTPException(status_code=400, detail="type 必须是 ids | keyword | seller")

    content = await file.read()

    # 文件大小限制：超过 10 MB 直接拒绝，防止内存 DoS（zip bomb 等）
    _MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"文件过大（{len(content)} 字节），上限 {_MAX_UPLOAD_BYTES} 字节（10 MB）",
        )

    try:
        tokens = parse_upload(file.filename, content)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not tokens:
        raise HTTPException(status_code=400, detail="文件未解析出任何有效条目")

    task_ids: list[int] = []
    if type == "ids":
        tid = create_task("detail", {"ids": tokens, "with_detail": with_detail,
                                     "backend_gtin": backend_gtin, "source": file.filename})
        background_tasks.add_task(_bg_run_ids, tokens, with_detail, tid, backend_gtin)
        task_ids.append(tid)
    elif type == "keyword":
        for kw in tokens:
            params: dict[str, Any] = {"keyword": kw, "max_pages": max_pages,
                                      "with_detail": with_detail,
                                      "backend_gtin": backend_gtin, "source": file.filename}
            if min_price is not None:
                params["min_price"] = min_price
            if max_price is not None:
                params["max_price"] = max_price
            tid = create_task("keyword", params)
            background_tasks.add_task(_bg_run_keyword, kw, max_pages, with_detail,
                                      min_price, max_price, tid, backend_gtin)
            task_ids.append(tid)
    else:  # seller
        for sid in tokens:
            tid = create_task("seller", {"seller_id": sid, "max_pages": max_pages,
                                         "with_detail": with_detail,
                                         "backend_gtin": backend_gtin, "source": file.filename})
            background_tasks.add_task(_bg_run_seller, sid, max_pages, with_detail, tid,
                                      backend_gtin)
            task_ids.append(tid)

    logger.info("collect_import type=%s 解析=%d 建任务=%d", type, len(tokens), len(task_ids))
    return {"type": type, "parsed": len(tokens), "created": len(task_ids), "task_ids": task_ids}


# ─────────────────────────────────────────────────────────────────────────────
# 卖家后台会话上报端点（本地 upload_session 脚本 → DMIT）
# ─────────────────────────────────────────────────────────────────────────────

class SellerSessionRequest(BaseModel):
    headers: dict[str, str] = Field(..., description="会话请求头（含 cookie / x-xsrf-token / wm_*）")
    proxy: Optional[dict] = Field(None, description="账号专属代理 {type,host,port,user,pass}")
    browser_id: Optional[str] = Field(None, description="BitBrowser 窗口 ID")
    captured_at: Optional[int] = Field(None, description="本地导出时的 Unix 时间戳")


def _seller_session_path():
    from pathlib import Path
    from app import config
    return Path(config.SELLER_SESSION_FILE)


@app.post("/seller-session", tags=["seller-session"])
def upload_seller_session(body: SellerSessionRequest, _key: str = Depends(require_api_key)):
    """接收本地上报的卖家后台会话，落盘到 config.SELLER_SESSION_FILE（0600）。

    供'从后台查 UPC/GTIN'对账使用。会话含登录 cookie，按敏感数据处理。
    """
    h = {k.lower(): v for k, v in (body.headers or {}).items()}
    if "cookie" not in h or "x-xsrf-token" not in h:
        raise HTTPException(status_code=400, detail="会话缺少 cookie 或 x-xsrf-token，无效")

    import json as _json
    import os as _os
    path = _seller_session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"headers": body.headers, "proxy": body.proxy,
               "browser_id": body.browser_id, "captured_at": body.captured_at or int(time.time())}
    # 原子写 + 收紧权限（仅属主可读写）
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        _os.chmod(tmp, 0o600)
    except OSError:
        pass
    _os.replace(tmp, path)
    logger.info("seller-session 已更新 browser_id=%s cookie_len=%d proxy=%s",
                body.browser_id, len(h.get("cookie", "")), bool(body.proxy))
    return {"ok": True, "saved": str(path), "captured_at": payload["captured_at"],
            "cookie_len": len(h.get("cookie", "")), "has_proxy": bool(body.proxy)}


@app.get("/seller-session/status", tags=["seller-session"])
def seller_session_status(check: bool = Query(False, description="是否发一次 isbm 探测会话是否仍有效"),
                          _key: str = Depends(require_api_key)):
    """查看会话状态：是否存在、导出至今多久；check=1 时实打一次 isbm 验活（观察时效用）。"""
    import json as _json
    path = _seller_session_path()
    if not path.exists():
        return {"exists": False, "path": str(path)}
    try:
        sess = _json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"exists": True, "path": str(path), "valid_json": False, "error": str(exc)}
    cap = sess.get("captured_at") or 0
    age_min = round((time.time() - cap) / 60, 1) if cap else None
    out = {"exists": True, "path": str(path), "captured_at": cap, "age_min": age_min,
           "has_proxy": bool(sess.get("proxy")), "browser_id": sess.get("browser_id")}
    if check:
        try:
            from app.engine.isbm_client import IsbmClient, SessionExpired
            cli = IsbmClient(sess, group_size=1)
            try:
                hit = cli.fetch(["42379869"])   # 稳定探针商品
                out["alive"] = bool(hit)
            except SessionExpired as exc:
                out["alive"] = False
                out["detail"] = str(exc)
        except Exception as exc:
            out["alive"] = None
            out["detail"] = f"探测异常: {exc}"
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 任务查询端点
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/tasks", tags=["tasks"])
def list_tasks_api(
    limit: int = Query(20, ge=1, le=100, description="每页数量"),
    offset: int = Query(0, ge=0, description="偏移量（基于 offset 分页）"),
    _key: str = Depends(require_api_key),
):
    """分页列出任务（按 id 倒序，最新在前）。返回 total 供前端分页计算。"""
    tasks, total = list_tasks(limit=limit, offset=offset)
    return {"items": tasks, "count": len(tasks), "total": total, "limit": limit, "offset": offset}


@app.get("/tasks/{task_id}", tags=["tasks"])
def get_task_api(task_id: int, _key: str = Depends(require_api_key)):
    """按 ID 查询单个任务的状态/进度/结果计数。"""
    task = get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return task


class DeleteTasksRequest(BaseModel):
    task_ids: list[int] = Field(..., min_length=1, description="要删除的任务 ID 列表")
    vacuum: bool = Field(False, description="删除后 VACUUM 收缩数据库文件（会短暂锁库）")


@app.post("/tasks/delete", tags=["tasks"])
def delete_tasks_api(body: DeleteTasksRequest, _key: str = Depends(require_api_key)):
    """批量删除任务**及其采集数据**（products/listings/product_changes），释放空间。

    ⚠️ 不可恢复：会删掉这些任务采集到的商品数据。
    """
    stats = delete_tasks(body.task_ids, vacuum=body.vacuum)
    logger.info("delete_tasks ids=%s → %s", body.task_ids, stats)
    return {"ok": True, "deleted": stats}


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


@app.get("/products/changes", tags=["results"])
def list_product_changes(
    product_id: Optional[str] = Query(None, description="按商品 ID 过滤（可选）"),
    after_id: int = Query(0, ge=0, description="keyset 分页游标"),
    limit: int = Query(20, ge=1, le=200, description="每页数量"),
    _key: str = Depends(require_api_key),
):
    """M4：查询有价格/库存/卖家数变动的商品变动记录（product_changes 表）。"""
    with get_conn() as conn:
        if product_id is not None:
            rows = conn.execute(
                "SELECT * FROM product_changes WHERE id > ? AND product_id=?"
                " ORDER BY id ASC LIMIT ?",
                (after_id, product_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM product_changes WHERE id > ? ORDER BY id ASC LIMIT ?",
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
# 数据导出端点（CSV / Excel）
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/export/{kind}", tags=["results"])
def export_data(
    kind: str,
    fmt: str = Query("csv", description="csv | xlsx"),
    task_id: Optional[int] = Query(None, description="按任务过滤（可选）"),
    _key: str = Depends(require_api_key),
):
    """导出 products / listings 为 CSV 或 Excel 文件下载。"""
    if kind not in ("products", "listings"):
        raise HTTPException(status_code=400, detail="kind 必须是 products | listings")
    if fmt not in ("csv", "xlsx"):
        raise HTTPException(status_code=400, detail="fmt 必须是 csv | xlsx")

    with get_conn() as conn:
        if task_id is not None:
            rows = conn.execute(
                f"SELECT * FROM {kind} WHERE task_id=? ORDER BY id ASC", (task_id,)
            ).fetchall()
        else:
            rows = conn.execute(f"SELECT * FROM {kind} ORDER BY id ASC").fetchall()

    # 中文列定义（顺序/名称在 app/export_schema.py 调整）
    from app.export_schema import COLUMNS_BY_KIND, render_cell
    col_defs = COLUMNS_BY_KIND[kind]          # [(db_col, 中文表头), ...]
    headers = [label for _, label in col_defs]

    dict_rows = [dict(r) for r in rows]
    # 文件名用任务 code（task{id}_{日期}_{时间}），如 products_task1_20260603_111918.csv
    if task_id is not None:
        task = get_task(task_id)
        suffix = "_" + (task["code"] if task else f"task{task_id}")
    else:
        suffix = "_all"
    fname = f"{kind}{suffix}.{fmt}"

    def row_values(r: dict) -> list:
        return [render_cell(col, r.get(col)) for col, _ in col_defs]

    if fmt == "csv":
        import csv
        import io
        buf = io.StringIO()
        buf.write("﻿")  # BOM，让 Excel 正确识别 UTF-8 中文
        writer = csv.writer(buf)
        writer.writerow(headers)
        for r in dict_rows:
            writer.writerow(row_values(r))
        from fastapi.responses import Response
        return Response(
            content=buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    # xlsx
    import io
    from openpyxl import Workbook
    from fastapi.responses import Response
    wb = Workbook()
    ws = wb.active
    ws.title = kind
    ws.append(headers)
    for r in dict_rows:
        ws.append(row_values(r))
    bio = io.BytesIO()
    wb.save(bio)
    return Response(
        content=bio.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


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


# ─────────────────────────────────────────────────────────────────────────────
# 指标端点（M6）
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/metrics", tags=["metrics"])
def get_metrics(_key: str = Depends(require_api_key)):
    """返回全局采集指标聚合数据。

    计算字段（由 metrics 表 + proxy_log 表聚合）：
      - total_requests   : 总请求次数
      - total_success    : 成功次数（200 且解析 ok）
      - total_429        : HTTP 429（waiting-room）次数
      - total_blocked    : 封控次数（含 403/验证码等）
      - total_products   : 成功入库商品数
      - total_ip_used    : 累计使用过的 IP 数
      - success_rate     : 成功率（0~1，total_requests=0 时为 null）
      - rate_429         : 429 比率（0~1）
      - blocked_rate     : 封控比率（0~1）
      - avg_yield_per_ip : 每IP平均产出商品数（来自 proxy_log yield 聚合）
    """
    with get_conn() as conn:
        # 从 metrics 表取累积计数器
        row = conn.execute(
            "SELECT * FROM metrics WHERE id = 1"
        ).fetchone()

        # 从 proxy_log 聚合：每IP产出（yield 事件的 count 字段求平均）
        yield_row = conn.execute(
            "SELECT COUNT(*) AS yield_count, SUM(count) AS yield_total"
            " FROM proxy_log WHERE event = 'yield'"
        ).fetchone()

    if row is None:
        # metrics 行不存在（极端情况：库刚建未被写入）
        total_requests = 0
        total_success = 0
        total_429 = 0
        total_blocked = 0
        total_products = 0
        total_ip_used = 0
        updated_at = None
    else:
        total_requests = row["total_requests"]
        total_success  = row["total_success"]
        total_429      = row["total_429"]
        total_blocked  = row["total_blocked"]
        total_products = row["total_products"]
        total_ip_used  = row["total_ip_used"]
        updated_at     = row["updated_at"]

    # 计算比率（分母为 0 时返回 None）
    def safe_rate(numerator: int, denominator: int):
        if denominator <= 0:
            return None
        return round(numerator / denominator, 4)

    # 每IP平均产出：yield_count > 0 时才计算
    yield_count = yield_row["yield_count"] if yield_row else 0
    yield_total = yield_row["yield_total"] if yield_row else 0
    if yield_count and yield_count > 0 and yield_total:
        avg_yield_per_ip = round(yield_total / yield_count, 2)
    else:
        avg_yield_per_ip = None

    return {
        "total_requests": total_requests,
        "total_success":  total_success,
        "total_429":      total_429,
        "total_blocked":  total_blocked,
        "total_products": total_products,
        "total_ip_used":  total_ip_used,
        "success_rate":   safe_rate(total_success, total_requests),
        "rate_429":       safe_rate(total_429, total_requests),
        "blocked_rate":   safe_rate(total_blocked, total_requests),
        "avg_yield_per_ip": avg_yield_per_ip,
        "updated_at":     updated_at,
    }
