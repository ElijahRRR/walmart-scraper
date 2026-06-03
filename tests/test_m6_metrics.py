"""M6 指标仪表测试。

feature #26：
  - bump_metric() 正确累加 metrics 表各字段
  - GET /metrics 返回正确的聚合数据（成功率/429率/封控率/每IP产出）
  - proxy_log yield 事件的 avg_yield_per_ip 计算正确
  - 空库时 /metrics 返回合理默认值（不报 500）
  - /metrics 端点需要鉴权

feature #27：
  - stress_ip_lifespan.run_stress() dry-run 模式：正确统计封前产出数/封控触发点
  - 封控在第 N 次请求时，total_requests == N，blocked_at_request == N
  - 未封控时 blocked_at_request == None
  - 产出商品数 == min(成功请求数, block_at - 1) 的合理值

验证策略（不发真实网络请求）：
  - 全部使用临时 SQLite 库 + TestClient
  - metric bump 直接操作 DB，不依赖真实采集
  - stress 脚本用 dry-run=True（monkeypatch 封控逻辑由 mock collector 内建）

运行：
    python -m pytest tests/test_m6_metrics.py -v
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
    """建临时 DB 并返回 TestClient（重新加载模块隔离状态）。"""
    db_path = os.path.join(tmp_dir, "test_m6.db")
    cfg_module.DB_PATH = db_path
    cfg_module.API_KEY = "test-key-m6"

    import app.db as db_module
    importlib.reload(db_module)
    db_module.init_db()

    import app.api as api_module
    importlib.reload(api_module)

    from fastapi.testclient import TestClient
    client = TestClient(api_module.app, raise_server_exceptions=True)
    return client, db_module


VALID_KEY = "test-key-m6"
HEADERS = {"X-API-Key": VALID_KEY}


# ─────────────────────────────────────────────────────────────────────────────
# #26 指标仪表：bump_metric 累加逻辑
# ─────────────────────────────────────────────────────────────────────────────

class TestBumpMetric(unittest.TestCase):
    """验证 bump_metric() 正确累加 metrics 表各计数器。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        _, self.db = _make_client(self._tmp)

    def _read_row(self):
        """直接读 metrics 表第一行。"""
        with self.db.get_conn() as conn:
            return conn.execute("SELECT * FROM metrics WHERE id=1").fetchone()

    def test_initial_state_all_zero(self):
        """init_db 后 metrics 行存在且所有计数为 0。"""
        row = self._read_row()
        self.assertIsNotNone(row, "metrics 行应存在（init_db 写入 id=1）")
        self.assertEqual(row["total_requests"], 0)
        self.assertEqual(row["total_success"], 0)
        self.assertEqual(row["total_429"], 0)
        self.assertEqual(row["total_blocked"], 0)
        self.assertEqual(row["total_products"], 0)
        self.assertEqual(row["total_ip_used"], 0)

    def test_bump_single_field(self):
        """bump_metric 累加单个字段。"""
        self.db.bump_metric(total_requests=3)
        row = self._read_row()
        self.assertEqual(row["total_requests"], 3)
        # 其他字段不变
        self.assertEqual(row["total_success"], 0)

    def test_bump_multiple_fields(self):
        """bump_metric 同时累加多个字段。"""
        self.db.bump_metric(total_requests=10, total_success=8,
                            total_429=1, total_blocked=1)
        row = self._read_row()
        self.assertEqual(row["total_requests"], 10)
        self.assertEqual(row["total_success"], 8)
        self.assertEqual(row["total_429"], 1)
        self.assertEqual(row["total_blocked"], 1)

    def test_bump_accumulates_across_calls(self):
        """多次 bump_metric 累加（不覆盖）。"""
        self.db.bump_metric(total_requests=5)
        self.db.bump_metric(total_requests=3)
        row = self._read_row()
        self.assertEqual(row["total_requests"], 8)

    def test_bump_products_and_ip_used(self):
        """bump_metric 正确累加 total_products 和 total_ip_used。"""
        self.db.bump_metric(total_products=12, total_ip_used=2)
        row = self._read_row()
        self.assertEqual(row["total_products"], 12)
        self.assertEqual(row["total_ip_used"], 2)


# ─────────────────────────────────────────────────────────────────────────────
# #26 指标仪表：/metrics 端点聚合计算
# ─────────────────────────────────────────────────────────────────────────────

