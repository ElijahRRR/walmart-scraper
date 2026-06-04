"""M2 测试：ProxyPool 升级 + Lane 池 + 防封 + proxy_log 落库。

测试策略：
  - 对 ProxyPool._extract 打桩，返回假IP串，不发真实网络请求
  - 用桩网络驱动 lane 执行，注入封控响应，断言 BLOCKED 状态
  - 断言 proxy_log 落库正确（extract/block/yield 事件）
  - 全程使用临时 SQLite，隔离真实数据

运行：
    python -m pytest tests/test_m2_lanes.py -v
"""
import importlib
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.config as cfg_module


# ─────────────────────────────────────────────────────────────────────────────
# Fixture：临时 DB + 重置模块
# ─────────────────────────────────────────────────────────────────────────────

def _setup_temp_db(tmp_dir: str) -> str:
    db_path = os.path.join(tmp_dir, "test_m2.db")
    cfg_module.DB_PATH = db_path
    import app.db as db_module
    importlib.reload(db_module)
    db_module.init_db()
    return db_path


def _make_fake_ip_line(ip: str = "1.2.3.4", port: str = "443",
                        user: str = "u", pwd: str = "p") -> str:
    """生成 cliproxy 格式的 IP 行：ip:port:user:pwd"""
    return f"{ip}:{port}:{user}:{pwd}"


# ─────────────────────────────────────────────────────────────────────────────
# #10 ProxyPool 升级测试
# ─────────────────────────────────────────────────────────────────────────────

class TestProxyPoolUpgrade(unittest.TestCase):
    """feature #10：手动 rotate + get_status + auto_rotate 默认关"""

    def _make_pool(self, ip_sequence: list[str] = None):
        """创建打桩的 ProxyPool，_extract 按序返回假IP，禁止磁盘缓存读写。"""
        from app.engine.proxy import ProxyPool
        pool = ProxyPool(api_key="test-key", pace_min=0, pace_max=0,
                         auto_rotate=False)
        # 禁用磁盘缓存（_load_state 始终返回 False，_save_state 空操作）
        pool._load_state = lambda: False
        pool._save_state = lambda: None

        ip_seq = list(ip_sequence or ["1.2.3.4:443"])
        call_count = [0]

        def fake_extract() -> str:
            idx = min(call_count[0], len(ip_seq) - 1)
            ip_port = ip_seq[idx]
            call_count[0] += 1
            ip, port = ip_port.split(":")
            pool._extractions += 1
            pool._proxy = f"http://u:p@{ip}:{port}"
            pool._born_at = time.time()
            pool._uses = 0
            return pool._proxy

        pool._extract = fake_extract
        return pool, call_count

    def test_default_auto_rotate_false(self):
        """默认 auto_rotate=False"""
        from app.engine.proxy import ProxyPool
        pool = ProxyPool()
        self.assertFalse(pool._auto_rotate)

    def test_get_status_no_ip(self):
        """未获取过IP时 get_status 返回 proxy=None"""
        from app.engine.proxy import ProxyPool
        pool = ProxyPool(pace_min=0, pace_max=0)
        status = pool.get_status()
        self.assertIsNone(status["proxy"])
        self.assertEqual(status["born_at"], 0)
        self.assertEqual(status["age_sec"], 0.0)
        self.assertFalse(status["alive"])

    def test_get_status_after_current(self):
        """current() 之后 get_status 包含 IP / born_at / age_sec / uses / auto_rotate"""
        pool, _ = self._make_pool(["5.6.7.8:443"])
        proxy = pool.current()
        self.assertIsNotNone(proxy)
        status = pool.get_status()
        self.assertIsNotNone(status["proxy"])
        self.assertIn("5.6.7.8:443", status["proxy"])
        self.assertGreater(status["born_at"], 0)
        self.assertGreaterEqual(status["age_sec"], 0.0)
        self.assertIsInstance(status["uses"], int)
        self.assertFalse(status["auto_rotate"])

    def test_rotate_changes_ip_and_overwrites_state(self):
        """rotate() 后 IP 变化，且磁盘缓存被覆盖（_save_state 被调用）"""
        pool, call_count = self._make_pool(["1.1.1.1:443", "2.2.2.2:443"])
        # 先获取第一个IP
        pool.current()
        first_ip = pool.get_status()["proxy"]
        self.assertIn("1.1.1.1", first_ip)

        # 记录 _save_state 调用次数
        save_calls = [0]
        orig_save = pool._save_state

        def counting_save():
            save_calls[0] += 1
            orig_save()

        pool._save_state = counting_save

        # rotate
        pool.rotate("测试封控")
        second_ip = pool.get_status()["proxy"]

        # IP 应已变化
        self.assertIn("2.2.2.2", second_ip)
        # _save_state 应被调用（覆盖磁盘缓存）
        self.assertGreater(save_calls[0], 0)

    def test_rotate_resets_uses_to_zero(self):
        """rotate 后 uses 归零"""
        pool, _ = self._make_pool(["1.1.1.1:443", "2.2.2.2:443"])
        pool.current()
        pool._uses = 10  # 模拟已经用了10次
        pool.rotate("封控测试")
        status = pool.get_status()
        self.assertEqual(status["uses"], 0)

    def test_get_status_born_at_increases_age(self):
        """born_at 设置后 age_sec 递增"""
        pool, _ = self._make_pool(["3.3.3.3:443"])
        pool.current()
        s1 = pool.get_status()
        time.sleep(0.05)
        s2 = pool.get_status()
        self.assertGreaterEqual(s2["age_sec"], s1["age_sec"])


