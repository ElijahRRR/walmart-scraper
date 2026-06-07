/* ============================================================
   submit.jsx — 新建采集任务（核心面板）
   三类：按商品 ID / 关键词 / 卖家店铺；含文件导入 + 后台 GTIN
   （已对接真实后端：doSubmit/onFile 传出真实参数）
   ============================================================ */
function SubmitPanel({ onSubmit, onImport }) {
  const [tab, setTab] = useState("ids");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef(null);

  // ids
  const [idsText, setIdsText] = useState("");
  // keyword
  const [kw, setKw] = useState("");
  const [kwPages, setKwPages] = useState(25);
  const [kwMin, setKwMin] = useState("");
  const [kwMax, setKwMax] = useState("");
  const [kwDetail, setKwDetail] = useState(true);
  // seller
  const [sid, setSid] = useState("");
  const [selPages, setSelPages] = useState(30);
  const [selDetail, setSelDetail] = useState(true);
  // shared
  const [backendGtin, setBackendGtin] = useState(false);

  const ids = idsText.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);
  const idCount = ids.length;

  const canSubmit =
    (tab === "ids" && idCount > 0) ||
    (tab === "keyword" && kw.trim()) ||
    (tab === "seller" && sid.trim());

  async function doSubmit() {
    if (!canSubmit || busy) return;
    setBusy(true);
    let payload;
    if (tab === "ids") {
      payload = { type: "ids", ids, backend_gtin: backendGtin,
        query: `ID 批次 · ${idCount} 个`, total: idCount };
    } else if (tab === "keyword") {
      payload = { type: "keyword", keyword: kw.trim(),
        max_pages: kwPages || 25,
        min_price: kwMin === "" ? null : +kwMin,
        max_price: kwMax === "" ? null : +kwMax,
        with_detail: kwDetail, backend_gtin: backendGtin,
        query: kw.trim(), total: (kwPages || 25) * 16 };
    } else {
      payload = { type: "seller", seller_id: sid.trim(),
        max_pages: selPages || 30, with_detail: selDetail, backend_gtin: backendGtin,
        query: `Seller ${sid.trim()}`, total: (selPages || 30) * 32 };
    }
    try {
      await onSubmit(payload);
      if (tab === "ids") setIdsText("");
      if (tab === "keyword") setKw("");
      if (tab === "seller") setSid("");
    } finally {
      setBusy(false);
    }
  }

  function pickFile() { fileRef.current && fileRef.current.click(); }
  function onFile(e) {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    const form = { backend_gtin: backendGtin };
    if (tab === "keyword") {
      form.with_detail = kwDetail; form.max_pages = kwPages || 25;
      if (kwMin !== "") form.min_price = +kwMin;
      if (kwMax !== "") form.max_price = +kwMax;
    } else if (tab === "seller") {
      form.with_detail = selDetail; form.max_pages = selPages || 30;
    }
    onImport(f, tab, form);
    e.target.value = "";
  }

  return (
    <div className="card">
      <div className="card-h">
        <span className="ic"><Icon name="plus" size={17} sw={2.4} /></span>
        <h2>新建采集任务</h2>
      </div>
      <div className="card-b">
        <div style={{ marginBottom: 14 }}>
          <Segmented value={tab} onChange={setTab} options={[
            { value: "ids", label: "商品 ID", icon: "hash" },
            { value: "keyword", label: "关键词", icon: "search" },
            { value: "seller", label: "卖家店铺", icon: "store" },
          ]} />
        </div>

        {tab === "ids" && (
          <div className="fadein">
            <div className="note" style={{ marginBottom: 12 }}>
              <span className="ic"><Icon name="bolt" size={13} /></span>
              按 ID 采集即采详情，无需二段式。每行一个商品 ID，或粘贴逗号分隔。
            </div>
            <div className="fld">
              <label>商品 ID 列表 {idCount > 0 && <span style={{ color: "var(--wm)", fontWeight: 800 }}>· {idCount} 个</span>}</label>
              <textarea className="ta" value={idsText} onChange={(e) => setIdsText(e.target.value)}
                placeholder={"5500001234\n5500005678\n5500009012"} />
            </div>
          </div>
        )}

        {tab === "keyword" && (
          <div className="fadein">
            <div className="fld">
              <label>搜索关键词</label>
              <input className="inp" value={kw} onChange={(e) => setKw(e.target.value)} placeholder="例如：wireless earbuds" />
            </div>
            <div className="row3">
              <div className="fld">
                <label>最大页数</label>
                <input className="inp tnum" type="number" min="1" max="25" value={kwPages}
                  onChange={(e) => setKwPages(+e.target.value)} />
              </div>
              <div className="fld">
                <label>最低价 $</label>
                <input className="inp tnum" type="number" value={kwMin} onChange={(e) => setKwMin(e.target.value)} placeholder="不限" />
              </div>
              <div className="fld">
                <label>最高价 $</label>
                <input className="inp tnum" type="number" value={kwMax} onChange={(e) => setKwMax(e.target.value)} placeholder="不限" />
              </div>
            </div>
            <div className="fld" style={{ marginTop: 2 }}>
              <Toggle on={kwDetail} onChange={setKwDetail} label="采集详情" sub="二段式，获取完整商品信息" />
            </div>
          </div>
        )}

        {tab === "seller" && (
          <div className="fadein">
            <div className="fld">
              <label>卖家 ID</label>
              <input className="inp mono" value={sid} onChange={(e) => setSid(e.target.value)} placeholder="例如：F92R2K2JQF4A4" />
            </div>
            <div className="fld">
              <label>最大页数</label>
              <input className="inp tnum" type="number" min="1" value={selPages}
                onChange={(e) => setSelPages(+e.target.value)} style={{ maxWidth: 130 }} />
              <div className="hint">卖家全店无 25 页硬限，默认覆盖大店铺</div>
            </div>
            <div className="fld" style={{ marginTop: 2 }}>
              <Toggle on={selDetail} onChange={setSelDetail} label="采集详情" sub="二段式，获取完整商品信息" />
            </div>
          </div>
        )}

        {/* backend GTIN */}
        <div style={{ borderTop: "1px solid var(--border)", margin: "13px 0", paddingTop: 13 }}>
          <Toggle on={backendGtin} onChange={setBackendGtin}
            label="查 UPC/GTIN（权威更准，较慢）"
            sub="建议产品审核完需要上架时才开启 · 走 isbm 回填目录权威码" />
        </div>

        {/* actions */}
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn btn-pri" style={{ flex: 1 }} disabled={!canSubmit || busy} onClick={doSubmit}>
            {busy ? <Icon name="refresh" size={15} className="spin" /> : <Icon name="bolt" size={15} fill />}
            {busy ? "提交中…" : "开始采集"}
          </button>
          <button className="btn btn-sec" onClick={pickFile} title="从 txt / csv / xlsx 导入">
            <Icon name="upload" size={15} />导入
          </button>
          <input ref={fileRef} type="file" accept=".txt,.csv,.xlsx" hidden onChange={onFile} />
        </div>
        <div className="hint" style={{ marginTop: 8, textAlign: "center" }}>
          支持 .txt / .csv / .xlsx 批量导入
        </div>
      </div>
    </div>
  );
}
window.SubmitPanel = SubmitPanel;
