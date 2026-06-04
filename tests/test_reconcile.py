"""启动复位僵尸任务测试。"""
import importlib, os, sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
import app.config as cfg

class TestReconcile(unittest.TestCase):
    def setUp(self):
        cfg.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        import app.db as db; importlib.reload(db); db.init_db()
        import app.service.tasks as T; importlib.reload(T); self.T = T
    def test_reconcile_marks_pending_running_failed(self):
        a = self.T.create_task("keyword", {"keyword": "x"})   # pending
        b = self.T.create_task("detail", {"ids": ["1"]}); self.T.update_status(b, "running")
        c = self.T.create_task("seller", {"seller_id": "9"})
        self.T.update_status(c, "running"); self.T.update_status(c, "done")  # 真实流程 pending→running→done
        n = self.T.reconcile_interrupted_tasks()
        self.assertEqual(n, 2)  # a(pending)+b(running) 复位, c(done)不动
        self.assertEqual(self.T.get_task(a)["status"], "failed")
        self.assertEqual(self.T.get_task(b)["status"], "failed")
        self.assertEqual(self.T.get_task(c)["status"], "done")
        self.assertEqual(self.T.reconcile_interrupted_tasks(), 0)  # 幂等
if __name__ == "__main__": unittest.main()
