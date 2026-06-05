"""配置模块：从环境变量（.env）读取所有服务参数，提供统一的 settings 对象。

优先级：环境变量 > .env 文件 > 内置缺省值。
"""
import logging
import os
from pathlib import Path

_cfg_logger = logging.getLogger(__name__)

# 尝试加载项目根目录的 .env 文件（可选依赖 python-dotenv；不存在则静默跳过）
_ROOT = Path(__file__).parent.parent  # 项目根目录

def _load_dotenv() -> None:
    """简单的 .env 文件加载器（不依赖 python-dotenv）。
    已设置的环境变量不会被覆盖（export 优先）。
    仅加载 .env，不 fallback 到 .env.example——示例文件只含占位符，
    回退读取会静默使用无效 key，且会把旧泄漏 key 带入运行时。
    """
    env_file = _ROOT / ".env"
    if not env_file.exists():
        return
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            # 去掉行内注释（# 之后的部分，且 # 前有空格）
            if " #" in val:
                val = val[:val.index(" #")].strip()
            # 已有环境变量不覆盖
            if key not in os.environ:
                os.environ[key] = val

_load_dotenv()


def _get(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _get_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _get_int(key: str, default: int) -> int:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return int(val.strip())
    except ValueError:
        return default


def _get_float(key: str, default: float) -> float:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return float(val.strip())
    except ValueError:
        return default


# ── 代理设置 ────────────────────────────────────────────────────────────────
PROXY_API_KEY: str = _get("PROXY_API_KEY", "")
"""cliproxy 提取 key，空字符串表示未配置（测试/本地可不填）"""

# ── 采集节奏（秒） ──────────────────────────────────────────────────────────
PACE_MIN: float = _get_float("PACE_MIN", 3.0)
"""最短请求间隔（秒）"""

PACE_MAX: float = _get_float("PACE_MAX", 7.0)
"""最长请求间隔（秒）"""

# ── Lane 并发设置 ────────────────────────────────────────────────────────────
LANES: int = _get_int("LANES", 1)
"""lane 数量（= 同时持有的 IP 数）；每 lane 串行采集"""

AUTO_ROTATE: bool = _get_bool("AUTO_ROTATE", False)
"""自动换 IP 开关：False=封控停下报警（默认），True=封控自动轮换"""

IP_MAX_AGE_MIN: int = _get_int("IP_MAX_AGE_MIN", 690)
"""单 IP 安全上限（分钟），默认 690 分钟（11.5h，留余量防 cliproxy 12h 掐线）"""

# ── API 鉴权 ─────────────────────────────────────────────────────────────────
_DEFAULT_API_KEY = "dev-key-change-me"
API_KEY: str = _get("API_KEY", _DEFAULT_API_KEY)
"""服务端 API Key（请求头 X-API-Key）；生产环境务必改掉"""

REQUIRE_API_KEY: bool = _get_bool("REQUIRE_API_KEY", True)
"""是否启用 X-API-Key 鉴权。True=默认，校验请求头；
False=完全关闭鉴权（所有端点免 key 访问），仅限可信内网环境。"""

if not REQUIRE_API_KEY:
    # 鉴权已关闭：所有端点对任何能访问到服务的人开放
    _cfg_logger.warning(
        "[安全告警] REQUIRE_API_KEY=false，已关闭 X-API-Key 鉴权，"
        "所有端点无需 key 即可访问。请仅在可信内网环境使用。"
    )
# 启动告警：鉴权开启但仍在用默认 key，任何知道该公开默认值的人都可调用全部受保护端点
elif API_KEY == _DEFAULT_API_KEY:
    _cfg_logger.warning(
        "[安全告警] API_KEY 使用默认占位值 %r，任何知道该值的人均可访问全部端点。"
        "请在 .env 中设置随机强密码后重启服务。",
        _DEFAULT_API_KEY,
    )

# ── 服务设置 ─────────────────────────────────────────────────────────────────
PORT: int = _get_int("PORT", 8900)
"""HTTP 服务端口"""

DB_PATH: str = _get("DB_PATH", "data/walmart.db")
"""SQLite 数据库路径（相对项目根目录，或绝对路径）"""

# ── webhook 回调（M4） ────────────────────────────────────────────────────────
WEBHOOK_URL: str = _get("WEBHOOK_URL", "")
"""任务完成回调 URL；空字符串表示关闭（默认关）。
可在 .env 设置：WEBHOOK_URL=https://your-server/callback"""

# ── 重试设置（M4） ─────────────────────────────────────────────────────────
RETRY_MAX: int = _get_int("RETRY_MAX", 2)
"""单个 item 瞬时失败最大重试次数（默认 2，封控不重试）"""


# ── 统一 settings 对象（字典形式，方便序列化和 FastAPI 依赖注入） ──────────────
settings: dict = {
    "proxy_api_key": PROXY_API_KEY,
    "pace_min": PACE_MIN,
    "pace_max": PACE_MAX,
    "lanes": LANES,
    "auto_rotate": AUTO_ROTATE,
    "ip_max_age_min": IP_MAX_AGE_MIN,
    "api_key": API_KEY,
    "require_api_key": REQUIRE_API_KEY,
    "port": PORT,
    "db_path": DB_PATH,
    "webhook_url": WEBHOOK_URL,
    "retry_max": RETRY_MAX,
}
