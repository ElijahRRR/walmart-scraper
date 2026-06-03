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

import requests

logger = logging.getLogger(__name__)

PROXY_API = ("https://webipapi.cliproxy.com/api/getIpInfo"
             "?key=your_cliproxy_key_here&port=443&num=1&country=US&state=&type=2")

# 跨进程复用：当前 IP 持久化到此文件（与脚本同目录）
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy_state.json")
# 温和节奏：每次请求后在区间内随机停顿（秒），避免行为指纹
MIN_DELAY, MAX_DELAY = 3.0, 7.0
# 安全上限：cliproxy IP 最长存活 12h，留 30min 余量防被服务端掐线时还在用
MAX_IP_AGE = (12 * 60 - 30) * 60
# 被封后退避时长（秒）
BLOCK_BACKOFF = 15.0


class ProxyPool:
    """持有一个当前 IP，复用到失效才换；IP 状态跨进程持久化。低并发下线程安全。"""

    def __init__(self) -> None:
        self._proxy: str | None = None
        self._born_at: float = 0.0
        self._uses: int = 0
        self._extractions: int = 0   # 本进程内提取次数 = 本次运行成本
        self._last_req_at: float = 0.0
        self._lock = threading.Lock()

    # ── 持久化 ──────────────────────────────────────────────────
    def _load_state(self) -> bool:
        """从磁盘载入上次的 IP。返回 True 表示载入了一个仍在有效期内的 IP。"""
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, json.JSONDecodeError):
            return False
        born = st.get("born_at", 0)
        if st.get("proxy") and (time.time() - born) < MAX_IP_AGE:
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
        line = requests.get(PROXY_API, timeout=20).text.strip()
        ip, port, user, pwd = line.split(":")
        self._extractions += 1
        logger.info("提取新 IP（本次运行第 %d 个）: %s:%s", self._extractions, ip, port)
        return f"http://{user}:{pwd}@{ip}:{port}"

    def current(self) -> str:
        """返回当前可用代理：优先内存 → 磁盘缓存 → 重新提取。"""
        with self._lock:
            aged = self._proxy and (time.time() - self._born_at) > MAX_IP_AGE
            if self._proxy and not aged:
                return self._proxy
            if not aged and self._load_state():   # 跨进程复用
                return self._proxy
            if aged:
                logger.info("IP 接近 12h 上限，主动轮换")
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

    # ── 节奏控制 ────────────────────────────────────────────────
    def pace(self) -> None:
        """请求前调用：保证两次请求间有温和的随机间隔。"""
        wait = random.uniform(MIN_DELAY, MAX_DELAY)
        elapsed = time.time() - self._last_req_at
        if self._last_req_at and elapsed < wait:
            time.sleep(wait - elapsed)
        self._last_req_at = time.time()
        self._uses += 1

    @property
    def stats(self) -> dict:
        return {"extractions": self._extractions, "uses_on_current": self._uses}
