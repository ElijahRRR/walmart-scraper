/* ============================================================
   results.jsx — 采集结果抽屉（products / listings）
   已对接真实后端：keyset 拉全 + 真实 CSV/Excel 下载
   ============================================================ */
const PRODUCT_COLS = [
  { k: "product_id", h: "产品ID", mono: true },
  { k: "_img", h: "主图" },
  { k: "title", h: "标题" },
  { k: "brand", h: "品牌" },
  { k: "price", h: "价格", price: true },
  { k: "was_price", h: "原价", mono: true },
  { k: "rating", h: "评分", mono: true },
  { k: "reviews", h: "评价数", mono: true },
  { k: "gtin13", h: "GTIN13", gtin: true },
  { k: "upc", h: "UPC", mono: true },
  { k: "seller_name", h: "卖家名称" },
  { k: "seller_type", h: "卖家类型" },
  { k: "fulfillment_channel", h: "履约" },
  { k: "seller_count", h: "卖家数", mono: true },
  { k: "snapshot_at", h: "采集时间", time: true },
];
const LISTING_COLS = [
  { k: "product_id", h: "产品ID", mono: true },
  { k: "title", h: "标题" },
  { k: "brand", h: "品牌" },
  { k: "price", h: "价格", price: true },
  { k: "rating", h: "评分", mono: true },
  { k: "reviews", h: "评价数", mono: true },
  { k: "seller_name", h: "卖家名称" },
  { k: "seller_id", h: "卖家ID", mono: true },
  { k: "fulfillment_type", h: "履约类型" },
  { k: "snapshot_at", h: "采集时间", time: true },
];

const SWATCH = ["#dce6f3", "#e4dcf3", "#f3e6dc", "#dcf3e8", "#f3dce0", "#e8f0d8"];

