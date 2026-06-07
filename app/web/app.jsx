/* ============================================================
   app.jsx — 组合 + 状态管理（已对接真实后端 window.API）
   轮询 /tasks /metrics /proxy/status /seller-session/status
   ============================================================ */
const SPARK_KEYS = {
  spark_requests: "total_requests",
  spark_success: "success_rate",
  spark_429: "total_429",
  spark_products: "total_products",
  spark_ip: "total_ip_used",
};

function normSession(s) {
  if (!s || !s.exists) return { exists: false, alive: false, age_min: 0, has_proxy: false, account: "未上报" };
  return {
    exists: true,
    alive: s.alive !== false,        // 不带 check 时 alive 缺省，视为有效
    age_min: s.age_min == null ? 0 : s.age_min,
    has_proxy: !!s.has_proxy,
    account: "本地上报会话",
  };
}

function App() {
  const [tasks, setTasks] = useState([]);
  const [lanes, setLanes] = useState([]);
  const [metrics, setMetrics] = useState(null);
  const [session, setSession] = useState({ exists: false, alive: false, age_min: 0, has_proxy: false, account: "—" });
  const [sel, setSel] = useState(() => new Set());
  const [viewTask, setViewTask] = useState(null);
  const [rotating, setRotating] = useState(null);
  const [interval_, setInterval_] = useState(5);
  const [refreshing, setRefreshing] = useState(false);
  const [apiKey, setApiKey] = useState(() => window.API.getKey());
  const [toasts, setToasts] = useState([]);

  const hist = useRef({ spark_requests: [], spark_success: [], spark_429: [], spark_products: [], spark_ip: [] });
  const toastSeq = useRef(0);
  const toast = useCallback((kind, msg) => {
    const id = ++toastSeq.current;
    setToasts((ts) => [...ts, { id, kind, msg }]);
    setTimeout(() => setToasts((ts) => ts.filter((t) => t.id !== id)), 3600);
  }, []);

  /* ---- 拉取并更新（容错：单项失败不影响其余）---- */
  const pull = useCallback(async (withCheck) => {
    const [t, m, l, s] = await Promise.allSettled([
      window.API.tasks(), window.API.metrics(), window.API.lanes(), window.API.session(withCheck),
    ]);
    if (t.status === "fulfilled") setTasks(t.value);
    else if (t.reason && t.reason.status === 401) toast("warn", "鉴权失败：请检查 X-API-Key");
    if (l.status === "fulfilled") setLanes(l.value);
    if (s.status === "fulfilled") setSession(normSession(s.value));
    if (m.status === "fulfilled") {
      const mm = m.value || {};
      const h = hist.current;
      Object.entries(SPARK_KEYS).forEach(([sk, mk]) => {
        const v = mm[mk];
        if (v != null) { h[sk] = [...h[sk].slice(-15), v]; }
      });
      setMetrics({
        ...mm,
        avg_yield_per_ip: mm.avg_yield_per_ip == null ? "—" : mm.avg_yield_per_ip,
        spark_requests: h.spark_requests.length > 1 ? h.spark_requests : null,
        spark_success: h.spark_success.length > 1 ? h.spark_success : null,
        spark_429: h.spark_429.length > 1 ? h.spark_429 : null,
        spark_products: h.spark_products.length > 1 ? h.spark_products : null,
        spark_ip: h.spark_ip.length > 1 ? h.spark_ip : null,
      });
    }
  }, [toast]);

  /* ---- 轮询 ---- */
  useEffect(() => {
    pull(true);  // 首次带 isbm 验活
    const h = setInterval(() => pull(false), interval_ * 1000);
    return () => clearInterval(h);
  }, [interval_, pull]);

  /* ---- 提交 ---- */
  async function handleSubmit(p) {
    try {
      let res;
      if (p.type === "ids") res = await window.API.submitIds(p.ids, p.backend_gtin);
      else if (p.type === "keyword") res = await window.API.submitKeyword({
        keyword: p.keyword, max_pages: p.max_pages, min_price: p.min_price,
        max_price: p.max_price, with_detail: p.with_detail, backend_gtin: p.backend_gtin });
      else res = await window.API.submitSeller({
        seller_id: p.seller_id, max_pages: p.max_pages, with_detail: p.with_detail, backend_gtin: p.backend_gtin });
      toast("ok", `任务 #${res.task_id} 已创建并加入队列`);
      pull(false);
    } catch (e) {
      toast("warn", "提交失败：" + e.message);
      throw e;  // 让 SubmitPanel 保留输入
    }
  }

  async function handleImport(file, tab, form) {
    try {
      const type = tab === "ids" ? "ids" : tab;
      const res = await window.API.importFile(file, { type, ...form });
      toast("ok", `已解析 ${res.parsed} 条 · 创建 ${res.created} 个任务`);
      pull(false);
    } catch (e) {
      toast("warn", "导入失败：" + e.message);
    }
  }

  function toggleSel(id) {
    setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }
  function toggleAll() {
    setSel((s) => s.size === tasks.length ? new Set() : new Set(tasks.map((t) => t.id)));
  }
  async function deleteSel() {
    const ids = [...sel];
    if (!ids.length) return;
    if (!window.confirm(`确认删除 ${ids.length} 个任务？\n将同时删除这些任务采集到的商品/列表数据，不可恢复。`)) return;
    try {
      const res = await window.API.deleteTasks(ids);
      const d = (res && res.deleted) || {};
      toast("warn", `已删除 ${d.tasks ?? ids.length} 个任务 · 商品 ${d.products ?? 0} · 列表 ${d.listings ?? 0}`);
      setSel(new Set());
      pull(false);
    } catch (e) { toast("warn", "删除失败：" + e.message); }
  }

  async function rotate(laneId) {
    setRotating(laneId);
    try {
      const res = await window.API.rotate(laneId);
      toast("ok", `Lane ${laneId} 已切换 → ${res.new_ip || "新 IP"}`);
      pull(false);
    } catch (e) {
      toast("warn", `Lane ${laneId} 换 IP 失败：${e.message}`);
    } finally { setRotating(null); }
  }

  async function manualRefresh() {
    setRefreshing(true);
    try { await pull(false); } finally { setTimeout(() => setRefreshing(false), 300); }
  }

  const blockedLanes = lanes.filter((l) => l.state === "blocked").length;

  return (
    <div className="app">
      {/* Header */}
      <header className="hdr">
        <div className="brand">
          <div className="brand-mark">
            <Icon name="cart" size={19} style={{ color: "#fff" }} sw={2} />
            <span style={{ position: "absolute", top: -3, right: -3 }}>
              <Icon name="spark" size={12} fill style={{ color: "var(--spark)", filter: "drop-shadow(0 1px 1px rgba(0,0,0,.2))" }} />
            </span>
          </div>
          <div className="brand-title">
            <b>沃尔玛采集服务</b>
            <span>COLLECTION CONSOLE</span>
          </div>
        </div>

        <div className="hdr-spacer" />

        <div className="hdr-pill">
          <span className="dot" style={{ background: blockedLanes ? "var(--blocked)" : "var(--ok)", boxShadow: `0 0 0 3px ${blockedLanes ? "var(--blocked-bg)" : "var(--ok-bg)"}` }} />
          代理 <b className="tnum">{Math.max(0, lanes.length - blockedLanes)}/{lanes.length}</b> 正常
          {blockedLanes > 0 && <span style={{ color: "var(--blocked)", fontWeight: 700 }}>· {blockedLanes} 封控</span>}
        </div>
        <div className="hdr-pill">
          <span className="dot" style={{ background: session.alive ? "var(--ok)" : "var(--err)", boxShadow: `0 0 0 3px ${session.alive ? "var(--ok-bg)" : "var(--err-bg)"}` }} />
          后台会话 <b>{session.alive ? "有效" : (session.exists ? "待验" : "未上报")}</b>
        </div>

        <div className="apikey">
          <label>X-API-KEY</label>
          <input type="password" value={apiKey}
            onChange={(e) => { setApiKey(e.target.value); window.API.setKey(e.target.value); }}
            onBlur={() => pull(false)} />
          <span className="ok"><Icon name="checkCircle" size={15} fill /></span>
        </div>
        <div className="avatar">OP</div>
      </header>

      {/* KPI strip */}
      {metrics && <MetricsStrip metrics={metrics} />}

      {/* Main grid */}
      <div className="main">
        <SubmitPanel onSubmit={handleSubmit} onImport={handleImport} />
        <TaskList
          tasks={tasks} sel={sel} onToggleSel={toggleSel} onToggleAll={toggleAll}
          onView={setViewTask} onDelete={deleteSel}
          interval={interval_} setInterval_={setInterval_}
          onRefresh={manualRefresh} refreshing={refreshing}
        />
        <div className="rail-r">
          <InfraRail lanes={lanes} onRotate={rotate} rotating={rotating} session={session} />
        </div>
      </div>

      {viewTask && <ResultDrawer task={viewTask} onClose={() => setViewTask(null)} onToast={toast} />}
      <ToastHost toasts={toasts} />
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
