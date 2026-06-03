"""文件批量导入测试：importer 解析 + /collect/import 端点。

策略同 M3：临时 SQLite + TestClient + 对 _bg_run_* 打桩（不发真实网络）。
运行：python -m pytest tests/test_m8_import.py -v
"""
import importlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.config as cfg_module
from app.service.importer import parse_upload

VALID_KEY = "test-key-xyz"


# ─────────────────────────────────────────────────────────────────────────────
# importer 解析单元测试
# ─────────────────────────────────────────────────────────────────────────────

class TestParseUpload(unittest.TestCase):
    def test_txt_lines_dedupe_comment_header(self):
        content = b"usItemId\n111\n222\n# comment\n\n222\n333\n"
        out = parse_upload("ids.txt", content)
        # 表头 usItemId 丢弃、空行/注释跳过、222 去重
        self.assertEqual(out, ["111", "222", "333"])

    def test_csv_first_column(self):
        content = b"123,Lasko Fan,29.97\n456,Shark,109.99\n"
        out = parse_upload("ids.csv", content)
        self.assertEqual(out, ["123", "456"])

    def test_keywords_with_spaces(self):
        content = "straw bag\niphone case\n straw bag \n".encode("utf-8")
        out = parse_upload("kw.txt", content)
        self.assertEqual(out, ["straw bag", "iphone case"])

    def test_xlsx_first_column_float_strip(self):
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["id"])          # 表头
        ws.append([123.0])         # 数字被读成 float → 应去掉 .0
        ws.append([456])
        ws.append([None])          # 空单元格跳过
        ws.append(["789"])
        buf = io.BytesIO()
        wb.save(buf)
        out = parse_upload("ids.xlsx", buf.getvalue())
        self.assertEqual(out, ["123", "456", "789"])

    def test_xls_rejected(self):
        with self.assertRaises(RuntimeError):
            parse_upload("old.xls", b"whatever")

    def test_empty_returns_empty(self):
        self.assertEqual(parse_upload("e.txt", b"\n\n# only comment\n"), [])


# ─────────────────────────────────────────────────────────────────────────────
# /collect/import 端点测试
# ─────────────────────────────────────────────────────────────────────────────

class TestImportEndpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        cfg_module.DB_PATH = os.path.join(self.tmp, "test_m8.db")
        cfg_module.API_KEY = VALID_KEY
        import app.db as db_module
        importlib.reload(db_module)
        db_module.init_db()
        import app.api as api_module
        importlib.reload(api_module)
        # 打桩后台执行，避免真实网络
        api_module._bg_run_ids = MagicMock()
        api_module._bg_run_keyword = MagicMock()
        api_module._bg_run_seller = MagicMock()
        self.api = api_module
        from fastapi.testclient import TestClient
        self.client = TestClient(api_module.app, raise_server_exceptions=True)

    def _post(self, content, filename, **form):
        files = {"file": (filename, content)}
        data = {**form}
        return self.client.post("/collect/import", files=files, data=data,
                                headers={"X-API-Key": VALID_KEY})

    def test_ids_one_task(self):
        r = self._post(b"111\n222\n333\n", "ids.txt", type="ids", with_detail="true")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["type"], "ids")
        self.assertEqual(d["parsed"], 3)
        self.assertEqual(d["created"], 1)           # ids 合并为 1 个任务
        self.assertEqual(len(d["task_ids"]), 1)
        self.api._bg_run_ids.assert_called_once()

    def test_keyword_multi_tasks(self):
        r = self._post(b"straw bag\niphone case\nlego\n", "kw.txt",
                       type="keyword", with_detail="false", max_pages="3")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["parsed"], 3)
        self.assertEqual(d["created"], 3)           # 每关键词一个任务
        self.assertEqual(self.api._bg_run_keyword.call_count, 3)

    def test_seller_multi_tasks(self):
        r = self._post(b"103057684\n101114637\n", "sellers.txt",
                       type="seller", max_pages="5")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["created"], 2)
        self.assertEqual(self.api._bg_run_seller.call_count, 2)

    def test_empty_file_400(self):
        r = self._post(b"\n# nothing\n", "e.txt", type="ids")
        self.assertEqual(r.status_code, 400)

    def test_bad_type_400(self):
        r = self._post(b"111\n", "ids.txt", type="bogus")
        self.assertEqual(r.status_code, 400)

    def test_no_key_401(self):
        files = {"file": ("ids.txt", b"111\n")}
        r = self.client.post("/collect/import", files=files, data={"type": "ids"})
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
