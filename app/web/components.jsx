/* ============================================================
   components.jsx — 共享原子组件
   ============================================================ */
const { useState, useEffect, useRef, useCallback } = React;

/* ---------- Icon set (line icons) ---------- */
const ICONS = {
  cart: "M7 18a1.6 1.6 0 100 3.2A1.6 1.6 0 007 18zm10 0a1.6 1.6 0 100 3.2A1.6 1.6 0 0017 18zM3 3h2l2.2 11.2a1.5 1.5 0 001.5 1.2h7.7a1.5 1.5 0 001.5-1.2L20 7H6",
  upload: "M12 16V4m0 0L7 9m5-5l5 5M5 20h14",
  search: "M11 4a7 7 0 105 12 7 7 0 00-5-12zm6 13l4 4",
  store: "M4 9l1-5h14l1 5M4 9v10a1 1 0 001 1h14a1 1 0 001-1V9M4 9h16M9 20v-6h6v6",
  tag: "M3 3v8l9 9 8-8-9-9H3zm4 4h.01",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  refresh: "M3 12a9 9 0 0115-6.7L21 8M21 3v5h-5M21 12a9 9 0 01-15 6.7L3 16m0 5v-5h5",
  globe: "M12 3a9 9 0 100 18 9 9 0 000-18zm0 0c2.5 2.5 3.5 6 3.5 9s-1 6.5-3.5 9c-2.5-2.5-3.5-6-3.5-9s1-6.5 3.5-9zM3 12h18",
  check: "M5 12l5 5L20 6",
  checkCircle: "M12 3a9 9 0 100 18 9 9 0 000-18zm-1.5 12.5L7 12l1.4-1.4 2.1 2.1L15.6 8 17 9.4l-6.5 6.1z",
  x: "M6 6l12 12M18 6L6 18",
  warn: "M12 3l9 16H3L12 3zm0 6v5m0 3h.01",
  alert: "M12 3a9 9 0 100 18 9 9 0 000-18zm0 5v5m0 3h.01",
  bolt: "M13 2L4 14h6l-1 8 9-12h-6l1-8z",
  box: "M3 7l9-4 9 4v10l-9 4-9-4V7zm9-4v18M3 7l9 4 9-4",
  chart: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7zm10 3a3 3 0 100-6 3 3 0 000 6z",
  download: "M12 4v10m0 0l-4-4m4 4l4-4M5 19h14",
  trash: "M4 7h16M9 7V5a1 1 0 011-1h4a1 1 0 011 1v2m-8 0v12a1 1 0 001 1h6a1 1 0 001-1V7",
  file: "M14 3H7a1 1 0 00-1 1v16a1 1 0 001 1h10a1 1 0 001-1V7l-4-4zm0 0v4h4",
  key: "M15 7a4 4 0 11-3.8 5.3L7 16.5l-2 .5.5-2 .5-2 1.7-1.7A4 4 0 0115 7zm1.5 2.5h.01",
  ip: "M12 12a3 3 0 100-6 3 3 0 000 6zm6.4 6.4a9 9 0 10-12.8 0M12 12v9",
  clock: "M12 3a9 9 0 100 18 9 9 0 000-18zm0 4v5l3.5 2",
  filter: "M3 5h18l-7 8v6l-4 2v-8L3 5z",
  plus: "M12 5v14M5 12h14",
  spark: "M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4L12 2z",
  hash: "M4 9h16M4 15h16M10 3L8 21M16 3l-2 18",
  shield: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z",
  trend: "M3 17l6-6 4 4 7-8",
  layers: "M12 3l9 5-9 5-9-5 9-5zm9 9l-9 5-9-5",
  dollar: "M12 3v18M16 7a4 4 0 00-4-2c-2.2 0-4 1.3-4 3s1.8 3 4 3 4 1.3 4 3-1.8 3-4 3a4 4 0 01-4-2",
};