function ResultDrawer({ task, onClose, onToast }) {
  const [tab, setTab] = useState("products");
  const [shown, setShown] = useState(0);
  const [products, setProducts] = useState([]);
  const [listings, setListings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [pPage, setPPage] = useState(50);
  const [lPage, setLPage] = useState(50);
  const [exp, setExp] = useState(null); // 'csv' | 'xlsx'

  useEffect(() => {
    setShown(1);
    if (!task) return;
    let cancelled = false;
    setLoading(true); setProducts([]); setListings([]); setPPage(50); setLPage(50); setTab("products");
    (async () => {
      try {
        const ps = await window.API.loadAll("products", task.id);
        if (!cancelled) setProducts(ps);
      } catch (e) { if (!cancelled) onToast && onToast("warn", "商品数据加载失败：" + e.message); }
      try {
        const ls = await window.API.loadAll("listings", task.id);
        if (!cancelled) setListings(ls);
      } catch (e) { /* listings 可能为空（ID采集任务） */ }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [task]);

  if (!task) return null;
  const cols = tab === "products" ? PRODUCT_COLS : LISTING_COLS;
  const all = tab === "products" ? products : listings;
  const page = tab === "products" ? pPage : lPage;
  const rows = all.slice(0, page);
  const hasMore = page < all.length;

  function loadMore() {
    if (tab === "products") setPPage((p) => p + 50); else setLPage((p) => p + 50);
  }
  async function doExport(fmt) {
    setExp(fmt);
    try {
      await window.API.download(tab, fmt, task.id);
      onToast && onToast("ok", `${tab === "products" ? "商品" : "Listing"}数据已导出 ${fmt.toUpperCase()}（${fmtInt(all.length)} 条）`);
    } catch (e) {
      onToast && onToast("warn", "导出失败：" + e.message);
    } finally { setExp(null); }
  }

  return (
    <React.Fragment>
      <div className={"scrim" + (shown ? " show" : "")} onClick={onClose} />
      <div className={"drawer" + (shown ? " show" : "")}>
        <div className="dr-h">
          <span style={{ width: 36, height: 36, borderRadius: 10, background: "var(--wm-50)", color: "var(--wm)", display: "grid", placeItems: "center", flex: "none" }}>
            <Icon name="box" size={18} />
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 14, fontWeight: 800, letterSpacing: "-.01em" }}>
              {task.query}
            </div>
            <div style={{ fontSize: 11, color: "var(--ink-3)", fontWeight: 600, display: "flex", gap: 8, marginTop: 1 }}>
              <span className="mono">#{task.id} · {task.code}</span>
              <span>·</span>
              <span>{fmtInt(task.result_count)} 条结果</span>
              {task.backend_gtin && <span style={{ color: "var(--wm)", display: "inline-flex", gap: 3, alignItems: "center" }}><Icon name="shield" size={11} />后台 GTIN 已回填</span>}
            </div>
          </div>
          <button className="btn btn-sec btn-sm" disabled={exp || !all.length} onClick={() => doExport("csv")}>
            <Icon name={exp === "csv" ? "refresh" : "download"} size={13} className={exp === "csv" ? "spin" : ""} />导出 CSV
          </button>
          <button className="btn btn-sec btn-sm" disabled={exp || !all.length} onClick={() => doExport("xlsx")}>
            <Icon name={exp === "xlsx" ? "refresh" : "download"} size={13} className={exp === "xlsx" ? "spin" : ""} />导出 Excel
          </button>
          <button className="x" onClick={onClose}><Icon name="x" size={16} /></button>
        </div>

        <div className="dr-body">
          <div className="dr-toolbar">
            <div style={{ width: 280 }}>
              <Segmented value={tab} onChange={setTab} options={[
                { value: "products", label: `商品数据 (${fmtInt(products.length)})` },
                { value: "listings", label: `Listing (${fmtInt(listings.length)})` },
              ]} />
            </div>
            <div style={{ flex: 1 }} />
            <span style={{ fontSize: 11.5, color: "var(--ink-3)", fontWeight: 600 }}>
              {loading ? <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}><Icon name="refresh" size={12} className="spin" />加载中…</span>
                : <React.Fragment>已加载 <b style={{ color: "var(--ink)" }} className="tnum">{fmtInt(rows.length)}</b> / {fmtInt(all.length)} 条</React.Fragment>}
            </span>
          </div>

          {all.length === 0 ? (
            <div className="empty" style={{ padding: 70 }}>
              <Icon name="box" size={34} style={{ opacity: .35 }} />
              <p>{loading ? "正在加载…" : `该任务下暂无${tab === "products" ? "商品详情" : " Listing "}数据`}</p>
            </div>
          ) : (
            <React.Fragment>
              <div className="dt-wrap">
                <div className="dt-scroll" style={{ maxHeight: "calc(100vh - 280px)" }}>
                  <table className="dt">
                    <thead>
                      <tr>{cols.map((c) => <th key={c.k}>{c.h}</th>)}</tr>
                    </thead>
                    <tbody>
                      {rows.map((r, i) => (
                        <tr key={i}>
                          {cols.map((c) => <Cell key={c.k} c={c} r={r} i={i} />)}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
              <div style={{ display: "flex", justifyContent: "center", marginTop: 14 }}>
                {hasMore ? (
                  <button className="btn btn-sec btn-sm" onClick={loadMore}>
                    <Icon name="plus" size={13} />加载更多（剩余 {fmtInt(all.length - page)} 条）
                  </button>
                ) : (
                  <span style={{ fontSize: 11.5, color: "var(--ok)", fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 5 }}>
                    <Icon name="check" size={13} sw={3} />已全部加载
                  </span>
                )}
              </div>
            </React.Fragment>
          )}
        </div>
      </div>
    </React.Fragment>
  );
}

function Cell({ c, r, i }) {
  let v = r[c.k];
  if (c.k === "_img") {
    if (r._img) return <td><img className="cell-img" src={r._img} alt="" loading="lazy"
      style={{ objectFit: "cover" }} onError={(e) => { e.target.style.display = "none"; }} /></td>;
    return <td><div className="cell-img" style={{ background: SWATCH[i % SWATCH.length] }} /></td>;
  }
  if (c.time) v = fmtTime(v);
  if (c.gtin) {
    const ok = r._gtinAuth;
    return <td className="num"><span className={ok ? "gtin-ok" : ""} title={ok ? "目录权威码" : "公开页值"}>{v}</span></td>;
  }
  if (c.price) return <td><span className="cell-price">{v}</span></td>;
  if (c.k === "title") {
    const href = r.url || (r.product_id ? "https://www.walmart.com/ip/" + r.product_id : null);
    if (!v) return <td>—</td>;
    if (!href) return <td title={v}>{v}</td>;
    return (
      <td title={"在沃尔玛打开：" + v}>
        <a className="cell-link" href={href} target="_blank" rel="noopener noreferrer">
          {v}<Icon name="external" size={11} style={{ marginLeft: 4, opacity: .55, flex: "none" }} />
        </a>
      </td>
    );
  }
  const cls = c.mono ? "num" : "";
  const title = typeof v === "string" ? v : "";
  return <td className={cls} title={title}>{v}</td>;
}

window.ResultDrawer = ResultDrawer;
