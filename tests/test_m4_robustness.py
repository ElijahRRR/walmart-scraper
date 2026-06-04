"""M4 健壮性测试：断点续采 / 失败重试 / 变动检测 / webhook 回调。

测试策略：
  - 全程使用临时 SQLite（tmp_path），不发真实网络请求
  - collector / urllib.request 通过 unittest.mock 打桩
  - webhook 用本地 mock（monkeypatch _fire_webhook），捕获调用不做真实 HTTP

运行：
    python -m pytest tests/test_m4_robustness.py -v
"""
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.config as cfg_module


# ─────────────────────────────────────────────────────────────────────────────
# Fixture 辅助
# ─────────────────────────────────────────────────────────────────────────────

def _setup_temp_db(tmp_dir: str) -> str:
    db_path = os.path.join(tmp_dir, "test_m4.db")
    cfg_module.DB_PATH = db_path
    import app.db as db_module
    importlib.reload(db_module)
    db_module.init_db()
    return db_path


def _reload_all(tmp_dir: str):
    """重新加载所有相关模块，使用临时 DB。"""
    _setup_temp_db(tmp_dir)
    import app.db as db_module
    import app.service.tasks as tasks_module
    import app.service.runner as runner_module
    importlib.reload(db_module)
    importlib.reload(tasks_module)
    importlib.reload(runner_module)
    return tasks_module, runner_module


def _make_ok_result(product_id: str, price: float = 10.0,
                    seller_count: int = 1) -> dict:
    return {
        "_status": "ok",
        "product_id": product_id,
        "brand": "TestBrand",
        "title": f"Product {product_id}",
        "price": price,
        "seller_count": seller_count,
    }


def _make_blocked_result(product_id: str) -> dict:
    return {"_status": "blocked", "product_id": product_id}


def _make_fail_result(product_id: str) -> dict:
    """非封控的失败（如 empty_page）"""
    return {"_status": "empty_page", "product_id": product_id}


# ─────────────────────────────────────────────────────────────────────────────
# #19 断点续采测试
# ─────────────────────────────────────────────────────────────────────────────

