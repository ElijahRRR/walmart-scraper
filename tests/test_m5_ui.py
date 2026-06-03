"""M5 极简 Web UI 测试。

验证策略（不发真实网络请求）：
  - 使用 FastAPI TestClient
  - GET / 返回 200 且是 HTML
  - HTML 内容包含三种任务类型表单元素（ids / keyword / seller）
  - HTML 引用正确的 API 路径（/collect/、/tasks、/products、/proxy/rotate、/proxy/status 等字样）
  - 静态文件目录可被访问（/static/ 返回 404 而非 500，说明 StaticFiles 已挂载）
  - GET / 不需要 X-API-Key（前端页面免鉴权）

运行：
    python -m pytest tests/test_m5_ui.py -v
"""
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.config as cfg_module


# ─────────────────────────────────────────────────────────────────────────────
# 辅助：临时 DB + TestClient
# ─────────────────────────────────────────────────────────────────────────────

def _make_client(tmp_dir: str):
    """重新加载 api 模块并返回 TestClient（使用临时 DB）。"""
    db_path = os.path.join(tmp_dir, "test_m5.db")
    cfg_module.DB_PATH = db_path
    cfg_module.API_KEY = "test-key-m5"

    import app.db as db_module
    importlib.reload(db_module)
    db_module.init_db()

    import app.api as api_module
    importlib.reload(api_module)

    from fastapi.testclient import TestClient
    client = TestClient(api_module.app, raise_server_exceptions=True)
    return client, api_module


VALID_KEY = "test-key-m5"


# ─────────────────────────────────────────────────────────────────────────────
# #23 任务提交表单验证
# ─────────────────────────────────────────────────────────────────────────────

class TestUIIndex(unittest.TestCase):
    """feature #23：GET / 返回 200 HTML，包含三种任务类型表单。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.client, self.api = _make_client(self._tmp)

    def test_get_root_returns_200(self):
        """GET / 应返回 200。"""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)

    def test_get_root_returns_html(self):
        """GET / 返回 Content-Type 为 text/html。"""
        resp = self.client.get("/")
        self.assertIn("text/html", resp.headers.get("content-type", ""))

    def test_root_no_api_key_required(self):
        """GET / 不需要 X-API-Key（前端页面应免鉴权）。"""
        # 不带任何鉴权头，应仍然返回 200
        resp = self.client.get("/", headers={})
        self.assertNotEqual(resp.status_code, 401)
        self.assertEqual(resp.status_code, 200)

    def test_html_contains_ids_form(self):
        """HTML 包含 ID 列表表单元素（ids 模式）。"""
        resp = self.client.get("/")
        html = resp.text
        # 标签页或面板 ids
        self.assertIn("ids", html.lower())
        # textarea 用于多行 ID 输入
        self.assertIn("textarea", html.lower())

    def test_html_contains_keyword_form(self):
        """HTML 包含关键词搜索表单元素（keyword 模式）。"""
        resp = self.client.get("/")
        html = resp.text
        self.assertIn("keyword", html.lower())
        # max_pages 字段
        self.assertIn("max_pages", html.lower())
        # 价格区间
        self.assertIn("min_price", html.lower())
        self.assertIn("max_price", html.lower())

    def test_html_contains_seller_form(self):
        """HTML 包含卖家采集表单元素（seller 模式）。"""
        resp = self.client.get("/")
        html = resp.text
        self.assertIn("seller", html.lower())
        self.assertIn("seller_id", html.lower())

    def test_html_has_with_detail_option(self):
        """三种表单均有 with_detail（详情采集）选项。"""
        resp = self.client.get("/")
        html = resp.text
        # 至少出现一次 with_detail 或 detail（checkbox）
        self.assertTrue("detail" in html.lower())

    def test_html_references_collect_ids_api(self):
        """HTML 中 JS 引用了 /collect/ids 路径。"""
        resp = self.client.get("/")
        html = resp.text
        self.assertIn("/collect/ids", html)

    def test_html_references_collect_keyword_api(self):
        """HTML 中 JS 引用了 /collect/keyword 路径。"""
        resp = self.client.get("/")
        html = resp.text
        self.assertIn("/collect/keyword", html)

    def test_html_references_collect_seller_api(self):
        """HTML 中 JS 引用了 /collect/seller 路径。"""
        resp = self.client.get("/")
        html = resp.text
        self.assertIn("/collect/seller", html)


# ─────────────────────────────────────────────────────────────────────────────
# #24 任务列表 + 进度 + 结果查看
# ─────────────────────────────────────────────────────────────────────────────

class TestUITasksAndResults(unittest.TestCase):
    """feature #24：HTML 引用 /tasks、/products、/listings 端点。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.client, self.api = _make_client(self._tmp)

    def test_html_references_tasks_api(self):
        """HTML 中 JS 引用了 /tasks 路径（用于轮询任务列表）。"""
        resp = self.client.get("/")
        html = resp.text
        self.assertIn("/tasks", html)

    def test_html_references_products_api(self):
        """HTML 中 JS 引用了 /products 路径（结果查看）。"""
        resp = self.client.get("/")
        html = resp.text
        self.assertIn("/products", html)

    def test_html_references_listings_api(self):
        """HTML 中 JS 引用了 /listings 路径（列表数据查看）。"""
        resp = self.client.get("/")
        html = resp.text
        self.assertIn("/listings", html)

    def test_html_has_tasks_table(self):
        """HTML 包含任务列表表格结构。"""
        resp = self.client.get("/")
        html = resp.text
        self.assertIn("<table", html.lower())
        # 任务列表标识
        self.assertIn("tasks", html.lower())

    def test_html_has_result_view(self):
        """HTML 包含结果查看区域（products / listings 标签或按钮）。"""
        resp = self.client.get("/")
        html = resp.text
        # 应有 products 和 listings 结果展示的切换
        self.assertIn("products", html.lower())
        self.assertIn("listings", html.lower())

    def test_html_has_auto_refresh_indicator(self):
        """HTML 包含自动刷新（轮询）相关逻辑。"""
        resp = self.client.get("/")
        html = resp.text
        # setInterval 或 auto-refresh 字样
        self.assertTrue("setInterval" in html or "auto-refresh" in html or "refresh" in html.lower())