class TestMetricsEndpoint(unittest.TestCase):
    """验证 GET /metrics 聚合计算正确性。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.client, self.db = _make_client(self._tmp)

    def test_metrics_requires_auth(self):
        """GET /metrics 需要 X-API-Key 鉴权。"""
        resp = self.client.get("/metrics")
        self.assertEqual(resp.status_code, 401)

    def test_metrics_empty_returns_zeros(self):
        """空库时 /metrics 返回全零（不报 500）。"""
        resp = self.client.get("/metrics", headers=HEADERS)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_requests"], 0)
        self.assertEqual(data["total_success"], 0)
        self.assertEqual(data["total_429"], 0)
        self.assertEqual(data["total_blocked"], 0)
        self.assertIsNone(data["success_rate"])  # 分母为 0 → None
        self.assertIsNone(data["rate_429"])
        self.assertIsNone(data["blocked_rate"])
        self.assertIsNone(data["avg_yield_per_ip"])

    def test_metrics_returns_required_fields(self):
        """GET /metrics 返回所有必需字段。"""
        resp = self.client.get("/metrics", headers=HEADERS)
        data = resp.json()
        required = [
            "total_requests", "total_success", "total_429", "total_blocked",
            "total_products", "total_ip_used",
            "success_rate", "rate_429", "blocked_rate", "avg_yield_per_ip",
        ]
        for field in required:
            self.assertIn(field, data, f"缺少字段: {field}")

    def test_success_rate_calculation(self):
        """成功率 = total_success / total_requests（带 4 位小数）。"""
        self.db.bump_metric(total_requests=10, total_success=8)
        resp = self.client.get("/metrics", headers=HEADERS)
        data = resp.json()
        self.assertEqual(data["total_requests"], 10)
        self.assertEqual(data["total_success"], 8)
        self.assertAlmostEqual(data["success_rate"], 0.8, places=3)

    def test_rate_429_calculation(self):
        """429 率 = total_429 / total_requests。"""
        self.db.bump_metric(total_requests=20, total_429=2)
        resp = self.client.get("/metrics", headers=HEADERS)
        data = resp.json()
        self.assertAlmostEqual(data["rate_429"], 0.1, places=3)

    def test_blocked_rate_calculation(self):
        """封控率 = total_blocked / total_requests。"""
        self.db.bump_metric(total_requests=50, total_blocked=5)
        resp = self.client.get("/metrics", headers=HEADERS)
        data = resp.json()
        self.assertAlmostEqual(data["blocked_rate"], 0.1, places=3)

    def test_avg_yield_per_ip_from_proxy_log(self):
        """avg_yield_per_ip 从 proxy_log yield 事件聚合。

        注入两条 yield 记录（count=10, count=20）→ 平均 = 15.0。
        """
        with self.db.get_conn() as conn:
            conn.execute(
                "INSERT INTO proxy_log(ip, event, count) VALUES(?, ?, ?)",
                ("1.2.3.4:8080", "yield", 10),
            )
            conn.execute(
                "INSERT INTO proxy_log(ip, event, count) VALUES(?, ?, ?)",
                ("5.6.7.8:8080", "yield", 20),
            )
        resp = self.client.get("/metrics", headers=HEADERS)
        data = resp.json()
        self.assertAlmostEqual(data["avg_yield_per_ip"], 15.0, places=1)

    def test_avg_yield_per_ip_ignores_non_yield_events(self):
        """avg_yield_per_ip 只聚合 yield 事件，extract/block 不计入。"""
        with self.db.get_conn() as conn:
            conn.execute(
                "INSERT INTO proxy_log(ip, event, count) VALUES(?, ?, ?)",
                ("1.2.3.4:8080", "extract", 0),
            )
            conn.execute(
                "INSERT INTO proxy_log(ip, event, count) VALUES(?, ?, ?)",
                ("1.2.3.4:8080", "block", 5),
            )
            conn.execute(
                "INSERT INTO proxy_log(ip, event, count) VALUES(?, ?, ?)",
                ("1.2.3.4:8080", "yield", 12),
            )
        resp = self.client.get("/metrics", headers=HEADERS)
        data = resp.json()
        # 只有 yield 事件：count=12，yield_count=1 → avg=12.0
        self.assertAlmostEqual(data["avg_yield_per_ip"], 12.0, places=1)

    def test_all_rates_correct_combined(self):
        """综合：注入完整数据，所有比率同时正确。

        requests=100, success=80, 429=5, blocked=3
        → success_rate=0.8, rate_429=0.05, blocked_rate=0.03
        """
        self.db.bump_metric(
            total_requests=100,
            total_success=80,
            total_429=5,
            total_blocked=3,
            total_products=78,
            total_ip_used=4,
        )
        resp = self.client.get("/metrics", headers=HEADERS)
        data = resp.json()
        self.assertEqual(data["total_requests"], 100)
        self.assertEqual(data["total_success"], 80)
        self.assertEqual(data["total_429"], 5)
        self.assertEqual(data["total_blocked"], 3)
        self.assertEqual(data["total_products"], 78)
        self.assertEqual(data["total_ip_used"], 4)
        self.assertAlmostEqual(data["success_rate"], 0.8,  places=3)
        self.assertAlmostEqual(data["rate_429"],     0.05, places=3)
        self.assertAlmostEqual(data["blocked_rate"], 0.03, places=3)


# ─────────────────────────────────────────────────────────────────────────────
# #26 指标仪表：前端 HTML 包含指标面板
# ─────────────────────────────────────────────────────────────────────────────

class TestMetricsUI(unittest.TestCase):
    """验证 Web UI 包含指标面板引用。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.client, _ = _make_client(self._tmp)

    def test_html_references_metrics_api(self):
        """GET / 返回的 HTML 中包含 /metrics 路径。"""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("/metrics", resp.text)

    def test_html_has_metrics_panel(self):
        """HTML 包含指标面板相关标识（metricsCard 或 指标）。"""
        resp = self.client.get("/")
        html = resp.text
        self.assertTrue(
            "metricsCard" in html or "指标" in html or "metrics" in html.lower(),
            "HTML 应包含指标面板",
        )

    def test_html_shows_success_rate(self):
        """HTML 中包含成功率（success_rate 或 成功率）显示逻辑。"""
        resp = self.client.get("/")
        html = resp.text
        self.assertTrue(
            "success_rate" in html or "成功率" in html,
            "HTML 应显示成功率",
        )

    def test_html_shows_blocked_rate(self):
        """HTML 中包含封控率（blocked_rate 或 封控率）显示逻辑。"""
        resp = self.client.get("/")
        html = resp.text
        self.assertTrue(
            "blocked_rate" in html or "封控率" in html,
            "HTML 应显示封控率",
        )


