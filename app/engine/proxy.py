"""cliproxy 短效 IP 管理器 —— 省成本 + 防封控的核心。

策略（与亚马逊 v3 的"每请求换 IP"相反）：
  - 一个 IP 提取后**反复复用**，直到：被封控 / 请求失败 / 接近 12h 真实上限；
  - **跨进程持久化**：IP 存到磁盘 state 文件，下次 `python probe.py` 复用同一个，
    而不是每次运行都重提（脚本非常驻，否则寿命全浪费）；
  - 复用期间加随机延迟、低并发，节奏比 v3 温和，避免把 IP 用封；
  - 被封时才 rotate（重提一个），并退避一会儿。

cliproxy: 每次提取=1 个短效 IP（无限会话, HTTP(s)/Socks5, 在线 2-12h, 5Mbps）。
"""
import json
import logging
import os
import random
import threading
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# proxy_state.json 默认存到项目根目录（app/engine/ 上两层）
# 多 lane 场景下由 ProxyPool.__init__ 按 lane_id 区分文件名（P2-7）
_PROJECT_ROOT = Path(__file__).parent.parent.parent
STATE_FILE = str(_PROJECT_ROOT / "proxy_state.json")  # 单 lane 兼容默认值

# 温和节奏：每次请求后在区间内随机停顿（秒），避免行为指纹
MIN_DELAY, MAX_DELAY = 3.0, 7.0
# 安全上限：cliproxy IP 最长存活 12h，留 30min 余量防被服务端掐线时还在用
MAX_IP_AGE = (12 * 60 - 30) * 60
# 被封后退避时长（秒）
BLOCK_BACKOFF = 15.0


def _build_proxy_api(key: str) -> str:
    """根据 key 构造 cliproxy 提取 URL。"""
    return (
        f"https://webipapi.cliproxy.com/api/getIpInfo"
        f"?key={key}&port=443&num=1&country=US&state=&type=2"
    )