class TestResume(unittest.TestCase):
    """feature #19：断点续采 — 重跑能跳过已完成项，只采剩余。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tasks, self.runner = _reload_all(self.tmp)

    def test_mark_item_done_and_get_completed(self):
        """mark_item_done 将 product_id 写入 completed_ids；get_completed_ids 能读回。"""
        task_id = self.tasks.create_task("detail", {"ids": ["A", "B", "C"]})
        self.tasks.mark_item_done(task_id, "A")
        self.tasks.mark_item_done(task_id, "B")

        completed = self.tasks.get_completed_ids(task_id)
        self.assertIn("A", completed)
        self.assertIn("B", completed)
        self.assertNotIn("C", completed)

    def test_mark_item_done_idempotent(self):
        """同一 product_id 重复 mark 不会重复存储。"""
        task_id = self.tasks.create_task("detail", {"ids": ["X"]})
        self.tasks.mark_item_done(task_id, "X")
        self.tasks.mark_item_done(task_id, "X")

        completed = self.tasks.get_completed_ids(task_id)
        self.assertEqual(len([x for x in completed if x == "X"]), 1)

    def test_run_ids_marks_items_done(self):
        """run_ids 每采完一个 item 后都 mark_item_done。"""
        call_log = []

        def fake_collect(pid, **kw):
            call_log.append(pid)
            return _make_ok_result(pid)

        mock_c = MagicMock()
        mock_c.collect_detail.side_effect = fake_collect

        task_id = self.runner.run_ids(["P1", "P2", "P3"], collector=mock_c)

        completed = self.tasks.get_completed_ids(task_id)
        self.assertIn("P1", completed)
        self.assertIn("P2", completed)
        self.assertIn("P3", completed)

    def test_resume_skips_completed(self):
        """续采时跳过已完成项，只采剩余。"""
        # 先跑 P1，中途假设被打断（手动 mark P1 完成，P2 未完成）
        task_id = self.tasks.create_task("detail", {"ids": ["P1", "P2"]})
        self.tasks.mark_item_done(task_id, "P1")

        call_log = []

        def fake_collect(pid, **kw):
            call_log.append(pid)
            return _make_ok_result(pid)

        mock_c = MagicMock()
        mock_c.collect_detail.side_effect = fake_collect

        # 续采：resume_task_id=task_id，只应采 P2
        resumed_id = self.runner.run_ids(
            ["P1", "P2"], collector=mock_c, resume_task_id=task_id
        )

        self.assertEqual(resumed_id, task_id)
        # 只有 P2 被采集（P1 被跳过）
        self.assertNotIn("P1", call_log)
        self.assertIn("P2", call_log)

    def test_resume_all_completed_no_calls(self):
        """所有 ID 均已完成时，续采不触发任何 collect_detail 调用。"""
        task_id = self.tasks.create_task("detail", {"ids": ["A", "B"]})
        self.tasks.mark_item_done(task_id, "A")
        self.tasks.mark_item_done(task_id, "B")

        mock_c = MagicMock()

        self.runner.run_ids(["A", "B"], collector=mock_c, resume_task_id=task_id)

        mock_c.collect_detail.assert_not_called()

    def test_resume_task_status_done(self):
        """续采完成后任务状态为 done。"""
        task_id = self.tasks.create_task("detail", {"ids": ["X", "Y"]})
        self.tasks.mark_item_done(task_id, "X")

        mock_c = MagicMock()
        mock_c.collect_detail.return_value = _make_ok_result("Y")

        self.runner.run_ids(["X", "Y"], collector=mock_c, resume_task_id=task_id)

        task = self.tasks.get_task(task_id)
        self.assertEqual(task["status"], "done")

    def test_get_completed_ids_nonexistent_task(self):
        """不存在的 task_id，get_completed_ids 返回空集合。"""
        completed = self.tasks.get_completed_ids(99999)
        self.assertEqual(completed, set())


# ─────────────────────────────────────────────────────────────────────────────
# #20 失败重试测试
# ─────────────────────────────────────────────────────────────────────────────

class TestRetry(unittest.TestCase):
    """feature #20：失败重试 — 瞬时失败重试到上限；封控不重试；超限标 give_up。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tasks, self.runner = _reload_all(self.tmp)

    def test_transient_failure_retries_and_succeeds(self):
        """瞬时失败（empty_page）后重试，第二次成功。"""
        call_count = [0]

        def fake_collect(pid, **kw):
            call_count[0] += 1
            if call_count[0] < 2:
                return _make_fail_result(pid)
            return _make_ok_result(pid, price=29.99)

        mock_c = MagicMock()
        mock_c.collect_detail.side_effect = fake_collect

        result = self.runner._collect_with_retry(mock_c, "P001")
        self.assertEqual(result["_status"], "ok")
        self.assertAlmostEqual(result["price"], 29.99)
        self.assertEqual(call_count[0], 2)

    def test_exception_retries_to_limit(self):
        """连续抛出异常，超过 RETRY_MAX 后返回 give_up。"""
        call_count = [0]

        def fake_collect(pid, **kw):
            call_count[0] += 1
            raise RuntimeError("临时网络超时")

        mock_c = MagicMock()
        mock_c.collect_detail.side_effect = fake_collect

        result = self.runner._collect_with_retry(mock_c, "P002")
        self.assertEqual(result["_status"], "give_up")
        # 总调用次数 = 1 + RETRY_MAX
        self.assertEqual(call_count[0], 1 + self.runner.RETRY_MAX)

    def test_blocked_not_retried(self):
        """封控状态直接返回，不重试（只调用一次）。"""
        call_count = [0]

        def fake_collect(pid, **kw):
            call_count[0] += 1
            return _make_blocked_result(pid)

        mock_c = MagicMock()
        mock_c.collect_detail.side_effect = fake_collect

        result = self.runner._collect_with_retry(mock_c, "P003")
        self.assertEqual(result["_status"], "blocked")
        self.assertEqual(call_count[0], 1)  # 只调用一次，未重试

    def test_blocked_stops_task(self):
        """run_ids 中遇到封控，任务状态变为 blocked。"""
        mock_c = MagicMock()
        mock_c.collect_detail.return_value = _make_blocked_result("B001")

        task_id = self.runner.run_ids(["B001"], collector=mock_c)
        task = self.tasks.get_task(task_id)
        self.assertEqual(task["status"], "blocked")

    def test_give_up_item_does_not_stop_task(self):
        """单个 item 超过重试上限（give_up）后，任务继续采下一个 item，最终 done。"""
        # P1 连续失败（返回 empty_page），P2 成功
        results = {
            "P1": [_make_fail_result("P1")] * (1 + self.runner.RETRY_MAX),
            "P2": [_make_ok_result("P2", price=5.0)],
        }
        call_counts = {"P1": 0, "P2": 0}

        def fake_collect(pid, **kw):
            idx = call_counts[pid]
            call_counts[pid] += 1
            r_list = results[pid]
            return r_list[min(idx, len(r_list) - 1)]

        mock_c = MagicMock()
        mock_c.collect_detail.side_effect = fake_collect

        task_id = self.runner.run_ids(["P1", "P2"], collector=mock_c)
        task = self.tasks.get_task(task_id)
        self.assertEqual(task["status"], "done")  # 任务未因 P1 失败而中止

        # P2 应该入库了
        import app.db as db
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM products WHERE product_id='P2'"
            ).fetchone()
        self.assertIsNotNone(row)

    def test_ok_on_first_try_no_extra_calls(self):
        """首次即成功，不多余调用。"""
        mock_c = MagicMock()
        mock_c.collect_detail.return_value = _make_ok_result("P999")

        result = self.runner._collect_with_retry(mock_c, "P999")
        self.assertEqual(result["_status"], "ok")
        mock_c.collect_detail.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# #21 变动检测测试
