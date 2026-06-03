# 沃尔玛采集服务 — Dockerfile
# 构建：docker build -t walmart-scraper .
# 运行：docker run -d --env-file .env -p 8900:8900 -v $(pwd)/data:/app/data walmart-scraper

FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 先复制依赖文件，利用 Docker 层缓存
COPY requirements.txt .

# 安装依赖（--no-cache-dir 减小镜像体积）
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码
COPY . .

# 数据目录（SQLite 数据库）挂载点
VOLUME ["/app/data"]

# 服务端口（与 config.PORT / .env PORT 一致）
EXPOSE 8900

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8900/health')"

# 启动命令
CMD ["python3", "run_server.py"]