function Icon({ name, size = 16, sw = 2, fill = false, style, className }) {
  const d = ICONS[name] || "";
  return (
    <svg width={size} height={size} viewBox="0 0 24 24"
      fill={fill ? "currentColor" : "none"}
      stroke={fill ? "none" : "currentColor"}
      strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round"
      style={style} className={className} aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

/* ---------- Status badge ---------- */
const STATUS_LABEL = { pending: "等待中", running: "运行中", done: "已完成", failed: "失败", blocked: "封控" };
const STATE_LABEL = { active: "正常", blocked: "封控", idle: "空闲", running: "工作中" };

function Badge({ status, children, pulse }) {
  const cls = "bdg bdg-" + status;
  return (
    <span className={cls}>
      <span className="d" style={{ background: "currentColor", opacity: .85,
        animation: pulse ? "fl 1.4s ease infinite" : "none" }} />
      {children || STATUS_LABEL[status] || status}
    </span>
  );
}

/* ---------- Sparkline ---------- */
function Sparkline({ data, w = 60, h = 22, color = "var(--wm)", fillO = 0.12, type = "line" }) {
  if (!data || !data.length) return null;
  const min = Math.min(...data), max = Math.max(...data);
  const rng = max - min || 1;
  const step = w / (data.length - 1);
  const pts = data.map((v, i) => [i * step, h - 2 - ((v - min) / rng) * (h - 4)]);
  if (type === "bar") {
    const bw = (w / data.length) * 0.62;
    return (
      <svg width={w} height={h} style={{ display: "block" }}>
        {data.map((v, i) => {
          const bh = ((v - min) / rng) * (h - 4) + 2;
          return <rect key={i} x={i * (w / data.length) + (w / data.length - bw) / 2}
            y={h - bh} width={bw} height={bh} rx={1.2} fill={color} opacity={0.55 + 0.45 * (i / data.length)} />;
        })}
      </svg>
    );
  }
  const line = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const area = line + ` L${w} ${h} L0 ${h} Z`;
  const id = "sg" + Math.round(min * 1000 + max + data.length);
  return (
    <svg width={w} height={h} style={{ display: "block", overflow: "visible" }}>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={fillO * 2} />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${id})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} r="2" fill={color} />
    </svg>
  );
}

/* ---------- Toggle ---------- */
function Toggle({ on, onChange, label, sub }) {
  return (
    <div className={"tgl" + (on ? " on" : "")} onClick={() => onChange(!on)} role="switch" aria-checked={on}>
      <span className="sw" />
      {label && <span className="lab">{label}{sub && <small>{sub}</small>}</span>}
    </div>
  );
}

/* ---------- Segmented control ---------- */
function Segmented({ value, onChange, options }) {
  return (
    <div className="seg">
      {options.map((o) => (
        <button key={o.value} className={value === o.value ? "on" : ""} onClick={() => onChange(o.value)}>
          {o.icon && <Icon name={o.icon} size={14} />}{o.label}
        </button>
      ))}
    </div>
  );
}

/* ---------- Checkbox ---------- */
function Check({ on, onChange }) {
  return (
    <div className={"chk" + (on ? " on" : "")} onClick={(e) => { e.stopPropagation(); onChange(!on); }}>
      {on && <Icon name="check" size={12} sw={3} style={{ color: "#fff" }} />}
    </div>
  );
}

/* ---------- Toasts ---------- */
function ToastHost({ toasts }) {
  const tone = { ok: "checkCircle", info: "bolt", warn: "warn" };
  return (
    <div className="toasts">
      {toasts.map((t) => (
        <div key={t.id} className={"toast " + (t.kind || "info")}>
          <span className="ti"><Icon name={tone[t.kind] || "bolt"} size={14} fill={t.kind === "ok"} /></span>
          {t.msg}
        </div>
      ))}
    </div>
  );
}

/* ---------- helpers ---------- */
function fmtInt(n) { return (n || 0).toLocaleString("en-US"); }
function fmtPct(r) { return r == null ? "—" : (r * 100).toFixed(1) + "%"; }
function fmtAge(sec) {
  if (sec == null) return "—";
  if (sec < 60) return Math.floor(sec) + "s";
  if (sec < 3600) return Math.floor(sec / 60) + "m " + Math.floor(sec % 60) + "s";
  return Math.floor(sec / 3600) + "h " + Math.floor((sec % 3600) / 60) + "m";
}
function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso); const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function relTime(iso) {
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 5) return "刚刚";
  if (s < 60) return s + " 秒前";
  if (s < 3600) return Math.floor(s / 60) + " 分钟前";
  return Math.floor(s / 3600) + " 小时前";
}
const TYPE_LABEL = { detail: "ID采集", keyword: "关键词", seller: "店铺" };
const TYPE_ICON = { detail: "hash", keyword: "search", seller: "store" };

Object.assign(window, {
  Icon, ICONS, Badge, Sparkline, Toggle, Segmented, Check, ToastHost,
  STATUS_LABEL, STATE_LABEL, TYPE_LABEL, TYPE_ICON,
  fmtInt, fmtPct, fmtAge, fmtTime, relTime,
});