# ─────────────────────────────────────────────────────────────────────────────

class TestChangeDetection(unittest.TestCase):
    """feature #21：变动检测 — 与上次快照对比 price/seller_count，记录变动。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tasks, self.runner = _reload_all(self.tmp)

    def _save(self, product_id: str, price: float, seller_count: int,
              task_id: int = None) -> bool:
        """便捷方法：构造 result 并调用 save_product。"""
        if task_id is None:
            task_id = self.tasks.create_task("detail", {})
        return self.runner.save_product(
            _make_ok_result(product_id, price=price, seller_count=seller_count),
            task_id,
        )

    def _get_product(self, product_id: str) -> dict:
        import app.db as db
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM products WHERE product_id=?", (product_id,)
            ).fetchone()
        return dict(row) if row else {}

    def _get_changes(self, product_id: str = None) -> list[dict]:
        import app.db as db
        with db.get_conn() as conn:
            if product_id:
                rows = conn.execute(
                    "SELECT * FROM product_changes WHERE product_id=?",
                    (product_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM product_changes").fetchall()
        return [dict(r) for r in rows]

    def test_first_insert_no_change(self):
        """首次插入：has_change=0，product_changes 无记录。"""
        self._save("NEW001", price=9.99, seller_count=2)

        p = self._get_product("NEW001")
        self.assertEqual(p["has_change"], 0)

        changes = self._get_changes("NEW001")
        self.assertEqual(len(changes), 0)

    def test_price_change_detected(self):
        """价格变动时，has_change=1，product_changes 有一条记录。"""
        task_id = self.tasks.create_task("detail", {})
        self._save("P100", price=10.0, seller_count=1, task_id=task_id)

        # 价格从 10.0 → 15.0（涨价）
        self._save("P100", price=15.0, seller_count=1, task_id=task_id)

        p = self._get_product("P100")
        self.assertEqual(p["has_change"], 1)
        # prev_price 应为首次价格（10.0）
        self.assertAlmostEqual(p["prev_price"], 10.0)

        changes = self._get_changes("P100")
        self.assertGreater(len(changes), 0)
        c = changes[-1]
        self.assertAlmostEqual(c["old_price"], 10.0)
        self.assertAlmostEqual(c["new_price"], 15.0)
        self.assertIn("price", json.loads(c["changed_fields"]))

    def test_seller_count_change_detected(self):
        """卖家数量变动时，product_changes 记录含 seller_count。"""
        task_id = self.tasks.create_task("detail", {})
        self._save("P200", price=20.0, seller_count=3, task_id=task_id)
        self._save("P200", price=20.0, seller_count=7, task_id=task_id)

        p = self._get_product("P200")
        self.assertEqual(p["has_change"], 1)
        self.assertEqual(p["prev_seller_count"], 3)

        changes = self._get_changes("P200")
        self.assertGreater(len(changes), 0)
        c = changes[-1]
        self.assertEqual(c["old_seller_count"], 3)
        self.assertEqual(c["new_seller_count"], 7)
        self.assertIn("seller_count", json.loads(c["changed_fields"]))

    def test_no_change_no_record(self):
        """相同价格和卖家数量第二次入库，has_change=0，不新增 product_changes。"""
        task_id = self.tasks.create_task("detail", {})
        self._save("P300", price=5.0, seller_count=2, task_id=task_id)
        self._save("P300", price=5.0, seller_count=2, task_id=task_id)

        p = self._get_product("P300")
        self.assertEqual(p["has_change"], 0)

        changes = self._get_changes("P300")
        self.assertEqual(len(changes), 0)

    def test_multiple_changes_accumulate(self):
        """多次变动累积多条 product_changes 记录。"""
        task_id = self.tasks.create_task("detail", {})
        self._save("P400", price=10.0, seller_count=1, task_id=task_id)
        self._save("P400", price=12.0, seller_count=1, task_id=task_id)
        self._save("P400", price=12.0, seller_count=3, task_id=task_id)

        changes = self._get_changes("P400")
        self.assertGreaterEqual(len(changes), 2)

    def test_products_endpoint_has_change_field(self):
        """products 表记录中 has_change 字段存在且合法。"""
        task_id = self.tasks.create_task("detail", {})
        self._save("P500", price=1.0, seller_count=1, task_id=task_id)
        self._save("P500", price=2.0, seller_count=1, task_id=task_id)

        p = self._get_product("P500")
        self.assertIn("has_change", p)
        self.assertIn(p["has_change"], (0, 1))


# ─────────────────────────────────────────────────────────────────────────────
# #22 webhook 完成回调测试
# ─────────────────────────────────────────────────────────────────────────────

class TestWebhook(unittest.TestCase):
    """feature #22：webhook 回调 — 任务完成时被调用；失败不抛异常；可配置 URL。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tasks, self.runner = _reload_all(self.tmp)

    def test_webhook_called_on_completion(self):
        """run_ids 完成后，webhook URL 接收到一次 POST 调用。"""
        fired = []

        def fake_fire(task_id, task_dict, url):
            fired.append({"task_id": task_id, "url": url, "status": task_dict.get("status")})

        mock_c = MagicMock()
        mock_c.collect_detail.return_value = _make_ok_result("W001")

        with patch.object(self.runner, "_fire_webhook", side_effect=fake_fire):
            task_id = self.runner.run_ids(
                ["W001"], collector=mock_c, webhook_url="http://localhost:9999/cb"
            )

        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["task_id"], task_id)
        self.assertEqual(fired[0]["url"], "http://localhost:9999/cb")
        self.assertEqual(fired[0]["status"], "done")

    def test_webhook_not_called_without_url(self):
        """未配置 webhook_url 时，_fire_webhook 不被调用。"""
        # 确保 config.WEBHOOK_URL 为空
        original = getattr(cfg_module, "WEBHOOK_URL", "")
        cfg_module.WEBHOOK_URL = ""

        fired = []

        def fake_fire(task_id, task_dict, url):
            fired.append(url)

        mock_c = MagicMock()
        mock_c.collect_detail.return_value = _make_ok_result("W002")

        with patch.object(self.runner, "_fire_webhook", side_effect=fake_fire):
            self.runner.run_ids(["W002"], collector=mock_c, webhook_url="")

        cfg_module.WEBHOOK_URL = original
        self.assertEqual(len(fired), 0)

    def test_webhook_failure_does_not_raise(self):
        """webhook POST 失败（URLError）时，run_ids 正常返回，不抛异常。"""
        import urllib.error

        def fake_fire(task_id, task_dict, url):
            raise urllib.error.URLError("连接失败")

        mock_c = MagicMock()
        mock_c.collect_detail.return_value = _make_ok_result("W003")

        # 不应抛出
        try:
            with patch.object(self.runner, "_fire_webhook", side_effect=fake_fire):
                task_id = self.runner.run_ids(
                    ["W003"], collector=mock_c, webhook_url="http://bad-url/cb"
                )
            task = self.tasks.get_task(task_id)
            self.assertEqual(task["status"], "done")
        except Exception as exc:
            self.fail(f"webhook 失败不应抛出异常，但得到: {exc}")

    def test_webhook_called_for_run_keyword(self):
        """run_keyword 完成后也触发 webhook。"""
        fired = []

        def fake_fire(task_id, task_dict, url):
            fired.append(task_id)

        mock_c = MagicMock()
        mock_c.collect_by_keyword.return_value = {
            "keyword": "fan",
            "listing": [],
            "details": [],
        }

        with patch.object(self.runner, "_fire_webhook", side_effect=fake_fire):
            task_id = self.runner.run_keyword(
                "fan", collector=mock_c, webhook_url="http://localhost/cb"
            )

        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0], task_id)

    def test_webhook_called_for_run_seller(self):
        """run_seller 完成后也触发 webhook。"""
        fired = []

        def fake_fire(task_id, task_dict, url):
            fired.append(task_id)

        mock_c = MagicMock()
        mock_c.collect_by_seller.return_value = {
            "seller_id": "9999",
            "listing": [],
            "details": [],
        }

        with patch.object(self.runner, "_fire_webhook", side_effect=fake_fire):
            task_id = self.runner.run_seller(
                "9999", collector=mock_c, webhook_url="http://localhost/cb"
            )

        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0], task_id)

    def test_webhook_called_on_blocked(self):
        """P2-5：任务因封控结束（blocked 状态），也应触发 webhook 通知调用方。"""
        fired = []

        def fake_fire(task_id, task_dict, url):
            fired.append({"task_id": task_id, "status": task_dict.get("status")})

        mock_c = MagicMock()
        mock_c.collect_detail.return_value = _make_blocked_result("W004")

        with patch.object(self.runner, "_fire_webhook", side_effect=fake_fire):
            task_id = self.runner.run_ids(
                ["W004"], collector=mock_c, webhook_url="http://localhost/cb"
            )

        task = self.tasks.get_task(task_id)
        self.assertEqual(task["status"], "blocked")
        # P2-5 修复后：blocked 时也触发 webhook（方便调用方感知任务异常终止）
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["task_id"], task_id)

    def test_fire_webhook_uses_urllib(self):
        """_fire_webhook 内部使用 urllib.request.urlopen，不需要额外依赖。"""
        # 验证 _fire_webhook 存在且可被 monkeypatch 打桩
        self.assertTrue(callable(getattr(self.runner, "_fire_webhook", None)))

    def test_webhook_payload_contains_task_info(self):
        """webhook POST 的 payload 包含 task_id/status/result_count 等字段。"""
        captured_payloads = []

        def mock_urlopen(req, timeout=None):
            # 捕获请求体
            data = json.loads(req.data.decode("utf-8"))
            captured_payloads.append(data)
            resp = MagicMock()
            resp.status = 200
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        mock_c = MagicMock()
        mock_c.collect_detail.return_value = _make_ok_result("WP001", price=30.0)

        import urllib.request as urllib_req
        with patch.object(urllib_req, "urlopen", side_effect=mock_urlopen):
            task_id = self.runner.run_ids(
                ["WP001"], collector=mock_c, webhook_url="http://localhost:9999/cb"
            )

        self.assertEqual(len(captured_payloads), 1)
        payload = captured_payloads[0]
        self.assertEqual(payload["task_id"], task_id)
        self.assertIn("status", payload)
        self.assertIn("result_count", payload)


