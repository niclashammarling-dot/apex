import React, { useState, useEffect, useCallback } from "react";

const SIGNAL_META = {
  recovering: { label: "RECOVERING", color: "#a78bfa" },
  breakout:   { label: "BREAKOUT",   color: "#00d48c" },
  trending:   { label: "TRENDING",   color: "#6aa0f0" },
  rising:     { label: "RISING",     color: "#7ab8ff" },
  extended:   { label: "EXTENDED",   color: "#ffaa00" },
  breakdown:  { label: "BREAKDOWN",  color: "#ff3355" },
  weak:       { label: "WEAK",       color: "#404040" },
};

function weeks(days) {
  if (!days) return null;
  if (days < 7) return `${days}d`;
  return `${Math.round(days / 5)}w`;
}

function scoreColor(score) {
  if (score === null || score === undefined) return "var(--text-3)";
  if (score >= 0.55) return "var(--green)";
  if (score >= 0.40) return "#e8a020";
  return "var(--red)";
}

export default function Watchlist() {
  const [items,   setItems]   = useState([]);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState(null);
  const [input,   setInput]   = useState("");
  const [adding,  setAdding]  = useState(false);
  const [addErr,  setAddErr]  = useState(null);
  const [open,    setOpen]    = useState(false);

  const load = useCallback(() => {
    fetch("/api/watchlist")
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => { setItems(d); setLoading(false); setError(null); })
      .catch(e => { setError(`Failed to load watchlist (${e})`); setLoading(false); });
  }, []);

  useEffect(() => { load(); }, [load]);

  function handleAdd(e) {
    e.preventDefault();
    const ticker = input.trim().toUpperCase();
    if (!ticker) return;
    setAdding(true);
    setAddErr(null);
    fetch(`/api/watchlist/${ticker}`, { method: "POST" })
      .then(r => r.ok ? r.json() : r.json().then(d => Promise.reject(d.detail)))
      .then(() => { setInput(""); load(); })
      .catch(err => setAddErr(typeof err === "string" ? err : "Failed to add"))
      .finally(() => setAdding(false));
  }

  function handleRemove(ticker) {
    fetch(`/api/watchlist/${ticker}`, { method: "DELETE" })
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(() => setItems(prev => prev.filter(i => i.ticker !== ticker)))
      .catch(() => {});
  }

  if (loading) return null;

  const sorted = [...items].sort((a, b) => (b.score ?? -1) - (a.score ?? -1));

  return (
    <div className="card">
      <div
        className="card-header"
        onClick={() => setOpen(v => !v)}
        style={{ cursor: "pointer", userSelect: "none" }}
      >
        <div className="card-title">
          Watchlist {!open && items.length > 0 && (
            <span style={{ fontWeight: 400, color: "var(--text-3)", fontSize: 11 }}>({items.length})</span>
          )}
        </div>
        <div className="card-meta" style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {open && <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 10, color: "var(--text-3)" }}>pre-breakout candidates</span>}
          <span style={{ fontSize: 11, color: "var(--text-3)" }}>{open ? "▲" : "▼"}</span>
        </div>
      </div>

      {open && (
        <>
          {/* Add form */}
          <div style={{ padding: "10px 16px", borderBottom: "1px solid var(--border)" }} onClick={e => e.stopPropagation()}>
            <form onSubmit={handleAdd} style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <input
                value={input}
                onChange={e => { setInput(e.target.value.toUpperCase()); setAddErr(null); }}
                placeholder="TICKER"
                maxLength={5}
                style={{
                  fontFamily: "'Inconsolata', monospace", fontSize: 12,
                  background: "var(--bg)", border: "1px solid var(--border)",
                  borderRadius: 4, padding: "5px 10px", color: "var(--text-1)",
                  width: 90, textTransform: "uppercase", outline: "none",
                }}
              />
              <button type="submit" disabled={adding || !input.trim()} style={{
                fontFamily: "'Inconsolata', monospace", fontSize: 11, fontWeight: 600,
                background: "transparent", border: "1px solid var(--border)",
                borderRadius: 4, padding: "5px 12px", color: "var(--text-2)",
                cursor: adding || !input.trim() ? "default" : "pointer",
                opacity: adding || !input.trim() ? 0.4 : 1,
              }}>
                {adding ? "…" : "+ Watch"}
              </button>
              {addErr && (
                <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, color: "var(--red)" }}>
                  {addErr}
                </span>
              )}
            </form>
          </div>

          {error && (
            <div style={{ padding: "10px 16px", fontFamily: "'Inconsolata', monospace", fontSize: 11, color: "var(--red)" }}>
              {error}
            </div>
          )}

          {!error && items.length === 0 && (
            <div className="card-body">
              <p className="muted">No tickers yet — RECOVERING tickers are added automatically after each poll.</p>
            </div>
          )}

          {sorted.map(item => {
            const sm     = SIGNAL_META[item.signal] ?? SIGNAL_META.weak;
            const color  = scoreColor(item.score);
            const streak = weeks(item.streak_days);
            return (
              <div key={item.ticker} style={{
                display: "flex", alignItems: "center", justifyContent: "space-between",
                padding: "10px 16px", borderTop: "1px solid var(--border)",
                background: item.pre_rotation ? "#faff6905" : "transparent",
              }}>
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 15, color: "var(--text-1)", fontWeight: 700 }}>
                      {item.ticker}
                    </span>
                    <span style={{
                      fontFamily: "'Inconsolata', monospace", fontSize: 9, fontWeight: 700,
                      color: sm.color, background: `${sm.color}18`, border: `1px solid ${sm.color}44`,
                      borderRadius: 3, padding: "1px 5px",
                    }}>
                      {sm.label}{streak ? <span style={{ fontWeight: 400, opacity: 0.7 }}> · {streak}</span> : null}
                    </span>
                    {item.pre_rotation && (
                      <span style={{
                        fontFamily: "'Inconsolata', monospace", fontSize: 9, fontWeight: 700,
                        color: "var(--green)", background: "#faff6910",
                        border: "1px solid #faff6938", borderRadius: 3, padding: "1px 5px",
                      }}>
                        PRE-ROT
                      </span>
                    )}
                  </div>
                  <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, color: "var(--text-3)", marginTop: 3 }}>
                    {item.sector}
                    {item.source === "manual" && <span style={{ marginLeft: 8, color: "var(--text-3)", opacity: 0.6 }}>manual</span>}
                  </div>
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 16, color, fontWeight: 700 }}>
                    {item.score?.toFixed(3) ?? "—"}
                  </span>
                  <button
                    onClick={() => handleRemove(item.ticker)}
                    style={{
                      fontFamily: "'Inconsolata', monospace", fontSize: 14,
                      background: "none", border: "none",
                      color: "var(--text-3)", cursor: "pointer", padding: "0 2px", lineHeight: 1,
                    }}
                    title="Remove"
                  >×</button>
                </div>
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}