# ─────────────────────────────────────────────────────────────────────────────
# #11 Lane 池测试
# ─────────────────────────────────────────────────────────────────────────────

class TestLanePool(unittest.TestCase):
    """feature #11：N 条 lane，每 lane 独立IP，任务分发到空闲 lane"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _setup_temp_db(self.tmp)
        # 重置全局单例
        import app.service.lanes as lanes_mod
        importlib.reload(lanes_mod)
        self.lanes_mod = lanes_mod

    def _make_fake_pool(self, ip: str = "1.2.3.4:443"):
        """创建一个 _extract 打桩的 ProxyPool。"""
        from app.engine.proxy import ProxyPool
        pool = ProxyPool(pace_min=0, pace_max=0, auto_rotate=False)
        # 让 pool 直接返回内存中的假IP，不调用真实网络
        pool._proxy = f"http://u:p@{ip}"
        pool._born_at = time.time()
        pool._uses = 0
        return pool

    def test_lane_pool_creates_correct_n_lanes(self):
        """LanePool 创建指定数量的 lane"""
        pool = self.lanes_mod.LanePool(n_lanes=3)
        self.assertEqual(pool.n_lanes, 3)

    def test_lane_pool_initial_state_idle(self):
        """所有 lane 初始状态为 IDLE"""
        pool = self.lanes_mod.LanePool(n_lanes=2)
        statuses = pool.get_all_status()
        self.assertEqual(len(statuses), 2)
        for s in statuses:
            self.assertEqual(s["state"], "idle")

    def test_get_idle_lane_returns_first_idle(self):
        """get_idle_lane 返回空闲 lane"""
        pool = self.lanes_mod.LanePool(n_lanes=2)
        idle = pool.get_idle_lane()
        self.assertIsNotNone(idle)
        self.assertEqual(idle.state, self.lanes_mod.LaneState.IDLE)

    def test_lane_run_work_changes_state(self):
        """lane.run_work 执行完成后状态回到 IDLE"""
        pool = self.lanes_mod.LanePool(n_lanes=1)
        lane = pool.lane(0)
        executed = [False]

        def work():
            executed[0] = True

        result = lane.run_work(work)
        self.assertTrue(result)
        self.assertTrue(executed[0])
        self.assertEqual(lane.state, self.lanes_mod.LaneState.IDLE)

    def test_blocked_lane_refuses_work(self):
        """BLOCKED 状态的 lane 拒绝执行新任务"""
        pool = self.lanes_mod.LanePool(n_lanes=1)
        lane = pool.lane(0)
        lane.notify_blocked("测试封控")

        executed = [False]

        def work():
            executed[0] = True

        result = lane.run_work(work)
        self.assertFalse(result)
        self.assertFalse(executed[0])
        self.assertEqual(lane.state, self.lanes_mod.LaneState.BLOCKED)

    def test_lane_pool_get_lane_status(self):
        """get_lane_status 返回指定 lane 的状态"""
        pool = self.lanes_mod.LanePool(n_lanes=2)
        s = pool.get_lane_status(0)
        self.assertIsNotNone(s)
        self.assertEqual(s["lane_id"], 0)
        self.assertEqual(s["state"], "idle")

        # 不存在的 lane_id 返回 None
        self.assertIsNone(pool.get_lane_status(99))


# ─────────────────────────────────────────────────────────────────────────────
# #12 防封测试
# ─────────────────────────────────────────────────────────────────────────────

class TestLaneBlockDetection(unittest.TestCase):
    """feature #12：识别封控 → lane 进入 BLOCKED 状态 → 不自动换IP"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _setup_temp_db(self.tmp)
        import app.service.lanes as lanes_mod
        importlib.reload(lanes_mod)
        self.lanes_mod = lanes_mod
        # 重置全局单例
        lanes_mod.reset_lane_pool()

    def test_notify_blocked_sets_state(self):
        """notify_blocked 把 lane 状态设为 BLOCKED"""
        lane = self.lanes_mod.Lane(0, api_key="", pace_min=0, pace_max=0)
        self.assertEqual(lane.state, self.lanes_mod.LaneState.IDLE)
        lane.notify_blocked("封控 HTTP403")
        self.assertEqual(lane.state, self.lanes_mod.LaneState.BLOCKED)
        self.assertEqual(lane.get_status().last_block, "封控 HTTP403")

    def test_notify_blocked_does_not_auto_rotate(self):
        """命中封控后，ProxyPool 的 IP 未改变（没有自动换IP）"""
        from app.engine.proxy import ProxyPool

        fake_ip = "9.8.7.6:443"
        lane = self.lanes_mod.Lane(0, api_key="", pace_min=0, pace_max=0)
        # 手动给 pool 注入一个假IP
        lane._pool._proxy = f"http://u:p@{fake_ip}"
        lane._pool._born_at = time.time()
        lane._pool._uses = 5

        lane.notify_blocked("封控 HTTP429")

        # IP 应该仍是原来的（没有换）
        ps = lane._pool.get_status()
        self.assertIn("9.8.7.6:443", ps.get("proxy", ""))
        self.assertEqual(lane.state, self.lanes_mod.LaneState.BLOCKED)

    def test_blocked_lane_work_rejected_no_ip_change(self):
        """BLOCKED lane 拒绝任务，且 ProxyPool IP 未变（不自动换）"""
        lane = self.lanes_mod.Lane(0, api_key="", pace_min=0, pace_max=0)
        lane._pool._proxy = "http://u:p@10.0.0.1:443"
        lane._pool._born_at = time.time()

        lane.notify_blocked("HTTP 403")
        original_proxy = lane._pool._proxy

        # 尝试执行工作，应被拒绝
        executed = [False]
        lane.run_work(lambda: executed.__setitem__(0, True))
        self.assertFalse(executed[0])

        # IP 未变
        self.assertEqual(lane._pool._proxy, original_proxy)

    def test_collector_is_blocked_recognition(self):
        """复用 collector._is_blocked 逻辑：识别403/429/waiting-room/验证码"""
        from app.engine.collector import WalmartCollector
        c = WalmartCollector.__new__(WalmartCollector)

        # 封控场景
        self.assertTrue(c._is_blocked(403, ""))
        self.assertTrue(c._is_blocked(429, ""))
        self.assertTrue(c._is_blocked(500, ""))
        self.assertTrue(c._is_blocked(503, ""))
        self.assertTrue(c._is_blocked(200, "px-captcha some text"))
        self.assertTrue(c._is_blocked(200, "Robot or human check"))
        self.assertTrue(c._is_blocked(200, "Verify your identity page"))
        self.assertTrue(c._is_blocked(200, "/blocked redirect"))

        # 非封控场景
        self.assertFalse(c._is_blocked(200, "normal page content __NEXT_DATA__"))
        self.assertFalse(c._is_blocked(404, "not found"))

    def test_lane_blocked_status_has_last_block_reason(self):
        """封控状态下 get_status 包含 last_block 原因和时间戳"""
        lane = self.lanes_mod.Lane(0, api_key="", pace_min=0, pace_max=0)
        before_ts = time.time()
        lane.notify_blocked("封控 HTTP429 waiting-room")
        after_ts = time.time()

        s = lane.get_status()
        self.assertEqual(s.last_block, "封控 HTTP429 waiting-room")
        self.assertGreaterEqual(s.last_block_at, before_ts)
        self.assertLessEqual(s.last_block_at, after_ts + 0.1)

    def test_resume_lane_resets_blocked_state(self):
        """resume() 后 lane 状态重置为 IDLE，且 ProxyPool 有新IP"""
        from app.engine.proxy import ProxyPool

        lane = self.lanes_mod.Lane(0, api_key="", pace_min=0, pace_max=0)
        lane._pool._proxy = "http://u:p@1.1.1.1:443"
        lane._pool._born_at = time.time() - 60
        lane._pool._uses = 3

        lane.notify_blocked("封控")
        self.assertEqual(lane.state, self.lanes_mod.LaneState.BLOCKED)

        # 打桩 rotate，使其不发真实网络请求
        new_ip = "2.2.2.2:443"
        rotate_called = [False]

        def fake_rotate(reason=""):
            rotate_called[0] = True
            lane._pool._proxy = f"http://u:p@{new_ip}"
            lane._pool._born_at = time.time()
            lane._pool._uses = 0
            return lane._pool._proxy

        lane._pool.rotate = fake_rotate

        result_ip = lane.resume()

        self.assertTrue(rotate_called[0])
        self.assertEqual(lane.state, self.lanes_mod.LaneState.IDLE)
        self.assertIsNone(lane.get_status().last_block)
        self.assertIn("2.2.2.2", result_ip)


