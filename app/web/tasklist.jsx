/* ============================================================
   tasklist.jsx — 任务列表与进度监控
   轮询模拟 · 进度条 · 批量勾选/删除 · 查看结果
   ============================================================ */
const PROG_COLOR = {
  done: "var(--ok)", failed: "var(--err)", blocked: "var(--blocked)",
  running: "var(--wm)", pending: "var(--border-strong)",
};

function TaskList({ tasks, sel, onToggleSel, onToggleAll, onView, onDelete, interval, setInterval_, onRefresh, refreshing }) {
  const selCount = sel.size;
  const allOn = tasks.length > 0 && tasks.every((t) => sel.has(t.id));

  return (
    <div className="card" style={{ display: "flex", flexDirection: "column" }}>
      <div className="card-h">
        <span className="ic"><Icon name="list" size={16} /></span>
        <h2>任务列表</h2>
        <span className="cnt tnum">{tasks.length}</span>
      </div>

      <div className="tl-toolbar">
        <div className="chk-wrap" style={{ display: "flex", alignItems: "center", gap: 8, paddingLeft: 4 }}>
          <Check on={allOn} onChange={onToggleAll} />
          <span style={{ fontSize: 11.5, fontWeight: 600, color: "var(--ink-3)" }}>
            {selCount > 0 ? `已选 ${selCount}` : "全选"}
          </span>
        </div>
        <div className="grow" />
        {selCount > 0 && (
          <button className="btn btn-danger btn-sm fadein" onClick={onDelete}>
            <Icon name="trash" size={13} />删除选中 ({selCount})
          </button>
        )}
        <span style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-3)", display: "flex", alignItems: "center", gap: 5 }}>
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--ok)", boxShadow: "0 0 0 3px var(--ok-bg)" }} />
          自动刷新
        </span>
        <select className="mini-sel tnum" value={interval} onChange={(e) => setInterval_(+e.target.value)}>
          {[3, 5, 10, 30].map((s) => <option key={s} value={s}>{s} 秒</option>)}
        </select>
        <button className="btn btn-sec btn-sm" onClick={onRefresh} title="立即刷新">
          <Icon name="refresh" size={13} className={refreshing ? "spin" : ""} />
        </button>
      </div>

      <div className="tl-scroll">
        {tasks.length === 0 ? (
          <div className="empty">
            <Icon name="list" size={30} style={{ opacity: .4 }} />
            <p>暂无任务，请在左侧新建采集任务</p>
          </div>
        ) : tasks.map((t) => <TaskRow key={t.id} t={t} sel={sel.has(t.id)} onToggleSel={onToggleSel} onView={onView} />)}
      </div>
    </div>
  );
}

function TaskRow({ t, sel, onToggleSel, onView }) {
  const pct = t.total > 0 ? Math.min(100, Math.round((t.progress / t.total) * 100)) : (t.status === "done" ? 100 : 0);
  const canView = t.result_count > 0 || t.status === "done";
  return (
    <div className={"trow" + (sel ? " sel" : "")}>
      <Check on={sel} onChange={() => onToggleSel(t.id)} />

      <div className="tcode">
        <b>#{t.id}</b>
        <span className="ty" style={{ display: "inline-flex", alignItems: "center", gap: 4, marginTop: 2 }}>
          <Icon name={TYPE_ICON[t.type]} size={10} />{TYPE_LABEL[t.type]}
        </span>
      </div>

      <div className="ttitle">
        <div className="q">{t.query}</div>
        <div className="meta">
          <span style={{ display: "inline-flex", alignItems: "center", gap: 3 }}>
            <Icon name="clock" size={10} />{relTime(t.updated_at)}
          </span>
          {t.backend_gtin && <span style={{ color: "var(--wm)", fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 3 }}>
            <Icon name="shield" size={10} />后台GTIN
          </span>}
          {t.error_msg && <span style={{ color: t.status === "blocked" ? "var(--blocked)" : "var(--err)", fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 220 }} title={t.error_msg}>{t.error_msg}</span>}
        </div>
      </div>

      <div className="tprog">
        <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
          <Badge status={t.status} pulse={t.status === "running"} />
          {t.result_count > 0 && <span style={{ fontSize: 10, fontWeight: 700, color: "var(--ok)" }}>{fmtInt(t.result_count)} 条</span>}
        </div>
        <div className="bar"><div className="fill" style={{ width: pct + "%", background: PROG_COLOR[t.status] }} /></div>
        <div className="txt tnum">
          {t.total > 0 ? `${fmtInt(t.progress)} / ${fmtInt(t.total)} · ${pct}%` : (t.status === "done" ? "已完成" : "—")}
        </div>
      </div>

      <div style={{ textAlign: "right" }}>
        <button className="btn btn-sec btn-sm" disabled={!canView} onClick={() => onView(t)}
          style={{ opacity: canView ? 1 : .4 }}>
          <Icon name="eye" size={13} />结果
        </button>
      </div>
    </div>
  );
}
window.TaskList = TaskList;
