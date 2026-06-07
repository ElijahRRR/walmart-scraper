"""任务批量删除（级联清理 products/listings/product_changes）测试。"""
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
from tests.test_m3_api import _get_test_client, VALID_KEY


def _seed_task_with_data(task_id: int):
    """造一个任务 + 其 products/listings/product_changes 行。"""
    import app.db as db
    with db.get_conn() as c:
        c.execute("INSERT INTO tasks (id, type, status, params) VALUES (?,?,?,?)",
                  (task_id, "detail", "done", "{}"))
        c.execute("INSERT INTO products (product_id, task_id) VALUES (?,?)",
                  (f"P{task_id}", task_id))
        c.execute("INSERT INTO listings (task_id, product_id) VALUES (?,?)",
                  (task_id, f"P{task_id}"))
        c.execute("INSERT INTO product_changes (product_id, task_id, old_price, new_price, "
                  "changed_fields) VALUES (?,?,?,?,?)",
                  (f"P{task_id}", task_id, 1.0, 2.0, '[\"price\"]'))


class TestTaskDelete(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.client, self.api = _get_test_client(self._tmp)
        import app.db as db
        self.db = db

    def _counts(self):
        with self.db.get_conn() as c:
            return {
                "tasks": c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
                "products": c.execute("SELECT COUNT(*) FROM products").fetchone()[0],
                "listings": c.execute("SELECT COUNT(*) FROM listings").fetchone()[0],
                "changes": c.execute("SELECT COUNT(*) FROM product_changes").fetchone()[0],
            }

    def test_delete_cascades_data(self):
        from app.service.tasks import delete_tasks
        _seed_task_with_data(101)
        _seed_task_with_data(102)
        self.assertEqual(self._counts()["products"], 2)

        stats = delete_tasks([101])
        self.assertEqual(stats["tasks"], 1)
        self.assertEqual(stats["products"], 1)
        self.assertEqual(stats["listings"], 1)
        self.assertEqual(stats["product_changes"], 1)
        # 101 全清，102 保留
        c = self._counts()
        self.assertEqual(c["tasks"], 1)
        self.assertEqual(c["products"], 1)
        with self.db.get_conn() as conn:
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM products WHERE task_id=101").fetchone())
            self.assertIsNotNone(conn.execute(
                "SELECT 1 FROM products WHERE task_id=102").fetchone())

    def test_delete_empty_noop(self):
        from app.service.tasks import delete_tasks
        self.assertEqual(delete_tasks([])["tasks"], 0)

    def test_endpoint_requires_key(self):
        r = self.client.post("/tasks/delete", json={"task_ids": [1]})
        self.assertEqual(r.status_code, 401)

    def test_endpoint_deletes(self):
        _seed_task_with_data(201)
        r = self.client.post("/tasks/delete", json={"task_ids": [201]},
                             headers={"X-API-Key": VALID_KEY})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["deleted"]["tasks"], 1)
        self.assertEqual(self._counts()["tasks"], 0)

    def test_endpoint_rejects_empty(self):
        r = self.client.post("/tasks/delete", json={"task_ids": []},
                             headers={"X-API-Key": VALID_KEY})
        self.assertEqual(r.status_code, 422)  # min_length=1


if __name__ == "__main__":
    unittest.main()
