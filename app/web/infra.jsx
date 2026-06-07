/* ============================================================
   infra.jsx — 代理 Lane 监控 + 卖家后台会话状态
   ============================================================ */
function InfraRail({ lanes, onRotate, rotating, session }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* 代理 Lane */}
      <div className="card">
        <div className="card-h">
          <span className="ic"><Icon name="globe" size={16} /></span>
          <h2>代理 IP / Lane</h2>
          <span className="cnt tnum">{lanes.length}</span>
        </div>
        <div className="card-b" style={{ paddingTop: 12, paddingBottom: 12 }}>
          {lanes.map((l) => {
            const blocked = l.state === "blocked";
            return (
              <div key={l.lane_id} className={"lane" + (blocked ? " blocked" : "")}>
                <div className="lane-top">
                  <span className="lane-name">
                    <span style={{ width: 8, height: 8, borderRadius: "50%",
                      background: blocked ? "var(--blocked)" : "var(--ok)",
                      boxShadow: `0 0 0 3px ${blocked ? "var(--blocked-bg)" : "var(--ok-bg)"}`,
                      animation: blocked ? "fl 1.3s ease infinite" : "none" }} />
                    Lane {l.lane_id}
                  </span>
                  <Badge status={blocked ? "blocked" : "done"}>{STATE_LABEL[l.state]}</Badge>
                </div>
                <div className="lane-ip">
                  <Icon name="ip" size={12} style={{ color: "var(--wm)" }} />
                  {l.current_ip}
                </div>
                <div className="lane-stats">
                  <div className="lane-stat"><div className="k">IP 寿命</div><div className="v tnum">{fmtAge(l.ip_age_sec)}</div></div>
                  <div className="lane-stat"><div className="k">请求次数</div><div className="v tnum">{fmtInt(l.ip_uses)}</div></div>
                  <div className="lane-stat"><div className="k">产出商品</div><div className="v tnum">{fmtInt(l.total_products)}</div></div>
                  <div className="lane-stat"><div className="k">封控原因</div><div className="v" style={{ fontSize: 10.5, color: l.last_block ? "var(--blocked)" : "var(--ink-3)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={l.last_block || "无"}>{l.last_block || "无"}</div></div>
                </div>
                {blocked && (
                  <div className="note amber" style={{ marginBottom: 9, padding: "7px 9px" }}>
                    <span className="ic"><Icon name="warn" size={12} /></span>
                    该 Lane 已被封控，建议立即切换 IP
                  </div>
                )}
                <button className={"btn btn-sm btn-block " + (blocked ? "btn-pri" : "btn-sec")}
                  disabled={rotating === l.lane_id} onClick={() => onRotate(l.lane_id)}>
                  <Icon name="refresh" size={13} className={rotating === l.lane_id ? "spin" : ""} />
                  {rotating === l.lane_id ? "切换中…" : "切换此 Lane 的 IP"}
                </button>
              </div>
            );
          })}
        </div>
      </div>

      {/* 卖家后台会话 */}
      <div className="card">
        <div className="card-h">
          <span className="ic"><Icon name="key" size={15} /></span>
          <h2>卖家后台会话</h2>
        </div>
        <div className="card-b">
          <div className="sess">
            <svg className="sess-ring" viewBox="0 0 44 44">
              <circle cx="22" cy="22" r="18" fill="none" stroke="var(--surface-3)" strokeWidth="5" />
              <circle cx="22" cy="22" r="18" fill="none" stroke={session.alive ? "var(--ok)" : "var(--err)"}
                strokeWidth="5" strokeLinecap="round" strokeDasharray="113"
                strokeDashoffset={113 * (session.age_min / 120)} transform="rotate(-90 22 22)" />
              <text x="22" y="20" textAnchor="middle" fontSize="11" fontWeight="800" fill="var(--ink)" fontFamily="var(--mono)">{session.age_min}</text>
              <text x="22" y="29" textAnchor="middle" fontSize="6.5" fontWeight="700" fill="var(--ink-3)">分钟</text>
            </svg>
            <div className="sess-meta">
              <b style={{ color: session.alive ? "var(--ok)" : "var(--err)" }}>
                {session.alive ? "会话有效" : "会话失效"}
              </b>
              <div>导出于 {session.age_min} 分钟前 · 已验活</div>
              <div className="mono" style={{ fontSize: 10, marginTop: 3 }}>{session.account}</div>
            </div>
          </div>
          <div className="lane-stats" style={{ marginTop: 12, marginBottom: 0 }}>
            <div className="lane-stat" style={{ background: "var(--surface-2)" }}>
              <div className="k">专属代理</div>
              <div className="v" style={{ fontSize: 11.5, color: session.has_proxy ? "var(--ok)" : "var(--err)", display: "flex", alignItems: "center", gap: 4 }}>
                <Icon name={session.has_proxy ? "check" : "x"} size={12} sw={3} />{session.has_proxy ? "已绑定" : "缺失"}
              </div>
            </div>
            <div className="lane-stat" style={{ background: "var(--surface-2)" }}>
              <div className="k">isbm 验活</div>
              <div className="v" style={{ fontSize: 11.5, color: "var(--ok)", display: "flex", alignItems: "center", gap: 4 }}>
                <Icon name="check" size={12} sw={3} />alive
              </div>
            </div>
          </div>
          <div className="note" style={{ marginTop: 11, padding: "7px 9px" }}>
            <span className="ic"><Icon name="bolt" size={12} /></span>
            由本地 upload_session.py 定时上报；勾选「后台 GTIN」的任务完成后自动回填权威码
          </div>
        </div>
      </div>
    </div>
  );
}
window.InfraRail = InfraRail;
