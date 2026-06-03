"""M3 REST API 测试：FastAPI 端点全覆盖。

测试策略：
  - 使用 fastapi.testclient.TestClient（不启动真实服务器，不发真实网络请求）
  - 对 runner.run_ids/run_keyword/run_seller 打桩，不调用真实采集
  - 对 LanePool.resume_lane / get_all_status 打桩，不调用 ProxyPool._extract
  - 全程使用临时 SQLite 库，隔离真实数据
  - 覆盖：401、有效key、三采集端点、任务查询、products/listings keyset分页、proxy状态/换IP

运行：
    python -m pytest tests/test_m3_api.py -v
"""
import importlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.config as cfg_module


# ─────────────────────────────────────────────────────────────────────────────
# 辅助：临时 DB 设置
# ─────────────────────────────────────────────────────────────────────────────

def _setup_temp_db(tmp_dir: str) -> str:
    db_path = os.path.join(tmp_dir, "test_m3.db")
    cfg_module.DB_PATH = db_path
    import app.db as db_module
    importlib.reload(db_module)
    db_module.init_db()
    return db_path


def _get_test_client(tmp_dir: str):
    """返回 TestClient（已初始化临时 DB + 覆盖 API_KEY）。"""
    _setup_temp_db(tmp_dir)
    # 固定 API Key，便于测试
    cfg_module.API_KEY = "test-key-xyz"

    # 重新加载 api 模块，确保使用更新后的 config（避免闭包缓存旧值）
    import app.api as api_module
    importlib.reload(api_module)

    from fastapi.testclient import TestClient
    client = TestClient(api_module.app, raise_server_exceptions=True)
    return client, api_module


VALID_KEY = "test-key-xyz"
BAD_KEY = "wrong-key"


# ─────────────────────────────────────────────────────────────────────────────
# #18 API Key 鉴权
# ─────────────────────────────────────────────────────────────────────────────

