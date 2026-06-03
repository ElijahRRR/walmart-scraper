"""Lane 池 —— 每条 lane 持有一个独立 IP，串行+限速执行采集任务。

架构决定（2026-06）：
  - 默认关闭自动换IP（封控时停下报警，由人工手动换）
  - 封控时把对应 lane 标记为 blocked，停止该 lane 的采集
  - 提供 resume_lane(lane_id) 入口：人工换IP后恢复 lane
  - proxy_log 落库：提取IP / 封控 / 每IP产出商品数

使用方式：
    from app.service.lanes import LanePool, get_lane_pool
    pool = get_lane_pool()          # 单例，全局复用
    task_id = pool.submit_ids(ids)  # 提交到空闲 lane
"""
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ── proxy_log 落库辅助 ─────────────────────────────────────────────────────────

def _log_proxy_event(ip: str, event: str, count: int = 0, detail: str = "") -> None:
    """写一条 proxy_log 记录（extract / block / yield）。

    设计上做了防御：DB 未初始化或写失败都只记 warning，不影响采集主流程。
    """
    try:
        from app.db import get_conn
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO proxy_log(ip, event, count, detail) VALUES(?, ?, ?, ?)",
                (ip, event, count, detail),
            )
    except Exception as exc:
        logger.warning("proxy_log 写入失败（不影响采集）：%s", exc)


# ── Lane 状态 ─────────────────────────────────────────────────────────────────

class LaneState(str, Enum):
    IDLE    = "idle"     # 空闲，可接任务
    RUNNING = "running"  # 正在采集
    BLOCKED = "blocked"  # 命中封控，等待人工处理
    STOPPED = "stopped"  # 已停止（不再接任务）


@dataclass
class LaneStatus:
    """供外部查询的 lane 状态快照。"""
    lane_id: int
    state: str
    current_ip: Optional[str]       # ip:port（无账密）
    ip_born_at: float               # IP 提取时的 Unix 时间戳
    ip_age_sec: float               # 本IP已存活秒数
    ip_uses: int                    # 本IP已用次数（产出计数）
    last_block: Optional[str]       # 最近一次封控原因
    last_block_at: float            # 最近封控时间戳（0=从未）
    total_products: int             # 本 lane 累计写入商品数


# ── Lane ──────────────────────────────────────────────────────────────────────

