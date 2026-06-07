/* ============================================================
   metrics.jsx — 顶部 KPI 指标条
   ============================================================ */
function MetricsStrip({ metrics }) {
  const m = metrics;
  const cards = [
    { label: "总请求", val: fmtInt(m.total_requests), icon: "list",
      iBg: "var(--wm-50)", iFg: "var(--wm)", spark: m.spark_requests, sc: "var(--wm)", st: "line",
      sub: null },
    { label: "成功率", val: fmtPct(m.success_rate), icon: "checkCircle",
      iBg: "var(--ok-bg)", iFg: "var(--ok)", spark: m.spark_success, sc: "var(--ok)", st: "line",
      sub: <span>{fmtInt(m.total_success)} 次成功</span> },
    { label: "限流 429", val: fmtPct(m.rate_429), icon: "bolt",
      iBg: "var(--warn-bg)", iFg: "var(--warn)", spark: m.spark_429, sc: "var(--warn)", st: "bar",
      sub: <span>{fmtInt(m.total_429)} 次</span> },
    { label: "封控", val: fmtInt(m.total_blocked), icon: "shield",
      iBg: "var(--blocked-bg)", iFg: "var(--blocked)", spark: null, sc: "var(--blocked)", st: "bar",
      sub: <span>封控率 {fmtPct(m.blocked_rate)}</span> },
    { label: "入库商品", val: fmtInt(m.total_products), icon: "box",
      iBg: "#eef0fb", iFg: "#5b63d6", spark: m.spark_products, sc: "#5b63d6", st: "line",
      sub: null },
    { label: "累计 IP", val: fmtInt(m.total_ip_used), icon: "globe",
      iBg: "#f0ecfb", iFg: "#8257cf", spark: m.spark_ip, sc: "#8257cf", st: "bar",
      sub: <span>每 IP {m.avg_yield_per_ip} 件</span> },
  ];
  return (
    <div className="kpis">
      {cards.map((c) => (
        <div className="kpi" key={c.label}>
          <div className="kpi-top">
            <span className="kpi-ico" style={{ background: c.iBg, color: c.iFg }}>
              <Icon name={c.icon} size={14} fill={c.icon === "checkCircle"} />
            </span>
            <span className="kpi-label">{c.label}</span>
          </div>
          <div className="kpi-val tnum" style={{ color: c.iFg }}>{c.val}</div>
          {c.sub && <div className="kpi-sub tnum">{c.sub}</div>}
          {c.spark && (
            <div className="kpi-spark">
              <Sparkline data={c.spark} color={c.sc} type={c.st} w={56} h={20} />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
window.MetricsStrip = MetricsStrip;
