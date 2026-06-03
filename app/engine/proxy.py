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

# proxy_state.json 固定存到项目根目录（app/engine/ 上两层）
_PROJECT_ROOT = Path(__file__).parent.parent.parent
STATE_FILE = str(_PROJECT_ROOT / "proxy_state.json")

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
    """持有一个当前 IP，复用到失效才换；IP 状态跨进程持久化。低并发下线程安全。"""

    def __init__(self, api_key: str = "",
                 pace_min: float = MIN_DELAY,
                 pace_max: float = MAX_DELAY,
                 ip_max_age_min: int = 690,
                 auto_rotate: bool = False) -> None:
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

    # ── 持久化 ──────────────────────────────────────────────────
    def _load_state(self) -> bool:
        """从磁盘载入上次的 IP。返回 True 表示载入了一个仍在有效期内的 IP。"""
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
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
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
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
        """被封/失败时强制提取**新** IP，并覆盖磁盘缓存（不能复用刚失败的 IP）。"""
        with self._lock:
            logger.warning("轮换 IP（%s），上个 IP 复用了 %d 次", reason, self._uses)
            self._proxy = self._extract()   # 直接提新，绕过磁盘缓存
            self._born_at = time.time()
            self._uses = 0
            self._save_state()              # 覆盖掉被封的 IP
        time.sleep(BLOCK_BACKOFF)
        return self._proxy

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
        """请求前调用：保证两次请求间有温和的随机间隔。"""
        wait = random.uniform(self._pace_min, self._pace_max)
        elapsed = time.time() - self._last_req_at
        if self._last_req_at and elapsed < wait:
            time.sleep(wait - elapsed)
        self._last_req_at = time.time()
        self._uses += 1

    @property
    def stats(self) -> dict:
        return {"extractions": self._extractions, "uses_on_current": self._uses}
