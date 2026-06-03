"""M1 服务层测试：任务状态机 + 落库 + 三流程接入。

测试策略：
  - 全程使用内存 SQLite（tmp path via tmp_path fixture）
  - 所有 WalmartCollector 网络调用通过 monkeypatch 替换成桩函数
  - 断言任务状态流转、products 去重 upsert、listings 去重

运行：
    python -m pytest tests/test_m1_service.py -v
"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# 确保项目根目录在 sys.path（pytest 在项目根执行时已自动添加）
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ─────────────────────────────────────────────────────────────────────────────
# Fixture helpers：使用临时 SQLite 库，避免污染真实数据
# ─────────────────────────────────────────────────────────────────────────────

import tempfile

import app.config as cfg_module


def _setup_temp_db(tmp_dir: str) -> str:
    """返回临时库路径，同时覆盖 config.DB_PATH 与 db 模块的 _resolve_db_path。"""
    db_path = os.path.join(tmp_dir, "test_walmart.db")
    # 覆盖全局配置，使 db 模块使用临时路径
    cfg_module.DB_PATH = db_path
    # 重新导入 db（避免模块缓存旧路径）
    import importlib
    import app.db as db_module
    importlib.reload(db_module)
    db_module.init_db()
    return db_path


# ─────────────────────────────────────────────────────────────────────────────
# 复用 test_parser.py 的 fixture HTML：LASKO / SHARK / STRAW
# ─────────────────────────────────────────────────────────────────────────────

def _wrap(product: dict, idml: dict) -> str:
    return json.dumps({
        "props": {"pageProps": {"initialData": {"data": {
            "product": product, "idml": idml,
        }}}}
    })


LASKO_HTML = _wrap(
    {
        "usItemId": "42379869", "brand": "Lasko",
        "name": "Lasko 16\" Oscillating Pedestal Fan",
        "type": "Electric Household Fans",
        "canonicalUrl": "/ip/Lasko/42379869",
        "sellerName": "Walmart.com", "sellerId": "F55CDC31AB754BB68FE0B39041159D63",
        "sellerType": "INTERNAL", "fulfillmentType": None,
        "priceInfo": {"currentPrice": {"price": 29.97, "priceString": "$29.97",
                                       "currencyUnit": "USD"},
                      "wasPrice": {"price": 52}, "shipPrice": None},
        "shippingOption": {"availabilityStatus": "AVAILABLE"},
        "averageRating": 4.3, "numberOfReviews": 42916, "upc": "046013460691",
        "imageInfo": {"thumbnailUrl": "https://example.com/lasko.jpg",
                      "allImages": [{"url": "https://example.com/lasko.jpg"}]},
    },
    {"longDescription": "<p>Lasko fan</p>",
     "specifications": [{"name": "Speeds", "value": "3"}]},
)

SHARK_HTML = _wrap(
    {
        "usItemId": "14469755459", "brand": "Shark",
        "name": "Shark FlexBreeze Fan",
        "type": "Portable Fans",
        "canonicalUrl": "/ip/Shark-Fan/14469755459",
        "sellerName": "Omni Crest llc", "sellerId": "C2F9387D763546FE803E88B316BBEFA9",
        "sellerType": "EXTERNAL", "catalogSellerId": 101114637,
        "fulfillmentType": "FC",
        "transactableOfferCount": 2, "additionalOfferCount": 1,
        "priceInfo": {"currentPrice": {"price": 109.99, "currencyUnit": "USD"},
                      "wasPrice": {"price": 149.99}, "shipPrice": None},
        "shippingOption": {"availabilityStatus": "AVAILABLE"},
        "averageRating": 4.4, "numberOfReviews": 122, "upc": "622356666268",
        "imageInfo": {"thumbnailUrl": "https://example.com/shark.jpg",
                      "allImages": [{"url": "https://example.com/shark.jpg"}]},
    },
    {"longDescription": "", "specifications": []},
)

# 搜索页面 fixture（含两个 usItemId）
def _listing_html(items: list[dict]) -> str:
    return json.dumps({
        "props": {"pageProps": {"initialData": {
            "searchResult": {
                "itemStacks": [{"items": items}],
                "count": len(items),
                "paginationV2": {"maxPage": 1},
            }
        }}}
    })


LISTING_HTML = _listing_html([
    {
        "usItemId": "111",
        "name": "Fan A", "brand": "BrandA",
        "priceInfo": {"currentPrice": {"price": 25.0}},
        "averageRating": 4.5, "numberOfReviews": 100,
        "sellerName": "SellerA", "sellerId": "SA",
        "fulfillmentType": "FC",
        "canonicalUrl": "/ip/fan-a/111",
        "imageInfo": {"thumbnailUrl": "https://example.com/a.jpg"},
    },
    {
        "usItemId": "222",
        "name": "Fan B", "brand": "BrandB",
        "priceInfo": {"currentPrice": {"price": 35.0}},
        "averageRating": 4.0, "numberOfReviews": 50,
        "sellerName": "SellerB", "sellerId": "SB",
        "fulfillmentType": "MARKETPLACE",
        "canonicalUrl": "/ip/fan-b/222",
        "imageInfo": {"thumbnailUrl": "https://example.com/b.jpg"},
    },
])


# ─────────────────────────────────────────────────────────────────────────────
# 测试类
# ─────────────────────────────────────────────────────────────────────────────

class TestTaskStateMachine(unittest.TestCase):
    """#6 任务状态机测试"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = _setup_temp_db(self.tmp)
        import importlib
        import app.service.tasks as tasks_mod
        importlib.reload(tasks_mod)
        self.tasks = tasks_mod

    def test_create_task_pending(self):
        """create_task 返回正整数，初始状态 pending"""
        task_id = self.tasks.create_task("detail", {"ids": ["A", "B"]})
        self.assertIsInstance(task_id, int)
        self.assertGreater(task_id, 0)
        task = self.tasks.get_task(task_id)
        self.assertIsNotNone(task)
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["params"]["ids"], ["A", "B"])

    def test_status_flow_running_done(self):
        """pending → running → done 流转"""
        task_id = self.tasks.create_task("keyword", {"keyword": "fan"})
        self.assertEqual(self.tasks.get_task(task_id)["status"], "pending")

        self.tasks.update_status(task_id, "running")
        self.assertEqual(self.tasks.get_task(task_id)["status"], "running")

        self.tasks.update_status(task_id, "done")
        self.assertEqual(self.tasks.get_task(task_id)["status"], "done")

    def test_status_flow_failed(self):
        """running → failed，error_msg 入库"""
        task_id = self.tasks.create_task("seller", {"seller_id": "999"})
        self.tasks.update_status(task_id, "running")
        self.tasks.update_status(task_id, "failed", error_msg="网络超时")
        task = self.tasks.get_task(task_id)
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error_msg"], "网络超时")

    def test_status_flow_blocked(self):
        """running → blocked"""
        task_id = self.tasks.create_task("detail", {"ids": []})
        self.tasks.update_status(task_id, "running")
        self.tasks.update_status(task_id, "blocked", error_msg="IP封控")
        task = self.tasks.get_task(task_id)
        self.assertEqual(task["status"], "blocked")

    def test_update_progress(self):
        """progress/total/result_count 字段更新"""
        task_id = self.tasks.create_task("detail", {"ids": ["X", "Y", "Z"]})
        self.tasks.update_progress(task_id, progress=1, total=3, result_count=1)
        task = self.tasks.get_task(task_id)
        self.assertEqual(task["progress"], 1)
        self.assertEqual(task["total"], 3)
        self.assertEqual(task["result_count"], 1)

        self.tasks.update_progress(task_id, progress=3, result_count=2)
        task = self.tasks.get_task(task_id)
        self.assertEqual(task["progress"], 3)
        self.assertEqual(task["result_count"], 2)

    def test_list_tasks(self):
        """list_tasks 返回正确条数，倒序排列"""
        ids = [self.tasks.create_task("detail", {"ids": [str(i)]}) for i in range(3)]
        listed = self.tasks.list_tasks(limit=10)
        self.assertGreaterEqual(len(listed), 3)
        # 倒序：最新 id 在前
        id_list = [t["id"] for t in listed]
        self.assertEqual(sorted(id_list, reverse=True)[:3], id_list[:3])

    def test_invalid_type_raises(self):
        """非法 type_ 抛 ValueError"""
        with self.assertRaises(ValueError):
            self.tasks.create_task("invalid_type", {})

    def test_invalid_status_raises(self):
        """非法状态抛 ValueError"""
        task_id = self.tasks.create_task("detail", {})
        with self.assertRaises(ValueError):
            self.tasks.update_status(task_id, "unknown")


