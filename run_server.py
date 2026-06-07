"""启动脚本：用 uvicorn 启动 Walmart 采集服务。

使用方式：
    python3 run_server.py
    # 或直接用 uvicorn：
    uvicorn app.api:app --host 0.0.0.0 --port 3000 --reload
    # 端口由 .env 的 PORT 决定（默认 3000，与 DMIT 一致）
"""
import logging

import uvicorn

# 延迟导入 config，确保 .env 已加载
from app.config import PORT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

if __name__ == "__main__":
    uvicorn.run(
        "app.api:app",
        host="0.0.0.0",
        port=PORT,
        reload=False,           # 生产用 False；开发时可改 True
        log_level="info",
    )