class Lane:
    """单条 lane：持有独立 ProxyPool，串行执行分配给它的工作单元（callable）。

    特性：
      - 封控时置 state=BLOCKED，不自动换IP，记日志，停止当前任务
      - resume(new_proxy_url) 恢复：换新IP后重置状态为 IDLE
      - 所有公开方法线程安全
    """

    def __init__(self, lane_id: int, api_key: str,
                 pace_min: float = 3.0, pace_max: float = 7.0,
                 ip_max_age_min: int = 690) -> None:
        from app.engine.proxy import ProxyPool
        self.lane_id = lane_id
        # 每 lane 独立 ProxyPool（独立IP，独立状态文件由内存持有）
        self._pool = ProxyPool(
            api_key=api_key,
            pace_min=pace_min,
            pace_max=pace_max,
            ip_max_age_min=ip_max_age_min,
            auto_rotate=False,   # lane 层面关自动换IP
        )
        self._state = LaneState.IDLE
        self._lock = threading.Lock()
        self._last_block: Optional[str] = None
        self._last_block_at: float = 0.0
        self._total_products: int = 0   # 累计产出商品数（本 lane 生命周期）
        # 当前IP的产出计数（用于 proxy_log yield 事件）
        self._ip_product_count: int = 0

    # ── 状态查询 ────────────────────────────────────────────────────────────

    @property
    def state(self) -> LaneState:
        return self._state

    def get_status(self) -> LaneStatus:
        """返回当前 lane 的状态快照（线程安全）。"""
        with self._lock:
            ps = self._pool.get_status()
            return LaneStatus(
                lane_id=self.lane_id,
                state=self._state.value,
                current_ip=ps.get("proxy"),
                ip_born_at=ps.get("born_at", 0),
                ip_age_sec=ps.get("age_sec", 0.0),
                ip_uses=ps.get("uses", 0),
                last_block=self._last_block,
                last_block_at=self._last_block_at,
                total_products=self._total_products,
            )

    # ── 封控通知（由 collector 回调 or lane 内部检测） ───────────────────────

    def notify_blocked(self, reason: str) -> None:
        """通知该 lane 发生封控：置 BLOCKED 状态，写 proxy_log，不换IP。"""
        with self._lock:
            if self._state == LaneState.BLOCKED:
                return  # 已经是封控状态，忽略重复通知
            self._state = LaneState.BLOCKED
            self._last_block = reason
            self._last_block_at = time.time()
            ps = self._pool.get_status()
            ip = ps.get("proxy") or "unknown"
            logger.warning(
                "[lane %d] 命中封控 → BLOCKED（不换IP）原因: %s  IP: %s",
                self.lane_id, reason, ip
            )
            # 记录封控事件到 proxy_log
            _log_proxy_event(ip, "block", count=self._ip_product_count, detail=reason)

    def notify_product_saved(self) -> None:
        """每次成功写入一个商品时通知 lane，更新产出计数。"""
        with self._lock:
            self._total_products += 1
            self._ip_product_count += 1

    # ── 恢复 lane（人工换IP后调用） ─────────────────────────────────────────

    def resume(self) -> str:
        """人工操作：提取新IP，重置 lane 状态为 IDLE。

        调用方需事先确认新IP可用（例如在 API 端点里触发）。
        Returns: 新IP的 ip:port 字符串（隐去账密）
        """
        with self._lock:
            # 先记录旧IP的产出（yield 事件）
            ps = self._pool.get_status()
            old_ip = ps.get("proxy") or "unknown"
            if self._ip_product_count > 0 or self._state == LaneState.BLOCKED:
                _log_proxy_event(
                    old_ip, "yield",
                    count=self._ip_product_count,
                    detail=f"手动恢复，上一封控原因: {self._last_block or '无'}"
                )

            # 强制提取新IP（rotate）
            new_proxy = self._pool.rotate(f"lane {self.lane_id} 手动恢复")
            # 从 pool.get_status 拿 ip:port（隐去账密）
            new_ps = self._pool.get_status()
            new_ip_display = new_ps.get("proxy") or new_proxy.split("@")[-1]

            # 记录新IP提取事件
            _log_proxy_event(new_ip_display, "extract", count=0,
                             detail=f"lane {self.lane_id} 手动恢复")

            # 重置状态
            self._ip_product_count = 0
            self._last_block = None
            self._last_block_at = 0.0
            self._state = LaneState.IDLE

            logger.info("[lane %d] 已恢复，新IP: %s", self.lane_id, new_ip_display)
            return new_ip_display

    # ── 确保有 IP（首次或 IP 过期时提取）─────────────────────────────────────

    def ensure_ip(self) -> Optional[str]:
        """确保 lane 有有效IP，并记录 extract 事件（仅在真正提取新IP时）。

        Returns: ip:port 或 None（无 API_KEY 时会抛异常，上层捕获）
        """
        with self._lock:
            before = self._pool.get_status()
            was_alive = before.get("alive", False)
            old_ip = before.get("proxy")

        # 在锁外调用（可能有网络请求）
        current_proxy = self._pool.current()  # 内部加锁，安全

        with self._lock:
            after = self._pool.get_status()
            new_ip = after.get("proxy")

            # 如果IP发生了变化（或从无到有），记 extract 事件
            if new_ip and new_ip != old_ip:
                _log_proxy_event(new_ip, "extract", count=0,
                                 detail=f"lane {self.lane_id} 初始化/IP更新")
                self._ip_product_count = 0  # 新IP产出归零

            return new_ip

    # ── 执行采集工作单元 ─────────────────────────────────────────────────────

    def run_work(self, work_fn: Callable[[], None]) -> bool:
        """在当前 lane 同步执行 work_fn。

        work_fn 应该是一个无参 callable，它会直接使用 self._pool 采集。
        如果 lane 处于 BLOCKED 状态，立即返回 False，不执行。

        Returns: True=正常完成，False=blocked 或异常
        """
        with self._lock:
            if self._state == LaneState.BLOCKED:
                logger.warning("[lane %d] 处于 BLOCKED 状态，拒绝执行任务", self.lane_id)
                return False
            if self._state == LaneState.STOPPED:
                return False
            self._state = LaneState.RUNNING

        try:
            work_fn()
            with self._lock:
                if self._state == LaneState.RUNNING:
                    self._state = LaneState.IDLE
            return True
        except Exception as exc:
            logger.exception("[lane %d] 执行异常: %s", self.lane_id, exc)
            with self._lock:
                if self._state == LaneState.RUNNING:
                    self._state = LaneState.IDLE
            return False

    @property
    def pool(self):
        """暴露底层 ProxyPool，供采集器使用。"""
        return self._pool