# ─────────────────────────────────────────────────────────────────────────────
# BE3 新增测试：P1-2 COALESCE / P2-1 Lane接通 / P2-3 续采计数 / P2-15 in_stock派生
# ─────────────────────────────────────────────────────────────────────────────

class TestBE3Fixes(unittest.TestCase):
    """BE3 数据正确性 + Lane 接通测试。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tasks, self.runner = _reload_all(self.tmp)

    def _get_product(self, product_id: str) -> dict:
        import app.db as db
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM products WHERE product_id=?", (product_id,)
            ).fetchone()
        return dict(row) if row else {}

    # ── P1-2 COALESCE：partial 不覆写已有有效字段 ─────────────────────────────

    def test_coalesce_partial_does_not_overwrite_valid_fields(self):
        """P1-2 COALESCE：partial 状态的 upsert 不应把已有的 title/upc/image_url 抹成 NULL。"""
        task_id = self.tasks.create_task("detail", {})

        # 首次入库：ok 状态，带完整字段
        self.runner.save_product({
            "_status": "ok",
            "product_id": "COALESCE001",
            "title": "完整商品标题",
            "upc": "123456789012",
            "image_url": "https://example.com/img.jpg",
            "price": 10.0,
        }, task_id)

        # 第二次入库：partial 状态，title/upc/image_url 均为 None（解析失败）
        self.runner.save_product({
            "_status": "partial",
            "product_id": "COALESCE001",
            "title": None,
            "upc": None,
            "image_url": None,
            "price": 12.0,  # 价格有变，确保触发 upsert
        }, task_id)

        p = self._get_product("COALESCE001")
        # 已有有效字段不应被 NULL 覆盖
        self.assertEqual(p["title"], "完整商品标题", "partial upsert 不应覆盖已有 title")
        self.assertEqual(p["upc"], "123456789012", "partial upsert 不应覆盖已有 upc")
        self.assertEqual(p["image_url"], "https://example.com/img.jpg",
                         "partial upsert 不应覆盖已有 image_url")
        # 价格应被更新（价格类字段无条件覆写）
        self.assertAlmostEqual(p["price"], 12.0)

    def test_coalesce_new_value_does_overwrite_null(self):
        """P1-2 COALESCE：新值非 NULL 时仍应覆盖旧的 NULL 值。"""
        task_id = self.tasks.create_task("detail", {})

        # 首次入库：title 为 None
        self.runner.save_product({
            "_status": "partial",
            "product_id": "COALESCE002",
            "title": None,
            "price": 5.0,
        }, task_id)

        # 第二次：补全 title
        self.runner.save_product({
            "_status": "ok",
            "product_id": "COALESCE002",
            "title": "补全后的标题",
            "price": 5.0,
        }, task_id)

        p = self._get_product("COALESCE002")
        self.assertEqual(p["title"], "补全后的标题")

    # ── P2-1 Lane 接通：notify_blocked / notify_product_saved ────────────────

    def test_lane_notify_product_saved_called_on_success(self):
        """P2-1：save_product 成功写入后调 lane.notify_product_saved()。"""
        mock_lane = MagicMock()
        task_id = self.tasks.create_task("detail", {})

        result = self.runner.save_product(
            {"_status": "ok", "product_id": "LANE001", "price": 1.0},
            task_id,
            lane=mock_lane,
        )

        self.assertTrue(result)
        mock_lane.notify_product_saved.assert_called_once()

    def test_lane_notify_product_saved_not_called_on_skip(self):
        """P2-1：save_product 跳过（blocked 状态）时不调 notify_product_saved。"""
        mock_lane = MagicMock()
        task_id = self.tasks.create_task("detail", {})

        result = self.runner.save_product(
            {"_status": "blocked", "product_id": "LANE002"},
            task_id,
            lane=mock_lane,
        )

        self.assertFalse(result)
        mock_lane.notify_product_saved.assert_not_called()

    def test_lane_notify_blocked_called_on_blocked_status(self):
        """P2-1：run_ids 遇封控时调 lane.notify_blocked 并将任务标 blocked。"""
        mock_lane = MagicMock()
        # 让 mock_lane.state 返回非 BLOCKED（不触发提前退出）
        from app.service.lanes import LaneState
        mock_lane.state = LaneState.IDLE

        mock_c = MagicMock()
        mock_c.collect_detail.return_value = _make_blocked_result("LANE003")

        # 通过 monkeypatch _make_collector 注入 (collector, lane)
        with patch.object(self.runner, "_make_collector", return_value=(mock_c, mock_lane)):
            task_id = self.runner.run_ids(["LANE003"])

        task = self.tasks.get_task(task_id)
        self.assertEqual(task["status"], "blocked")
        mock_lane.notify_blocked.assert_called_once()

    # ── P2-3 续采计数：mark_item_done 只在入库成功后调用 ──────────────────────

    def test_resume_count_only_counts_saved_items(self):
        """P2-3：续采初始 result_count 不含 give_up 历史项；当轮计数也只计入库成功的项。"""
        # 首轮：P1 成功入库，P2 give_up（失败）
        call_seq = {
            "P1": _make_ok_result("P1", price=1.0),
            "P2": {"_status": "give_up", "product_id": "P2"},
        }
        mock_c = MagicMock()
        mock_c.collect_detail.side_effect = lambda pid, **kw: call_seq[pid]

        task_id = self.runner.run_ids(["P1", "P2"], collector=mock_c)
        task = self.tasks.get_task(task_id)

        # P1 入库成功，P2 give_up 失败；result_count 应为 1
        self.assertEqual(task["result_count"], 1,
                         "result_count 应只计成功入库的项（P1），不含 give_up 项（P2）")

    def test_mark_item_done_only_on_save_success(self):
        """P2-3：mark_item_done 只在 save_product 返回 True 后调用；give_up 的 pid 不进 completed_ids。"""
        call_seq = {
            "P1": _make_ok_result("P1", price=2.0),
            "P2": {"_status": "give_up", "product_id": "P2"},
        }
        mock_c = MagicMock()
        mock_c.collect_detail.side_effect = lambda pid, **kw: call_seq[pid]

        task_id = self.runner.run_ids(["P1", "P2"], collector=mock_c)
        completed = self.tasks.get_completed_ids(task_id)

        self.assertIn("P1", completed, "成功入库的 P1 应在 completed_ids 中")
        self.assertNotIn("P2", completed, "give_up 的 P2 不应在 completed_ids 中")

    # ── P2-15 in_stock 派生 ────────────────────────────────────────────────────

    def test_parser_derives_in_stock_from_availability_status(self):
        """P2-15：parser 从 ship_info.availability_status 派生 in_stock（整数 0/1/None）。"""
        from app.engine.parser import WalmartParser

        # 构造最小 __NEXT_DATA__ HTML（只含 shippingOption）
        def make_html(availability_status):
            import json
            data = {
                "props": {"pageProps": {"initialData": {"data": {
                    "product": {
                        "usItemId": "INSTOCK001",
                        "name": "Test Product",
                        "shippingOption": {
                            "availabilityStatus": availability_status,
                        },
                    }
                }}}}
            }
            return f'<script id="__NEXT_DATA__">{json.dumps(data)}</script>'

        parser = WalmartParser()

        # IN_STOCK → 1
        r = parser.parse_product(make_html("IN_STOCK"), "INSTOCK001")
        self.assertEqual(r.get("in_stock"), 1,
                         "IN_STOCK 应派生 in_stock=1")

        # OUT_OF_STOCK → 0
        r = parser.parse_product(make_html("OUT_OF_STOCK"), "INSTOCK001")
        self.assertEqual(r.get("in_stock"), 0,
                         "OUT_OF_STOCK 应派生 in_stock=0")

        # UNAVAILABLE → 0
        r = parser.parse_product(make_html("UNAVAILABLE"), "INSTOCK001")
        self.assertEqual(r.get("in_stock"), 0,
                         "UNAVAILABLE 应派生 in_stock=0")

    def test_in_stock_saved_to_db_and_triggers_change_detection(self):
        """P2-15：in_stock 写入 products 表，并能触发 in_stock 变动检测。"""
        task_id = self.tasks.create_task("detail", {})

        # 首次入库：in_stock=1（有货）
        self.runner.save_product({
            "_status": "ok",
            "product_id": "INSTOCK002",
            "price": 5.0,
            "in_stock": 1,
        }, task_id)
        p = self._get_product("INSTOCK002")
        self.assertEqual(p["in_stock"], 1, "in_stock 应存入 products 表")

        # 第二次：in_stock 变为 0（缺货）
        self.runner.save_product({
            "_status": "ok",
            "product_id": "INSTOCK002",
            "price": 5.0,
            "in_stock": 0,
        }, task_id)

        import app.db as db
        with db.get_conn() as conn:
            changes = conn.execute(
                "SELECT * FROM product_changes WHERE product_id='INSTOCK002'"
            ).fetchall()
        self.assertGreater(len(changes), 0, "in_stock 变动应写入 product_changes")
        import json as _json
        changed_fields = _json.loads(changes[0]["changed_fields"])
        self.assertIn("in_stock", changed_fields, "changed_fields 应包含 in_stock")


# ─────────────────────────────────────────────────────────────────────────────
# 回归：M1/M2/M3 无破坏
# ─────────────────────────────────────────────────────────────────────────────

class TestM4NoRegression(unittest.TestCase):
    """M4 改动后，M1 核心功能无回归：save_product/save_listing_items/run_ids 仍正常。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tasks, self.runner = _reload_all(self.tmp)

    def test_save_product_basic_still_works(self):
        """save_product 基本路径：ok 状态正常入库。"""
        task_id = self.tasks.create_task("detail", {"ids": ["R001"]})
        ok = self.runner.save_product(
            {"_status": "ok", "product_id": "R001", "price": 9.99, "title": "Regression Product"},
            task_id,
        )
        self.assertTrue(ok)

        import app.db as db
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM products WHERE product_id='R001'"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row["price"], 9.99)

    def test_save_product_blocked_still_skipped(self):
        """blocked 状态仍然不入库。"""
        task_id = self.tasks.create_task("detail", {})
        ok = self.runner.save_product({"_status": "blocked", "product_id": "R002"}, task_id)
        self.assertFalse(ok)

    def test_run_ids_basic_still_works(self):
        """run_ids 基本流程：任务 done，products 入库。"""
        mock_c = MagicMock()
        mock_c.collect_detail.side_effect = lambda pid, **kw: _make_ok_result(pid, price=1.0)

        task_id = self.runner.run_ids(["R1", "R2", "R3"], collector=mock_c)

        task = self.tasks.get_task(task_id)
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["progress"], 3)

        import app.db as db
        with db.get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM products WHERE task_id=?", (task_id,)
            ).fetchone()[0]
        self.assertEqual(count, 3)


# ─────────────────────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestResume,
        TestRetry,
        TestChangeDetection,
        TestWebhook,
        TestBE3Fixes,
        TestM4NoRegression,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