class TestSaveProduct(unittest.TestCase):
    """#7 详情结果落库测试"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _setup_temp_db(self.tmp)
        import importlib
        import app.service.tasks as tasks_mod
        import app.service.runner as runner_mod
        importlib.reload(tasks_mod)
        importlib.reload(runner_mod)
        self.tasks = tasks_mod
        self.runner = runner_mod

        from app.engine.parser import WalmartParser
        self.parser = WalmartParser()

    def test_save_product_ok(self):
        """parse_product(LASKO_HTML) 落库成功，字段正确"""
        task_id = self.tasks.create_task("detail", {"ids": ["42379869"]})
        result = self.parser.parse_product(LASKO_HTML)
        self.assertEqual(result["_status"], "ok")

        ok = self.runner.save_product(result, task_id)
        self.assertTrue(ok)

        import app.db as db
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM products WHERE product_id='42379869'"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["brand"], "Lasko")
        self.assertAlmostEqual(row["price"], 29.97)
        self.assertEqual(row["parse_status"], "ok")
        self.assertEqual(row["task_id"], task_id)

    def test_save_product_upsert(self):
        """同一 product_id 两次写入 → upsert，只有一行，price 更新"""
        task_id = self.tasks.create_task("detail", {"ids": ["42379869"]})
        result = self.parser.parse_product(LASKO_HTML)
        self.runner.save_product(result, task_id)

        # 修改价格，再存一次
        result2 = dict(result)
        result2["price"] = 19.97
        self.runner.save_product(result2, task_id)

        import app.db as db
        with db.get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM products WHERE product_id='42379869'"
            ).fetchone()[0]
            row = conn.execute(
                "SELECT price FROM products WHERE product_id='42379869'"
            ).fetchone()
        self.assertEqual(count, 1)
        self.assertAlmostEqual(row["price"], 19.97)

    def test_save_product_blocked_skipped(self):
        """_status=blocked 不写入"""
        task_id = self.tasks.create_task("detail", {})
        blocked = {
            "_status": "blocked",
            "product_id": "99999",
        }
        ok = self.runner.save_product(blocked, task_id)
        self.assertFalse(ok)

        import app.db as db
        with db.get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM products WHERE product_id='99999'"
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_save_product_no_id_skipped(self):
        """无 product_id → 跳过"""
        result = {"_status": "ok", "brand": "X"}
        ok = self.runner.save_product(result, None)
        self.assertFalse(ok)

    def test_save_product_multiple_types(self):
        """LASKO + SHARK 都落库，分别取出字段正确"""
        task_id = self.tasks.create_task("detail", {})
        r_lasko = self.parser.parse_product(LASKO_HTML)
        r_shark = self.parser.parse_product(SHARK_HTML)

        self.runner.save_product(r_lasko, task_id)
        self.runner.save_product(r_shark, task_id)

        import app.db as db
        with db.get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        self.assertEqual(count, 2)


class TestSaveListingItems(unittest.TestCase):
    """#8 列表结果落库测试"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _setup_temp_db(self.tmp)
        import importlib
        import app.service.tasks as tasks_mod
        import app.service.runner as runner_mod
        importlib.reload(tasks_mod)
        importlib.reload(runner_mod)
        self.tasks = tasks_mod
        self.runner = runner_mod

        from app.engine.parser import parse_listing
        self.parse_listing = parse_listing

    def test_save_listings(self):
        """parse_listing 输出 → listings 表，两条记录，字段正确"""
        task_id = self.tasks.create_task("keyword", {"keyword": "fan"})
        res = self.parse_listing(LISTING_HTML)
        self.assertEqual(res["_status"], "ok")
        self.assertEqual(len(res["items"]), 2)

        written = self.runner.save_listing_items(res["items"], task_id)
        self.assertEqual(written, 2)

        import app.db as db
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM listings WHERE task_id=?", (task_id,)
            ).fetchall()
        self.assertEqual(len(rows), 2)
        pids = {r["product_id"] for r in rows}
        self.assertIn("111", pids)
        self.assertIn("222", pids)
        # 校验字段
        row_111 = next(r for r in rows if r["product_id"] == "111")
        self.assertEqual(row_111["title"], "Fan A")
        self.assertAlmostEqual(row_111["price"], 25.0)
        self.assertEqual(row_111["task_id"], task_id)

    def test_listing_dedup_same_task(self):
        """同一任务内同一 product_id 第二次 INSERT OR IGNORE，不重复"""
        task_id = self.tasks.create_task("keyword", {})
        items = [{"product_id": "111", "title": "Fan A", "price": 25.0}]

        written1 = self.runner.save_listing_items(items, task_id)
        written2 = self.runner.save_listing_items(items, task_id)

        self.assertEqual(written1, 1)
        self.assertEqual(written2, 0)

        import app.db as db
        with db.get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM listings WHERE task_id=?", (task_id,)
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_listing_different_tasks(self):
        """不同任务的同一 product_id 都应入库"""
        t1 = self.tasks.create_task("keyword", {})
        t2 = self.tasks.create_task("keyword", {})
        items = [{"product_id": "111", "title": "Fan A"}]

        self.runner.save_listing_items(items, t1)
        self.runner.save_listing_items(items, t2)

        import app.db as db
        with db.get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        self.assertEqual(count, 2)

    def test_listing_skip_no_product_id(self):
        """无 product_id 的条目跳过"""
        task_id = self.tasks.create_task("keyword", {})
        items = [{"title": "Ghost Product"}]
        written = self.runner.save_listing_items(items, task_id)
        self.assertEqual(written, 0)


