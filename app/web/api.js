/* ============================================================
   api.js — 真实后端 API 层（替换原 mockdata.js）
   暴露 window.API；统一带 X-API-Key；含字段映射（DB行 → 前端展示字段）
   ============================================================ */
(function () {
  let _key = "dev-key-change-me";
  try { _key = localStorage.getItem("wm_api_key") || _key; } catch (e) {}

  function getKey() { return _key; }
  function setKey(k) { _key = k || ""; try { localStorage.setItem("wm_api_key", _key); } catch (e) {} }

  async function req(path, opts = {}) {
    const headers = Object.assign({ "X-API-Key": _key }, opts.headers || {});
    if (opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
      opts = { ...opts, body: JSON.stringify(opts.json), method: opts.method || "POST" };
      delete opts.json;
    }
    const r = await fetch(path, { ...opts, headers });
    const ct = r.headers.get("content-type") || "";
    let data = null;
    if (ct.includes("application/json")) data = await r.json().catch(() => null);
    if (!r.ok) {
      const err = new Error((data && data.detail) || ("HTTP " + r.status));
      err.status = r.status; err.data = data;
      throw err;
    }
    return data;
  }

  /* ---------- 字段映射 ---------- */
  const money = (v) => (v == null || v === "") ? "—" : ("$" + Number(v).toFixed(2));
  const intc = (v) => (v == null || v === "") ? "—" : Number(v).toLocaleString("en-US");

  function mapTask(t) {
    const p = t.params || {};
    let query = t.query;
    if (!query) {
      if (t.type === "keyword") query = p.keyword || "关键词";
      else if (t.type === "seller") query = "Seller " + (p.seller_id || "");
      else query = "ID 批次 · " + ((p.ids && p.ids.length) || t.total || 0) + " 个";
    }
    return { ...t, query, backend_gtin: !!p.backend_gtin, updated_at: t.updated_at || t.created_at };
  }

  function mapProduct(r) {
    let auth = false;
    try { auth = JSON.parse(r.gtin_meta || "{}").source === "seller_backend"; } catch (e) {}
    return {
      ...r,
      price: money(r.price),
      was_price: r.was_price == null ? "—" : money(r.was_price),
      rating: r.rating == null ? "—" : r.rating,
      reviews: intc(r.reviews),
      gtin13: r.gtin13 || "—",
      upc: r.upc || "—",
      seller_type: r.seller_type || "—",
      fulfillment_channel: r.fulfillment_channel || "—",
      seller_count: r.seller_count == null ? "—" : String(r.seller_count),
      _img: r.image_url || null,
      _gtinAuth: auth,
    };
  }

  function mapListing(r) {
    return {
      ...r,
      price: money(r.price),
      rating: r.rating == null ? "—" : r.rating,
      reviews: intc(r.reviews),
      fulfillment_type: r.fulfillment_type || "—",
      _img: r.image_url || null,
    };
  }

  /* ---------- 分页拉全（keyset，封顶防失控）---------- */
  async function loadAll(kind, taskId, cap = 2000) {
    const out = [];
    let after = 0;
    const map = kind === "products" ? mapProduct : mapListing;
    while (out.length < cap) {
      const d = await req(`/${kind}?task_id=${taskId}&after_id=${after}&limit=200`);
      const items = d.items || [];
      out.push(...items.map(map));
      if (items.length < 200) break;
      after = d.next_cursor;
    }
    return out;
  }

  /* ---------- 导出（带鉴权头 → blob 下载）---------- */
  async function download(kind, fmt, taskId) {
    const r = await fetch(`/export/${kind}?fmt=${fmt}&task_id=${taskId}`, {
      headers: { "X-API-Key": _key },
    });
    if (!r.ok) throw new Error("导出失败 HTTP " + r.status);
    const blob = await r.blob();
    const cd = r.headers.get("content-disposition") || "";
    const m = cd.match(/filename="?([^"]+)"?/);
    const name = m ? m[1] : `${kind}.${fmt}`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = name; document.body.appendChild(a); a.click();
    a.remove(); URL.revokeObjectURL(url);
  }

  window.API = {
    getKey, setKey, req,
    tasks: () => req("/tasks?limit=100").then((d) => (d.items || []).map(mapTask)),
    metrics: () => req("/metrics"),
    lanes: () => req("/proxy/status").then((d) => d.lanes || []),
    session: (check) => req("/seller-session/status" + (check ? "?check=1" : "")).catch(() => ({ exists: false })),
    submitIds: (ids, backend_gtin) => req("/collect/ids", { json: { ids, backend_gtin } }),
    submitKeyword: (b) => req("/collect/keyword", { json: b }),
    submitSeller: (b) => req("/collect/seller", { json: b }),
    importFile: (file, form) => {
      const fd = new FormData();
      fd.append("file", file);
      Object.entries(form).forEach(([k, v]) =>
        fd.append(k, typeof v === "boolean" ? (v ? "true" : "false") : String(v)));
      return req("/collect/import", { method: "POST", body: fd });
    },
    deleteTasks: (ids) => req("/tasks/delete", { json: { task_ids: ids } }),
    rotate: (lane_id) => req("/proxy/rotate", { json: { lane_id } }),
    loadAll, download,
  };
})();