# ─────────────────────────────────────────────────────────────────────────────
# #13 proxy_log 落库测试
# ─────────────────────────────────────────────────────────────────────────────

class TestProxyLogDB(unittest.TestCase):
    """feature #13：proxy_log 记录 extract / block / yield 事件"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = _setup_temp_db(self.tmp)
        import app.service.lanes as lanes_mod
        importlib.reload(lanes_mod)
        self.lanes_mod = lanes_mod
        lanes_mod.reset_lane_pool()

    def _get_proxy_logs(self, ip=None, event=None):
        """从临时DB查询 proxy_log。"""
        import app.db as db
        with db.get_conn() as conn:
            if ip and event:
                rows = conn.execute(
                    "SELECT * FROM proxy_log WHERE ip=? AND event=?", (ip, event)
                ).fetchall()
            elif ip:
                rows = conn.execute(
                    "SELECT * FROM proxy_log WHERE ip=?", (ip,)
                ).fetchall()
            elif event:
                rows = conn.execute(
                    "SELECT * FROM proxy_log WHERE event=?", (event,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM proxy_log").fetchall()
        return [dict(r) for r in rows]

    def test_log_proxy_event_extract(self):
        """_log_proxy_event 写入 extract 事件"""
        self.lanes_mod._log_proxy_event("5.5.5.5:443", "extract", count=0, detail="lane 0 初始化")
        logs = self._get_proxy_logs("5.5.5.5:443", "extract")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["event"], "extract")
        self.assertEqual(logs[0]["count"], 0)

    def test_log_proxy_event_block(self):
        """_log_proxy_event 写入 block 事件，含封控原因"""
        self.lanes_mod._log_proxy_event("6.6.6.6:443", "block", count=5, detail="封控 HTTP403")
        logs = self._get_proxy_logs("6.6.6.6:443", "block")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["count"], 5)
        self.assertIn("403", logs[0]["detail"])

    def test_log_proxy_event_yield(self):
        """_log_proxy_event 写入 yield 事件，含产出计数"""
        self.lanes_mod._log_proxy_event("7.7.7.7:443", "yield", count=42, detail="手动恢复")
        logs = self._get_proxy_logs("7.7.7.7:443", "yield")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["count"], 42)

    def test_notify_blocked_writes_block_log(self):
        """notify_blocked 触发 block 事件入 proxy_log"""
        lane = self.lanes_mod.Lane(0, api_key="", pace_min=0, pace_max=0)
        fake_ip = "8.8.8.8:443"
        lane._pool._proxy = f"http://u:p@{fake_ip}"
        lane._pool._born_at = time.time()
        lane._pool._uses = 3
        # 模拟已产出3个商品
        lane._ip_product_count = 3

        lane.notify_blocked("封控 HTTP429")
        logs = self._get_proxy_logs(fake_ip, "block")
        self.assertGreater(len(logs), 0)
        self.assertEqual(logs[0]["count"], 3)

    def test_notify_product_saved_updates_count(self):
        """notify_product_saved 累加 total_products 和 _ip_product_count"""
        lane = self.lanes_mod.Lane(0, api_key="", pace_min=0, pace_max=0)
        self.assertEqual(lane.get_status().total_products, 0)
        lane.notify_product_saved()
        lane.notify_product_saved()
        s = lane.get_status()
        self.assertEqual(s.total_products, 2)
        self.assertEqual(lane._ip_product_count, 2)

    def test_resume_writes_yield_and_extract_logs(self):
        """resume() 写入 yield（旧IP产出）和 extract（新IP）两条记录"""
        lane = self.lanes_mod.Lane(0, api_key="", pace_min=0, pace_max=0)
        old_ip = "11.11.11.11:443"
        new_ip = "22.22.22.22:443"
        lane._pool._proxy = f"http://u:p@{old_ip}"
        lane._pool._born_at = time.time() - 60
        lane._pool._uses = 5
        lane._ip_product_count = 5

        lane.notify_blocked("封控")

        # 打桩 rotate
        def fake_rotate(reason=""):
            lane._pool._proxy = f"http://u:p@{new_ip}"
            lane._pool._born_at = time.time()
            lane._pool._uses = 0
            # 真实 rotate→_extract 会触发 on_extract 回调记 extract 日志，此处显式模拟
            if lane._pool._on_extract:
                lane._pool._on_extract(new_ip)
            return lane._pool._proxy

        lane._pool.rotate = fake_rotate

        lane.resume()

        # 旧IP的 yield 事件
        yield_logs = self._get_proxy_logs(old_ip, "yield")
        self.assertGreater(len(yield_logs), 0)
        # 新IP的 extract 事件
        extract_logs = self._get_proxy_logs(new_ip, "extract")
        self.assertGreater(len(extract_logs), 0)

    def test_proxy_log_per_ip_yield_count(self):
        """每IP寿命内的产出商品数可从 proxy_log 的 yield 记录算出"""
        # 模拟一个IP产出10个商品后被封，记 yield=10
        self.lanes_mod._log_proxy_event("33.33.33.33:443", "extract", 0, "初始化")
        self.lanes_mod._log_proxy_event("33.33.33.33:443", "yield", 10, "手动换IP")

        logs = self._get_proxy_logs("33.33.33.33:443")
        total_yield = sum(r["count"] for r in logs if r["event"] == "yield")
        self.assertEqual(total_yield, 10)

    def test_multiple_ips_in_proxy_log(self):
        """多个IP的记录互不干扰"""
        for i in range(3):
            ip = f"1{i}.0.0.1:443"
            self.lanes_mod._log_proxy_event(ip, "extract", 0)
            self.lanes_mod._log_proxy_event(ip, "yield", i * 5)

        logs = self._get_proxy_logs()
        self.assertEqual(len(logs), 6)  # 3 extract + 3 yield


# ─────────────────────────────────────────────────────────────────────────────
# 集成：桩网络驱动 lane 跑任务，注入封控响应
# ─────────────────────────────────────────────────────────────────────────────

class TestLaneBlockedIntegration(unittest.TestCase):
    """端到端集成：桩网络触发封控 → lane BLOCKED → proxy_log 有记录"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _setup_temp_db(self.tmp)
        import app.service.lanes as lanes_mod
        importlib.reload(lanes_mod)
        self.lanes_mod = lanes_mod
        lanes_mod.reset_lane_pool()

    def _get_proxy_logs_by_event(self, event: str):
        import app.db as db
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM proxy_log WHERE event=?", (event,)
            ).fetchall()
        return [dict(r) for r in rows]

    def test_blocked_work_fn_marks_lane_and_logs(self):
        """工作函数内部检测到封控 → 通知 lane → BLOCKED + proxy_log 有 block 记录"""
        from app.engine.collector import WalmartCollector
        lane = self.lanes_mod.Lane(0, api_key="", pace_min=0, pace_max=0)
        fake_ip = "99.88.77.66:443"
        lane._pool._proxy = f"http://u:p@{fake_ip}"
        lane._pool._born_at = time.time()
        lane._pool._uses = 0

        def blocked_work_fn():
            """模拟 runner 内部：collector 返回 blocked 结果，lane 被通知"""
            # 模拟 collector._get 返回 None（blocked场景）
            # runner 检测到后调用 lane.notify_blocked
            blocked_result = {"_status": "blocked", "product_id": "X001"}
            # 在真实实现里，runner 会判断 _status==blocked 并调用 lane.notify_blocked
            # 这里直接模拟
            lane.notify_product_saved()  # 模拟采到1个
            lane.notify_blocked("封控 HTTP403")

        lane.run_work(blocked_work_fn)

        # lane 应处于 BLOCKED 状态
        self.assertEqual(lane.state, self.lanes_mod.LaneState.BLOCKED)

        # proxy_log 应有 block 记录
        block_logs = self._get_proxy_logs_by_event("block")
        self.assertGreater(len(block_logs), 0)
        self.assertEqual(block_logs[0]["ip"], fake_ip)

    def test_blocked_lane_not_auto_rotated(self):
        """封控后，ProxyPool 的 IP 保持不变（未自动换）"""
        lane = self.lanes_mod.Lane(0, api_key="", pace_min=0, pace_max=0)
        original_proxy = "http://u:p@55.55.55.55:443"
        lane._pool._proxy = original_proxy
        lane._pool._born_at = time.time()

        lane.notify_blocked("封控 HTTP429")

        # IP 未变
        self.assertEqual(lane._pool._proxy, original_proxy)
        self.assertEqual(lane.state, self.lanes_mod.LaneState.BLOCKED)


