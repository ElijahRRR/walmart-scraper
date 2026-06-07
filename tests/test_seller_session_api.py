"""POST /seller-session + GET /seller-session/status 端点测试（TestClient，不触网）。"""
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
from tests.test_m3_api import _get_test_client, VALID_KEY, BAD_KEY


class TestSellerSession(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        # 会话落盘到临时目录，避免污染真实 data/
        cfg_module.SELLER_SESSION_FILE = os.path.join(self._tmp, "seller_session.json")
        self.client, self.api = _get_test_client(self._tmp)
        # reload 后 api 模块读到的 config 是同一个对象，但确保路径生效
        self.api._seller_session_path  # noqa: B018 (存在性)

    def _session_body(self, with_proxy=True):
        b = {"headers": {"cookie": "a=1; b=2", "x-xsrf-token": "tok123",
                         "wm_svc.name": "x"},
             "browser_id": "BID1", "captured_at": 1700000000}
        if with_proxy:
            b["proxy"] = {"type": "socks5", "host": "1.2.3.4", "port": 9,
                          "user": "u", "pass": "p"}
        return b

    def test_requires_key(self):
        r = self.client.post("/seller-session", json=self._session_body())
        self.assertEqual(r.status_code, 401)

    def test_upload_and_status(self):
        # 上传
        r = self.client.post("/seller-session", json=self._session_body(),
                             headers={"X-API-Key": VALID_KEY})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["has_proxy"])
        self.assertTrue(os.path.exists(cfg_module.SELLER_SESSION_FILE))
        # 文件权限 0600（仅 owner 读写）
        mode = os.stat(cfg_module.SELLER_SESSION_FILE).st_mode & 0o777
        self.assertEqual(mode, 0o600)
        # 状态
        s = self.client.get("/seller-session/status", headers={"X-API-Key": VALID_KEY}).json()
        self.assertTrue(s["exists"])
        self.assertEqual(s["browser_id"], "BID1")
        self.assertTrue(s["has_proxy"])

    def test_reject_session_without_auth_headers(self):
        bad = {"headers": {"cookie": "a=1"}}  # 缺 x-xsrf-token
        r = self.client.post("/seller-session", json=bad,
                             headers={"X-API-Key": VALID_KEY})
        self.assertEqual(r.status_code, 400)

    def test_status_when_missing(self):
        s = self.client.get("/seller-session/status", headers={"X-API-Key": VALID_KEY}).json()
        self.assertFalse(s["exists"])


if __name__ == "__main__":
    unittest.main()