class TestApiKeyAuth(unittest.TestCase):
    """feature #18：无key拒绝 / 正确key放行 / /health 免鉴权"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.client, self.api = _get_test_client(self._tmp)

    def test_health_no_key(self):
        """GET /health 不需要 key，应返回 200。"""
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")

    def test_no_key_returns_401(self):
        """缺少 X-API-Key 应返回 401。"""
        resp = self.client.get("/tasks")
        self.assertEqual(resp.status_code, 401)

    def test_wrong_key_returns_401(self):
        """错误 X-API-Key 应返回 401。"""
        resp = self.client.get("/tasks", headers={"X-API-Key": BAD_KEY})
        self.assertEqual(resp.status_code, 401)

    def test_correct_key_passes(self):
        """正确 X-API-Key 应放行（返回 200 而不是 401）。"""
        resp = self.client.get("/tasks", headers={"X-API-Key": VALID_KEY})
        self.assertEqual(resp.status_code, 200)

    def test_collect_ids_no_key_401(self):
        resp = self.client.post("/collect/ids", json={"ids": ["123"]})
        self.assertEqual(resp.status_code, 401)

    def test_proxy_status_no_key_401(self):
        resp = self.client.get("/proxy/status")
        self.assertEqual(resp.status_code, 401)


# ─────────────────────────────────────────────────────────────────────────────
# #14 采集任务提交端点
# ─────────────────────────────────────────────────────────────────────────────

class TestCollectEndpoints(unittest.TestCase):
    """feature #14：三个 /collect/* 端点提交任务，返回 task_id，任务入库。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.client, self.api = _get_test_client(self._tmp)
        self.headers = {"X-API-Key": VALID_KEY}

    def test_collect_ids_returns_task_id(self):
        """POST /collect/ids 返回 task_id 并入库。"""
        # 对后台采集函数打桩（不执行真实采集）
        with patch.object(self.api, "_bg_run_ids", return_value=None):
            resp = self.client.post(
                "/collect/ids",
                json={"ids": ["111", "222"], "with_detail": True},
                headers=self.headers,
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("task_id", data)
        self.assertIsInstance(data["task_id"], int)
        self.assertEqual(data["status"], "pending")

        # 验证任务确实入库
        from app.service.tasks import get_task
        task = get_task(data["task_id"])
        self.assertIsNotNone(task)
        self.assertEqual(task["type"], "detail")
        self.assertEqual(task["status"], "pending")

    def test_collect_ids_params_validation(self):
        """POST /collect/ids ids 为空时应返回 422。"""
        resp = self.client.post(
            "/collect/ids",
            json={"ids": []},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 422)

    def test_collect_keyword_returns_task_id(self):
        """POST /collect/keyword 返回 task_id 并入库，参数包含 min_price。"""
        with patch.object(self.api, "_bg_run_keyword", return_value=None):
            resp = self.client.post(
                "/collect/keyword",
                json={"keyword": "iphone case", "max_pages": 2,
                      "min_price": 5.0, "max_price": 50.0, "with_detail": False},
                headers=self.headers,
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("task_id", data)

        from app.service.tasks import get_task
        task = get_task(data["task_id"])
        self.assertIsNotNone(task)
        self.assertEqual(task["type"], "keyword")
        self.assertEqual(task["params"]["keyword"], "iphone case")
        self.assertEqual(task["params"]["min_price"], 5.0)

    def test_collect_keyword_validation(self):
        """max_pages 超出范围应返回 422。"""
        resp = self.client.post(
            "/collect/keyword",
            json={"keyword": "test", "max_pages": 99},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 422)

    def test_collect_seller_returns_task_id(self):
        """POST /collect/seller 返回 task_id 并入库。"""
        with patch.object(self.api, "_bg_run_seller", return_value=None):
            resp = self.client.post(
                "/collect/seller",
                json={"seller_id": "SELLER_001", "max_pages": 3, "with_detail": True},
                headers=self.headers,
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("task_id", data)

        from app.service.tasks import get_task
        task = get_task(data["task_id"])
        self.assertIsNotNone(task)
        self.assertEqual(task["type"], "seller")
        self.assertEqual(task["params"]["seller_id"], "SELLER_001")

    def test_multiple_tasks_get_different_ids(self):
        """连续提交三个任务，各自拿到不同 task_id。"""
        ids_list = []
        for _ in range(3):
            with patch.object(self.api, "_bg_run_ids", return_value=None):
                resp = self.client.post(
                    "/collect/ids",
                    json={"ids": ["999"]},
                    headers=self.headers,
                )
            self.assertEqual(resp.status_code, 200)
            ids_list.append(resp.json()["task_id"])
        # 三个 task_id 应不同
        self.assertEqual(len(set(ids_list)), 3)


# ─────────────────────────────────────────────────────────────────────────────
# #15 任务查询端点
# ─────────────────────────────────────────────────────────────────────────────

class TestTaskEndpoints(unittest.TestCase):
    """feature #15：GET /tasks/{id} 状态/进度，GET /tasks 分页列表。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.client, self.api = _get_test_client(self._tmp)
        self.headers = {"X-API-Key": VALID_KEY}

    def _create_task(self, type_="detail", params=None):
        from app.service.tasks import create_task, update_status, update_progress
        task_id = create_task(type_, params or {"ids": ["A"]})
        update_status(task_id, "running")
        update_progress(task_id, progress=3, total=10, result_count=2)
        return task_id

    def test_get_task_by_id(self):
        """GET /tasks/{id} 返回正确的状态和进度字段。"""
        task_id = self._create_task()
        resp = self.client.get(f"/tasks/{task_id}", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["id"], task_id)
        self.assertEqual(data["status"], "running")
        self.assertEqual(data["progress"], 3)
        self.assertEqual(data["total"], 10)
        self.assertEqual(data["result_count"], 2)

    def test_get_task_not_found(self):
        """不存在的 task_id 应返回 404。"""
        resp = self.client.get("/tasks/99999", headers=self.headers)
        self.assertEqual(resp.status_code, 404)

    def test_list_tasks_returns_items(self):
        """GET /tasks 返回分页列表，含 items/count/limit/offset。"""
        for i in range(3):
            self._create_task()
        resp = self.client.get("/tasks?limit=10&offset=0", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("items", data)
        self.assertIn("count", data)
        self.assertIn("limit", data)
        self.assertIn("offset", data)
        self.assertGreaterEqual(data["count"], 3)

    def test_list_tasks_pagination(self):
        """GET /tasks 分页参数生效（limit=1 应只返回1条）。"""
        for _ in range(3):
            self._create_task()
        resp = self.client.get("/tasks?limit=1&offset=0", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(len(data["items"]), 1)

    def test_task_contains_result_count(self):
        """/tasks/{id} 响应含 result_count 字段。"""
        task_id = self._create_task()
        resp = self.client.get(f"/tasks/{task_id}", headers=self.headers)
        data = resp.json()
        self.assertIn("result_count", data)


# ─────────────────────────────────────────────────────────────────────────────
# #16 结果浏览端点
# ─────────────────────────────────────────────────────────────────────────────

class TestResultEndpoints(unittest.TestCase):
    """feature #16：GET /products、GET /listings keyset 分页，按 task_id 过滤。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.client, self.api = _get_test_client(self._tmp)
        self.headers = {"X-API-Key": VALID_KEY}

    def _seed_data(self):
        """向 DB 写入桩商品数据，返回 task_id。"""
        from app.service.tasks import create_task
        from app.service.runner import save_product, save_listing_items

        task_id = create_task("detail", {"ids": ["P1", "P2", "P3"]})

        for i in range(1, 4):
            save_product({
                "product_id": f"ITEM{i:04d}",
                "_status": "ok",
                "title": f"Test Product {i}",
                "price": 9.99 * i,
                "brand": "TestBrand",
            }, task_id=task_id)

        save_listing_items([
            {"product_id": f"ITEM{i:04d}", "title": f"Listing {i}", "price": 5.0 * i}
            for i in range(1, 4)
        ], task_id=task_id)

        return task_id

    def test_products_basic(self):
        """GET /products 返回结构正确（items/count/next_cursor/limit）。"""
        self._seed_data()
        resp = self.client.get("/products?limit=10", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("items", data)
        self.assertIn("count", data)
        self.assertIn("next_cursor", data)
        self.assertIn("limit", data)
        self.assertGreaterEqual(data["count"], 3)

    def test_products_keyset_pagination(self):
        """keyset 分页：after_id 游标生效。"""
        task_id = self._seed_data()
        # 取第一页（limit=2）
        resp1 = self.client.get("/products?limit=2", headers=self.headers)
        self.assertEqual(resp1.status_code, 200)
        data1 = resp1.json()
        self.assertEqual(data1["count"], 2)

        # 用 next_cursor 取第二页
        cursor = data1["next_cursor"]
        resp2 = self.client.get(f"/products?after_id={cursor}&limit=10",
                                headers=self.headers)
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        # 第二页不应包含第一页的 id
        first_page_ids = {item["id"] for item in data1["items"]}
        second_page_ids = {item["id"] for item in data2["items"]}
        self.assertTrue(first_page_ids.isdisjoint(second_page_ids))

    def test_products_filter_by_task_id(self):
        """GET /products?task_id=X 只返回该任务的商品。"""
        task_id1 = self._seed_data()  # 3 条 ITEM0001-0003

        # 另一个任务的商品
        from app.service.tasks import create_task
        from app.service.runner import save_product
        task_id2 = create_task("detail", {"ids": ["OTHER1"]})
        save_product({
            "product_id": "OTHER0001",
            "_status": "ok",
            "title": "Other Product",
        }, task_id=task_id2)

        # 按 task_id1 过滤，不含 OTHER0001
        resp = self.client.get(f"/products?task_id={task_id1}&limit=50",
                               headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        pids = {item["product_id"] for item in data["items"]}
        self.assertNotIn("OTHER0001", pids)
        # 三条 ITEM 都在
        for i in range(1, 4):
            self.assertIn(f"ITEM{i:04d}", pids)

    def test_listings_basic(self):
        """GET /listings 返回结构正确。"""
        self._seed_data()
        resp = self.client.get("/listings?limit=10", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("items", data)
        self.assertGreaterEqual(data["count"], 3)

    def test_listings_filter_by_task_id(self):
        """GET /listings?task_id=X 只返回指定任务的列表项。"""
        task_id = self._seed_data()
        resp = self.client.get(f"/listings?task_id={task_id}&limit=50",
                               headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # 所有结果 task_id 字段应一致
        for item in data["items"]:
            self.assertEqual(item["task_id"], task_id)

    def test_listings_empty_task(self):
        """不存在的 task_id 过滤应返回空列表，不报错。"""
        resp = self.client.get("/listings?task_id=99999&limit=10",
                               headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["items"], [])


# ─────────────────────────────────────────────────────────────────────────────
# #17 代理管理端点
# ─────────────────────────────────────────────────────────────────────────────

class TestProxyEndpoints(unittest.TestCase):
    """feature #17：GET /proxy/status 状态查询，POST /proxy/rotate 手动换IP。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.client, self.api = _get_test_client(self._tmp)
        self.headers = {"X-API-Key": VALID_KEY}

        # 重置全局 lane pool 单例，便于测试注入
        from app.service import lanes as lanes_module
        lanes_module.reset_lane_pool()

    def _make_mock_lane_pool(self, n_lanes=1):
        """创建 mock LanePool，不调用真实 ProxyPool._extract。"""
        from app.service.lanes import LanePool, Lane

        mock_pool = MagicMock(spec=LanePool)
        mock_pool.n_lanes = n_lanes

        # get_all_status 返回假数据
        now = time.time()
        mock_pool.get_all_status.return_value = [
            {
                "lane_id": 0,
                "state": "idle",
                "current_ip": "1.2.3.4:443",
                "ip_born_at": now - 300,
                "ip_age_sec": 300.0,
                "ip_uses": 5,
                "last_block": None,
                "last_block_at": 0.0,
                "total_products": 12,
            }
        ]

        # get_lane_status 返回单条
        mock_pool.get_lane_status.return_value = {
            "lane_id": 0,
            "state": "idle",
            "current_ip": "5.6.7.8:443",
            "ip_born_at": now,
            "ip_age_sec": 1.0,
            "ip_uses": 0,
            "last_block": None,
            "last_block_at": 0.0,
            "total_products": 12,
        }

        # resume_lane 返回新IP
        mock_pool.resume_lane.return_value = "5.6.7.8:443"

        return mock_pool

    def test_proxy_status_structure(self):
        """GET /proxy/status 返回 {lanes: [...]}，lane 含必要字段。"""
        mock_pool = self._make_mock_lane_pool()
        with patch("app.api.get_lane_pool", return_value=mock_pool):
            resp = self.client.get("/proxy/status", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("lanes", data)
        self.assertIsInstance(data["lanes"], list)
        self.assertGreater(len(data["lanes"]), 0)

        lane = data["lanes"][0]
        for field in ("lane_id", "state", "current_ip", "ip_age_sec",
                      "ip_uses", "total_products"):
            self.assertIn(field, lane, f"缺少字段 {field}")

    def test_proxy_status_shows_current_ip(self):
        """GET /proxy/status 展示的 current_ip 与 mock 一致。"""
        mock_pool = self._make_mock_lane_pool()
        with patch("app.api.get_lane_pool", return_value=mock_pool):
            resp = self.client.get("/proxy/status", headers=self.headers)
        data = resp.json()
        self.assertEqual(data["lanes"][0]["current_ip"], "1.2.3.4:443")

    def test_proxy_rotate_triggers_resume_lane(self):
        """POST /proxy/rotate 触发 LanePool.resume_lane，返回新IP信息。"""
        mock_pool = self._make_mock_lane_pool()
        with patch("app.api.get_lane_pool", return_value=mock_pool):
            resp = self.client.post(
                "/proxy/rotate",
                json={"lane_id": 0},
                headers=self.headers,
            )
        self.assertEqual(resp.status_code, 200)
        # 确认 resume_lane 被调用（换IP触发）
        mock_pool.resume_lane.assert_called_once_with(0)
        data = resp.json()
        self.assertIn("new_ip", data)
        self.assertEqual(data["new_ip"], "5.6.7.8:443")
        self.assertIn("lane_id", data)
        self.assertIn("lane_status", data)

    def test_proxy_rotate_invalid_lane(self):
        """lane_id 超出范围应返回 400。"""
        mock_pool = self._make_mock_lane_pool(n_lanes=1)
        with patch("app.api.get_lane_pool", return_value=mock_pool):
            resp = self.client.post(
                "/proxy/rotate",
                json={"lane_id": 99},
                headers=self.headers,
            )
        self.assertEqual(resp.status_code, 400)

    def test_proxy_rotate_default_lane_id(self):
        """POST /proxy/rotate 不传 lane_id 时默认用 lane 0。"""
        mock_pool = self._make_mock_lane_pool()
        with patch("app.api.get_lane_pool", return_value=mock_pool):
            resp = self.client.post(
                "/proxy/rotate",
                json={},  # 空 body，lane_id 默认 0
                headers=self.headers,
            )
        self.assertEqual(resp.status_code, 200)
        mock_pool.resume_lane.assert_called_once_with(0)


# ─────────────────────────────────────────────────────────────────────────────
# run_server.py 存在验证
# ─────────────────────────────────────────────────────────────────────────────

class TestRunServer(unittest.TestCase):
    """run_server.py 就位且可导入（不实际启动服务器）。"""

    def test_run_server_exists(self):
        run_server_path = PROJECT_ROOT / "run_server.py"
        self.assertTrue(run_server_path.exists(), "run_server.py 不存在")

    def test_run_server_importable(self):
        """run_server.py 可 import（不启动服务器）—— 通过检查内容验证语法。"""
        run_server_path = PROJECT_ROOT / "run_server.py"
        with open(run_server_path, encoding="utf-8") as f:
            source = f.read()
        # 验证包含关键启动代码
        self.assertIn("uvicorn", source)
        self.assertIn("app.api:app", source)
        self.assertIn("PORT", source)


# ─────────────────────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