# ── LanePool ──────────────────────────────────────────────────────────────────

class LanePool:
    """N 条 lane 的管理器。

    功能：
      - 分配空闲 lane 给采集任务
      - 提供每 lane 的状态查询
      - 提供人工恢复 blocked lane 的入口
    """

    def __init__(self, n_lanes: int = 1, api_key: str = "",
                 pace_min: float = 3.0, pace_max: float = 7.0,
                 ip_max_age_min: int = 690) -> None:
        self._lanes: list[Lane] = [
            Lane(i, api_key=api_key,
                 pace_min=pace_min, pace_max=pace_max,
                 ip_max_age_min=ip_max_age_min)
            for i in range(n_lanes)
        ]
        self._lock = threading.Lock()
        logger.info("LanePool 初始化：%d 条 lane", n_lanes)

    # ── 状态查询 ────────────────────────────────────────────────────────────

    def get_all_status(self) -> list[dict]:
        """返回所有 lane 的状态列表（dict 格式，供 API 序列化）。"""
        result = []
        for lane in self._lanes:
            s = lane.get_status()
            result.append({
                "lane_id": s.lane_id,
                "state": s.state,
                "current_ip": s.current_ip,
                "ip_born_at": s.ip_born_at,
                "ip_age_sec": s.ip_age_sec,
                "ip_uses": s.ip_uses,
                "last_block": s.last_block,
                "last_block_at": s.last_block_at,
                "total_products": s.total_products,
            })
        return result

    def get_lane_status(self, lane_id: int) -> Optional[dict]:
        """返回指定 lane 的状态，lane_id 不存在返回 None。"""
        if lane_id < 0 or lane_id >= len(self._lanes):
            return None
        s = self._lanes[lane_id].get_status()
        return {
            "lane_id": s.lane_id,
            "state": s.state,
            "current_ip": s.current_ip,
            "ip_born_at": s.ip_born_at,
            "ip_age_sec": s.ip_age_sec,
            "ip_uses": s.ip_uses,
            "last_block": s.last_block,
            "last_block_at": s.last_block_at,
            "total_products": s.total_products,
        }

    # ── 恢复 blocked lane ───────────────────────────────────────────────────

    def resume_lane(self, lane_id: int) -> Optional[str]:
        """人工操作：提取新IP并恢复指定 lane。

        Returns: 新IP的 ip:port，lane_id 非法时返回 None。
        """
        if lane_id < 0 or lane_id >= len(self._lanes):
            return None
        return self._lanes[lane_id].resume()

    # ── 获取空闲 lane ────────────────────────────────────────────────────────

    def get_idle_lane(self) -> Optional[Lane]:
        """返回第一个空闲的 lane，无空闲时返回 None。"""
        with self._lock:
            for lane in self._lanes:
                if lane.state == LaneState.IDLE:
                    return lane
        return None

    # ── 直接访问 lane 列表（供 runner 层使用）────────────────────────────────

    @property
    def lanes(self) -> list[Lane]:
        return self._lanes

    @property
    def n_lanes(self) -> int:
        return len(self._lanes)

    def lane(self, idx: int) -> Lane:
        """按索引获取 lane。"""
        return self._lanes[idx]


# ── 全局单例 ────────────────────────────────────────────────────────────────

_global_pool: Optional[LanePool] = None
_pool_lock = threading.Lock()


def get_lane_pool() -> LanePool:
    """获取全局 LanePool 单例（懒初始化，从 config 读取参数）。"""
    global _global_pool
    if _global_pool is not None:
        return _global_pool
    with _pool_lock:
        if _global_pool is not None:
            return _global_pool
        from app import config
        _global_pool = LanePool(
            n_lanes=config.LANES,
            api_key=config.PROXY_API_KEY,
            pace_min=config.PACE_MIN,
            pace_max=config.PACE_MAX,
            ip_max_age_min=config.IP_MAX_AGE_MIN,
        )
        return _global_pool


def reset_lane_pool() -> None:
    """重置全局单例（测试用途）。"""
    global _global_pool
    with _pool_lock:
        _global_pool = None