class ProxyPool:
    """持有一个当前 IP，复用到失效才换；IP 状态跨进程持久化。低并发下线程安全。

    构造参数（P2-7 新增 state_file）：
      api_key       — cliproxy 提取 key
      pace_min/max  — 请求间隔随机范围（秒）
      ip_max_age_min — 单 IP 安全上限（分钟）
      auto_rotate   — 封控时是否自动换IP（默认 False，由 Lane 层控制）
      state_file    — 持久化文件路径；None 则使用模块级默认 STATE_FILE。
                      多 lane 场景由 Lane 创建时传入 proxy_state_{lane_id}.json，
                      避免各 lane 读写同一文件互相污染（P2-7）。
    """

    def __init__(self, api_key: str = "",
                 pace_min: float = MIN_DELAY,
                 pace_max: float = MAX_DELAY,
                 ip_max_age_min: int = 690,
                 auto_rotate: bool = False,
                 state_file: str | None = None) -> None:
        self._proxy: str | None = None
        self._born_at: float = 0.0
        self._uses: int = 0
        self._extractions: int = 0   # 本进程内提取次数 = 本次运行成本
        self._last_req_at: float = 0.0
        self._lock = threading.Lock()
        # 可配参数
        self._api_key = api_key
        self._pace_min = pace_min
        self._pace_max = pace_max
        self._max_ip_age = ip_max_age_min * 60  # 转换为秒
        self._auto_rotate = auto_rotate
        # P2-7：按 lane 区分 state 文件，默认 None 时使用模块级 STATE_FILE
        self._state_file: str = state_file if state_file is not None else STATE_FILE

    # ── 持久化 ──────────────────────────────────────────────────
    def _load_state(self) -> bool:
        """从磁盘载入上次的 IP。返回 True 表示载入了一个仍在有效期内的 IP。
        使用 self._state_file（多 lane 下每 lane 独立文件，避免 IP 跨 lane 污染）。
        """
        try:
            with open(self._state_file, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, json.JSONDecodeError):
            return False
        born = st.get("born_at", 0)
        if st.get("proxy") and (time.time() - born) < self._max_ip_age:
            self._proxy, self._born_at = st["proxy"], born
            age_min = int((time.time() - born) / 60)
            logger.info("复用磁盘缓存 IP（已存活 %d 分钟）: %s",
                        age_min, self._proxy.split("@")[-1])
            return True
        return False

    def _save_state(self) -> None:
        # 使用 self._state_file，多 lane 下各自写独立文件，不互相覆盖（P2-7）
        try:
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump({"proxy": self._proxy, "born_at": self._born_at}, f)
        except OSError:
            logger.warning("代理 state 写盘失败（不影响本次运行）")

    # ── 提取 / 轮换 ──────────────────────────────────────────────
    def _extract(self) -> str:
        api_url = _build_proxy_api(self._api_key)
        line = requests.get(api_url, timeout=20).text.strip()
        ip, port, user, pwd = line.split(":")
        self._extractions += 1
        logger.info("提取新 IP（本次运行第 %d 个）: %s:%s", self._extractions, ip, port)
        return f"http://{user}:{pwd}@{ip}:{port}"

    def current(self) -> str:
        """返回当前可用代理：优先内存 → 磁盘缓存 → 重新提取。"""
        with self._lock:
            aged = self._proxy and (time.time() - self._born_at) > self._max_ip_age
            if self._proxy and not aged:
                return self._proxy
            if not aged and self._load_state():   # 跨进程复用
                return self._proxy
            if aged:
                logger.info("IP 接近上限，主动轮换")
            self._proxy = self._extract()
            self._born_at = time.time()
            self._uses = 0
            self._save_state()
            return self._proxy

    def rotate(self, reason: str = "") -> str:
        """被封/失败时强制提取**新** IP，并覆盖磁盘缓存（不能复用刚失败的 IP）。

        P2-8 修复：sleep 移到锁外执行。
        策略：在锁内完成 IP 提取和状态更新，保存新 proxy 引用到局部变量，
        然后释放锁，再 sleep 退避——确保 sleep 期间其他线程可以调
        get_status()/notify_blocked() 等，不被 15s 阻塞。
        """
        with self._lock:
            logger.warning("轮换 IP（%s），上个 IP 复用了 %d 次", reason, self._uses)
            self._proxy = self._extract()   # 直接提新，绕过磁盘缓存
            self._born_at = time.time()
            self._uses = 0
            self._save_state()              # 覆盖掉被封的 IP
            new_proxy = self._proxy         # 局部保存，锁外返回
        # 退避 sleep 在锁外执行，get_status/notify_blocked 不被阻塞（P2-8）
        time.sleep(BLOCK_BACKOFF)
        return new_proxy

    def get_status(self) -> dict:
        """返回当前 IP 状态（供 API 展示）。

        字段说明：
          proxy      — ip:port（账密已隐去），无IP时为 None
          born_at    — IP 提取时的 Unix 时间戳（float），无IP时为 0
          age_sec    — 当前IP已存活秒数（float）
          age_min    — 当前IP已存活分钟数（保留1位小数）
          uses       — 本IP已使用（请求）次数（即本IP产出计数）
          alive      — 是否在安全有效期内
          max_age_min — 配置的IP安全上限（分钟）
          auto_rotate — 自动换IP开关状态
        """
        with self._lock:
            if not self._proxy:
                return {
                    "proxy": None,
                    "born_at": 0,
                    "age_sec": 0.0,
                    "age_min": 0.0,
                    "uses": 0,
                    "alive": False,
                    "max_age_min": self._max_ip_age // 60,
                    "auto_rotate": self._auto_rotate,
                }
            age_sec = time.time() - self._born_at
            return {
                "proxy": self._proxy.split("@")[-1],   # 只暴露 ip:port，隐去账密
                "born_at": self._born_at,
                "age_sec": round(age_sec, 1),
                "age_min": round(age_sec / 60, 1),
                "uses": self._uses,
                "alive": age_sec < self._max_ip_age,
                "max_age_min": self._max_ip_age // 60,
                "auto_rotate": self._auto_rotate,
            }

    # ── 节奏控制 ────────────────────────────────────────────────
    def pace(self) -> None:
        """请求前调用：保证两次请求间有温和的随机间隔。

        P1-3 修复：_last_req_at / _uses 的读写在锁内原子完成，sleep 放锁外。
        策略：
          1. 持锁：读 _last_req_at，计算需等待时长，立即更新 _last_req_at 和 _uses
             （预占时间戳，防止并发请求同时通过节速检查）。
          2. 释锁：执行实际 sleep（避免串行化所有等待，其他线程 get_status 不被阻塞）。
        这样两个并发调用者会得到各自错开的 _last_req_at，从而错开发出请求。
        """
        wait = random.uniform(self._pace_min, self._pace_max)
        with self._lock:
            now = time.time()
            elapsed = now - self._last_req_at
            sleep_time = (wait - elapsed) if self._last_req_at and elapsed < wait else 0.0
            # 预占时间戳：加上本次预期等待时长，下一个调用者会在此基础上再等
            self._last_req_at = now + sleep_time
            self._uses += 1
        # sleep 在锁外，不阻塞 get_status/notify_blocked（P1-3 + P2-8 同策略）
        if sleep_time > 0:
            time.sleep(sleep_time)

    @property
    def stats(self) -> dict:
        return {"extractions": self._extractions, "uses_on_current": self._uses}