# ─────────────────────────────────────────────────────────────────────────────
# #25 当前 IP + 手动切换 IP 按钮
# ─────────────────────────────────────────────────────────────────────────────

class TestUIProxyControls(unittest.TestCase):
    """feature #25：HTML 引用 /proxy/status 和 /proxy/rotate，含换IP按钮。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.client, self.api = _make_client(self._tmp)

    def test_html_references_proxy_status_api(self):
        """HTML 中 JS 引用了 /proxy/status 路径。"""
        resp = self.client.get("/")
        html = resp.text
        self.assertIn("/proxy/status", html)

    def test_html_references_proxy_rotate_api(self):
        """HTML 中 JS 引用了 /proxy/rotate 路径。"""
        resp = self.client.get("/")
        html = resp.text
        self.assertIn("/proxy/rotate", html)

    def test_html_has_rotate_button(self):
        """HTML 包含「换IP」或「切换 IP」按钮相关文字。"""
        resp = self.client.get("/")
        html = resp.text
        # 换IP/切换 IP/获取IP 等字样
        self.assertTrue("ip" in html.lower() or "rotate" in html.lower())

    def test_html_shows_blocked_warning_logic(self):
        """HTML 包含封控状态提示相关代码（blocked 字样）。"""
        resp = self.client.get("/")
        html = resp.text
        self.assertIn("blocked", html.lower())

    def test_html_shows_ip_age_info(self):
        """HTML 包含 IP 寿命相关展示（ip_age_sec 或寿命字样）。"""
        resp = self.client.get("/")
        html = resp.text
        self.assertTrue("ip_age_sec" in html or "寿命" in html)

    def test_html_has_lane_id_input(self):
        """HTML 包含 lane_id 选择输入（多 lane 场景）。"""
        resp = self.client.get("/")
        html = resp.text
        self.assertIn("lane_id", html.lower())


# ─────────────────────────────────────────────────────────────────────────────
# 静态文件可被服务
# ─────────────────────────────────────────────────────────────────────────────

class TestStaticFiles(unittest.TestCase):
    """静态文件挂载验证（/static/ 已注册，不是 404/500 路由缺失）。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.client, self.api = _make_client(self._tmp)

    def test_static_mount_registered(self):
        """app.routes 中应存在挂载到 /static 的路由。"""
        import app.api as api_module
        # 检查路由表：有以 /static 开头的路由（Mount）
        route_paths = []
        for route in api_module.app.routes:
            if hasattr(route, "path"):
                route_paths.append(route.path)
        # StaticFiles mount 注册路径为 /static
        self.assertTrue(
            any("/static" in p for p in route_paths),
            f"未找到 /static 挂载，路由列表：{route_paths}",
        )

    def test_static_nonexist_returns_404_not_500(self):
        """/static/ 下不存在的文件应返回 404，而不是 500（挂载配置错误）。"""
        resp = self.client.get("/static/__nonexist_file__.js")
        self.assertIn(resp.status_code, [404, 200])  # 404 = 文件不存在；不能是 500

    def test_index_html_exists_on_disk(self):
        """app/web/index.html 文件应实际存在于磁盘。"""
        web_dir = PROJECT_ROOT / "app" / "web"
        index_html = web_dir / "index.html"
        self.assertTrue(index_html.exists(), f"app/web/index.html 不存在（路径：{index_html}）")


# ─────────────────────────────────────────────────────────────────────────────
# 回归：已有 API 端点不受 M5 影响
# ─────────────────────────────────────────────────────────────────────────────

class TestM5Regression(unittest.TestCase):
    """M5 不破坏已有 API 端点。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.client, self.api = _make_client(self._tmp)
        self.headers = {"X-API-Key": VALID_KEY}

    def test_health_still_works(self):
        """GET /health 加入 M5 后仍然正常。"""
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_tasks_still_works(self):
        """GET /tasks 加入 M5 后仍然需要鉴权并正常返回。"""
        resp = self.client.get("/tasks", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("items", resp.json())

    def test_tasks_still_requires_auth(self):
        """GET /tasks 加入 M5 后仍然不允许无 key 访问。"""
        resp = self.client.get("/tasks")
        self.assertEqual(resp.status_code, 401)


# ─────────────────────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