# ─────────────────────────────────────────────────────────────────────────────
# BE2 修复验证：P1-3 pace 加锁 / P2-8 rotate sleep 锁外 / P2-7 state_file 隔离
# ─────────────────────────────────────────────────────────────────────────────

class TestBE2Fixes(unittest.TestCase):
    """BE2 代理/并发修复的专项验证测试。"""

    def _make_pool_no_io(self, pace_min=0.0, pace_max=0.0,
                         state_file=None):
        """创建打桩 ProxyPool：禁止磁盘 I/O 和真实网络请求。"""
        from app.engine.proxy import ProxyPool
        kw = dict(pace_min=pace_min, pace_max=pace_max, auto_rotate=False)
        if state_file is not None:
            kw["state_file"] = state_file
        pool = ProxyPool(**kw)
        pool._load_state = lambda: False
        pool._save_state = lambda: None
        pool._extract = lambda: "http://u:p@1.2.3.4:443"
        return pool

    # ── P1-3：pace() 加锁 —— 两个线程不会同时绕过节速 ────────────────────────

    def test_pace_serializes_concurrent_requests(self):
        """P1-3：pace() 对 _uses 的并发更新是原子的，不丢计数。"""
        from app.engine.proxy import ProxyPool
        pool = self._make_pool_no_io(pace_min=0.0, pace_max=0.0)
        N = 100
        threads = [threading.Thread(target=pool.pace) for _ in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 100 次并发调用，_uses 必须恰好等于 N（原子 +=1）
        self.assertEqual(pool._uses, N)

    def test_pace_timestamps_are_monotonic_under_concurrency(self):
        """P1-3：并发 pace() 后 _last_req_at 被正确预占，不小于 now。"""
        pool = self._make_pool_no_io(pace_min=0.0, pace_max=0.0)
        before = time.time()
        threads = [threading.Thread(target=pool.pace) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 预占后 _last_req_at >= before（不会被并发写回过去的时间）
        self.assertGreaterEqual(pool._last_req_at, before)

    # ── P2-8：rotate() sleep 在锁外，get_status 不被阻塞 ────────────────────

    def test_rotate_sleep_outside_lock_allows_concurrent_get_status(self):
        """P2-8：rotate() 的 sleep 期间，get_status() 可以立即返回。"""
        from app.engine.proxy import ProxyPool, BLOCK_BACKOFF

        pool = self._make_pool_no_io()
        pool.current()  # 先初始化 IP

        # 用极短的 BLOCK_BACKOFF 替换，让测试快跑
        TINY_BACKOFF = 0.05
        status_query_results = []

        def do_rotate():
            # 用 monkeypatch 方式只替换此次测试的 sleep
            import app.engine.proxy as proxy_mod
            orig = proxy_mod.BLOCK_BACKOFF
            proxy_mod.BLOCK_BACKOFF = TINY_BACKOFF
            try:
                pool.rotate("test")
            finally:
                proxy_mod.BLOCK_BACKOFF = orig

        t_rotate = threading.Thread(target=do_rotate)
        t_rotate.start()
        time.sleep(0.01)  # 确保 rotate 已进入 sleep 阶段

        # 此时 rotate 正在 sleep（锁已释放），get_status 应能立即返回
        t0 = time.time()
        status = pool.get_status()
        elapsed = time.time() - t0

        t_rotate.join()

        # get_status 应在远小于 TINY_BACKOFF 的时间内返回（<10ms）
        self.assertLess(elapsed, 0.03,
                        f"get_status 耗时 {elapsed:.3f}s，应 < 0.03s（rotate sleep 不应持锁）")
        self.assertIsNotNone(status)

    # ── P2-7：state_file 按 lane 区分 ────────────────────────────────────────

    def test_proxy_pool_uses_custom_state_file(self):
        """P2-7：ProxyPool 使用传入的 state_file，不使用模块级 STATE_FILE。"""
        from app.engine.proxy import ProxyPool, STATE_FILE
        import tempfile, os

        with tempfile.TemporaryDirectory() as tmpdir:
            custom_file = os.path.join(tmpdir, "lane1.json")
            pool = ProxyPool(pace_min=0, pace_max=0, state_file=custom_file)
            # 禁止网络；直接设置内存 IP 并保存
            pool._proxy = "http://u:p@9.9.9.9:443"
            pool._born_at = time.time()
            pool._save_state()

            # 自定义文件应存在且包含 IP
            self.assertTrue(os.path.exists(custom_file))
            import json
            with open(custom_file) as f:
                st = json.load(f)
            self.assertIn("9.9.9.9", st["proxy"])

            # 模块级 STATE_FILE 不应被写入（目录隔离保证）
            self.assertNotEqual(custom_file, STATE_FILE)

    def test_lane_uses_per_lane_state_file(self):
        """P2-7：Lane 创建的 ProxyPool state_file 按 lane_id 区分。"""
        import app.service.lanes as lanes_mod
        from app.engine.proxy import _PROJECT_ROOT

        lane0 = lanes_mod.Lane(0, api_key="", pace_min=0, pace_max=0)
        lane1 = lanes_mod.Lane(1, api_key="", pace_min=0, pace_max=0)
        lane2 = lanes_mod.Lane(2, api_key="", pace_min=0, pace_max=0)

        # lane 0 使用默认文件名
        self.assertEqual(lane0._pool._state_file,
                         str(_PROJECT_ROOT / "proxy_state.json"))
        # lane 1/2 使用 proxy_state_{id}.json
        self.assertEqual(lane1._pool._state_file,
                         str(_PROJECT_ROOT / "proxy_state_1.json"))
        self.assertEqual(lane2._pool._state_file,
                         str(_PROJECT_ROOT / "proxy_state_2.json"))

        # 三个 lane 的 state_file 互不相同
        files = {lane0._pool._state_file, lane1._pool._state_file,
                 lane2._pool._state_file}
        self.assertEqual(len(files), 3)

    def test_default_pool_uses_module_state_file(self):
        """P2-7：不传 state_file 时，ProxyPool 使用模块级 STATE_FILE（向后兼容）。"""
        from app.engine.proxy import ProxyPool, STATE_FILE
        pool = ProxyPool(pace_min=0, pace_max=0)
        self.assertEqual(pool._state_file, STATE_FILE)

    # ── P2-6：LanePool 多 lane 时 get_idle_lane 正常工作 ─────────────────────

    def test_lane_pool_get_idle_lane_multi_lane(self):
        """P2-6：LANES>1 场景下 get_idle_lane 仍正常返回空闲 lane。"""
        import importlib
        import app.service.lanes as lanes_mod
        importlib.reload(lanes_mod)

        pool = lanes_mod.LanePool(n_lanes=3)
        idle = pool.get_idle_lane()
        self.assertIsNotNone(idle)
        # lane 0 是 IDLE 状态
        self.assertEqual(idle.lane_id, 0)

        # 标记 lane 0 为 BLOCKED，get_idle_lane 应返回 lane 1
        pool.lanes[0].notify_blocked("测试")
        next_idle = pool.get_idle_lane()
        self.assertIsNotNone(next_idle)
        self.assertEqual(next_idle.lane_id, 1)


# ─────────────────────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestProxyPoolUpgrade,
        TestLanePool,
        TestLaneBlockDetection,
        TestProxyLogDB,
        TestLaneBlockedIntegration,
        TestBE2Fixes,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