class TestRunFlows(unittest.TestCase):
    """#9 三流程接入测试（使用 mock collector）"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _setup_temp_db(self.tmp)
        import importlib
        import app.service.tasks as tasks_mod
        import app.service.runner as runner_mod
        importlib.reload(tasks_mod)
        importlib.reload(runner_mod)
        self.tasks = tasks_mod
        self.runner = runner_mod

        from app.engine.parser import WalmartParser, parse_listing
        self.parser = WalmartParser()
        self.parse_listing = parse_listing

    def _make_mock_collector_ids(self) -> MagicMock:
        """桩 collector：collect_detail 返回 LASKO 和 SHARK 解析结果"""
        results = {
            "42379869": self.parser.parse_product(LASKO_HTML),
            "14469755459": self.parser.parse_product(SHARK_HTML),
        }

        def _fake_collect_detail(product_id, url=None, with_all_sellers=False):
            return results.get(product_id, {"_status": "give_up", "product_id": product_id})

        mock = MagicMock()
        mock.collect_detail.side_effect = _fake_collect_detail
        return mock

    def _make_mock_collector_keyword(self) -> MagicMock:
        """桩 collector：collect_by_keyword 返回 listing + 两条详情"""
        listing_res = self.parse_listing(LISTING_HTML)
        detail_results = [
            self.parser.parse_product(LASKO_HTML),
            self.parser.parse_product(SHARK_HTML),
        ]

        mock = MagicMock()
        mock.collect_by_keyword.return_value = {
            "keyword": "fan",
            "price_range": (None, None),
            "listing": listing_res["items"],
            "count_listing": len(listing_res["items"]),
            "details": detail_results,
        }
        return mock

    def _make_mock_collector_seller(self) -> MagicMock:
        """桩 collector：collect_by_seller 返回 listing（无详情）"""
        listing_res = self.parse_listing(LISTING_HTML)

        mock = MagicMock()
        mock.collect_by_seller.return_value = {
            "seller_id": "9999",
            "seller": None,
            "listing": listing_res["items"],
            "count_listing": len(listing_res["items"]),
            "details": [],
        }
        return mock

    # ── ids 流程 ────────────────────────────────────────────────────────────

    def test_run_ids_task_flow(self):
        """run_ids：任务状态 pending→running→done，products 正确入库"""
        mock_c = self._make_mock_collector_ids()
        task_id = self.runner.run_ids(["42379869", "14469755459"], collector=mock_c)

        task = self.tasks.get_task(task_id)
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["progress"], 2)
        self.assertEqual(task["total"], 2)
        self.assertGreaterEqual(task["result_count"], 2)

    def test_run_ids_products_saved(self):
        """run_ids 结果正确落 products 表"""
        mock_c = self._make_mock_collector_ids()
        task_id = self.runner.run_ids(["42379869", "14469755459"], collector=mock_c)

        import app.db as db
        with db.get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM products WHERE task_id=?", (task_id,)
            ).fetchone()[0]
        self.assertEqual(count, 2)

    def test_run_ids_dedup(self):
        """run_ids 同一 product_id 运行两次：products 仍只有一行"""
        mock_c = self._make_mock_collector_ids()
        self.runner.run_ids(["42379869"], collector=mock_c)
        self.runner.run_ids(["42379869"], collector=mock_c)

        import app.db as db
        with db.get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM products WHERE product_id='42379869'"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    # ── keyword 流程 ────────────────────────────────────────────────────────

    def test_run_keyword_task_flow(self):
        """run_keyword：任务状态 done，listings/products 都有数据"""
        mock_c = self._make_mock_collector_keyword()
        task_id = self.runner.run_keyword("fan", with_detail=True, collector=mock_c)

        task = self.tasks.get_task(task_id)
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["type"], "keyword")

    def test_run_keyword_listings_saved(self):
        """run_keyword：listings 入库2条"""
        mock_c = self._make_mock_collector_keyword()
        task_id = self.runner.run_keyword("fan", with_detail=True, collector=mock_c)

        import app.db as db
        with db.get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM listings WHERE task_id=?", (task_id,)
            ).fetchone()[0]
        self.assertEqual(count, 2)

    def test_run_keyword_detail_products_saved(self):
        """run_keyword with_detail=True：详情也落 products"""
        mock_c = self._make_mock_collector_keyword()
        task_id = self.runner.run_keyword("fan", with_detail=True, collector=mock_c)

        import app.db as db
        with db.get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        self.assertGreaterEqual(count, 2)

    def test_run_keyword_no_detail(self):
        """run_keyword with_detail=False：只有 listings，无 products"""
        # mock 的 details 为空
        listing_res = self.parse_listing(LISTING_HTML)
        mock_c = MagicMock()
        mock_c.collect_by_keyword.return_value = {
            "keyword": "fan", "price_range": (None, None),
            "listing": listing_res["items"],
            "count_listing": 2,
            "details": [],
        }
        task_id = self.runner.run_keyword("fan", with_detail=False, collector=mock_c)

        import app.db as db
        with db.get_conn() as conn:
            prod_count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            list_count = conn.execute(
                "SELECT COUNT(*) FROM listings WHERE task_id=?", (task_id,)
            ).fetchone()[0]
        self.assertEqual(prod_count, 0)
        self.assertEqual(list_count, 2)

    # ── seller 流程 ─────────────────────────────────────────────────────────

    def test_run_seller_task_flow(self):
        """run_seller：任务状态 done，listings 入库"""
        mock_c = self._make_mock_collector_seller()
        task_id = self.runner.run_seller("9999", with_detail=False, collector=mock_c)

        task = self.tasks.get_task(task_id)
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["type"], "seller")

    def test_run_seller_listings_saved(self):
        """run_seller：listings 入库2条"""
        mock_c = self._make_mock_collector_seller()
        task_id = self.runner.run_seller("9999", with_detail=False, collector=mock_c)

        import app.db as db
        with db.get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM listings WHERE task_id=?", (task_id,)
            ).fetchone()[0]
        self.assertEqual(count, 2)

    # ── 异常降级 ────────────────────────────────────────────────────────────

    def test_run_ids_exception_marks_failed(self):
        """collector 抛异常 → 任务状态标 failed"""
        mock_c = MagicMock()
        mock_c.collect_detail.side_effect = RuntimeError("模拟网络超时")

        task_id = self.runner.run_ids(["00001"], collector=mock_c)
        task = self.tasks.get_task(task_id)
        self.assertEqual(task["status"], "failed")
        self.assertIn("模拟网络超时", task["error_msg"])


# ─────────────────────────────────────────────────────────────────────────────
# 入口（兼容直接运行）
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [TestTaskStateMachine, TestSaveProduct, TestSaveListingItems, TestRunFlows]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