# ─────────────────────────────────────────────────────────────────────────────
# #27 压测脚本：dry-run 自检
# ─────────────────────────────────────────────────────────────────────────────

class TestStressIpLifespan(unittest.TestCase):
    """验证 stress_ip_lifespan.run_stress() dry-run 模式行为。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        # 设置临时 DB（run_stress 会调用 init_db）
        cfg_module.DB_PATH = os.path.join(self._tmp, "test_stress.db")
        import app.db as db_module
        importlib.reload(db_module)
        db_module.init_db()

    def _run(self, ids: list[str], block_at: int) -> dict:
        """跑 dry-run 压测并返回结果。"""
        from scripts.stress_ip_lifespan import run_stress
        return run_stress(
            product_ids=ids,
            dry_run=True,
            block_at=block_at,
        )

    def test_block_at_3_stops_at_3rd_request(self):
        """block_at=3 时，第 3 次请求触发封控，total_requests==3。"""
        ids = ["A", "B", "C", "D", "E"]
        result = self._run(ids, block_at=3)
        self.assertEqual(result["total_requests"], 3,
                         f"应在第3次请求时停止，实际: {result}")
        self.assertEqual(result["blocked_at_request"], 3)
        self.assertIsNotNone(result["block_reason"])

    def test_products_saved_before_block(self):
        """封控前成功的请求应有商品落库（block_at=4 → 前3次成功 → products_saved=3）。"""
        ids = ["X1", "X2", "X3", "X4", "X5"]
        result = self._run(ids, block_at=4)
        # 前 3 次为 ok，第 4 次封控，products_saved = 3
        self.assertEqual(result["products_saved"], 3,
                         f"封控前应有 3 个商品落库，实际: {result}")

    def test_no_block_when_block_at_exceeds_ids(self):
        """block_at 超过 ID 数量时，全部采集完不发生封控。"""
        ids = ["P1", "P2", "P3"]
        result = self._run(ids, block_at=999)
        self.assertIsNone(result["blocked_at_request"],
                          f"不应发生封控，实际: {result}")
        self.assertEqual(result["block_reason"], None)
        self.assertEqual(result["total_requests"], 3)

    def test_result_has_required_keys(self):
        """run_stress 结果包含所有必要字段。"""
        ids = ["R1", "R2"]
        result = self._run(ids, block_at=2)
        required = ["ip", "total_requests", "blocked_at_request",
                    "block_reason", "products_saved", "elapsed_sec"]
        for key in required:
            self.assertIn(key, result, f"结果缺少字段: {key}")

    def test_elapsed_sec_is_non_negative(self):
        """elapsed_sec 应为非负数（mock 场景可能极快导致 0.0，合理）。"""
        ids = ["T1"]
        result = self._run(ids, block_at=5)
        self.assertGreaterEqual(result["elapsed_sec"], 0)

    def test_block_at_1_blocks_immediately(self):
        """block_at=1 时，第一次请求即触发封控，products_saved=0。"""
        ids = ["Z1", "Z2", "Z3"]
        result = self._run(ids, block_at=1)
        self.assertEqual(result["total_requests"], 1)
        self.assertEqual(result["blocked_at_request"], 1)
        self.assertEqual(result["products_saved"], 0)


# ─────────────────────────────────────────────────────────────────────────────
# 回归：M1-M5 不受 M6 影响
# ─────────────────────────────────────────────────────────────────────────────

class TestM6Regression(unittest.TestCase):
    """确保 M6 添加的代码不破坏已有端点。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.client, _ = _make_client(self._tmp)

    def test_health_still_ok(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_tasks_endpoint_still_ok(self):
        resp = self.client.get("/tasks", headers=HEADERS)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("items", resp.json())

    def test_products_endpoint_still_ok(self):
        resp = self.client.get("/products", headers=HEADERS)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("items", resp.json())

    def test_proxy_status_still_ok(self):
        resp = self.client.get("/proxy/status", headers=HEADERS)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("lanes", resp.json())


# ─────────────────────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
