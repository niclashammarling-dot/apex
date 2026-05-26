import React, { useState, useEffect, useCallback } from "react";
import { COMPANY_NAMES } from "../companyNames.js";
import HoverTooltip, { TipRow, TipHeader } from "./HoverTooltip";

const SIGNAL_META = {
  recovering: { label: "RECOVERING", color: "#faff69" },
  breakout:   { label: "BREAKOUT",   color: "#faff69" },
  trending:   { label: "TRENDING",   color: "#6aa0f0" },
  rising:     { label: "RISING",     color: "#7ab8ff" },
  extended:   { label: "EXTENDED",   color: "#e8a020" },
  breakdown:  { label: "BREAKDOWN",  color: "#ff7575" },
  weak:       { label: "WEAK",       color: "#76766f" },
};

const cls = (...a) => a.filter(Boolean).join(" ");

function weeks(days) {
  if (!days) return null;
  if (days < 7) return `${days}d`;
  return `${Math.round(days / 5)}w`;
}

function WatchlistRow({ item, onRemove }) {
  const [tip, setTip] = useState(null);
  const sm    = SIGNAL_META[item.signal] ?? SIGNAL_META.weak;
  const score = item.score;
  const scoreClass = score == null ? "" : score >= 0.55 ? "t-pos" : score >= 0.40 ? "" : "t-neg";
  const streak = weeks(item.streak_days);

  return (
    <div
      onMouseMove={e => setTip({ x: e.clientX, y: e.clientY })}
      onMouseLeave={() => setTip(null)}
      style={{
        display: "flex", alignItems: "center", gap: 10,
        borderTop: "1px solid var(--t-border)",
        padding: "8px 14px",
        background: item.pre_rotation ? "rgba(250,255,105,0.03)" : "transparent",
      }}
    >
      <HoverTooltip pos={tip}>
        <TipHeader>{item.ticker}{COMPANY_NAMES[item.ticker] ? ` — ${COMPANY_NAMES[item.ticker]}` : ""}</TipHeader>
        <TipRow label="Signal"       value={`${sm.label}${streak ? ` · ${streak}` : ""}`} color={sm.color} />
        <TipRow label="Sector"       value={item.sector ?? "—"} />
        <TipRow label="Score"        value={score?.toFixed(3) ?? "—"} />
        <TipRow label="Pre-rotation" value={item.pre_rotation ? "YES" : "NO"} color={item.pre_rotation ? "#faff69" : "#76766f"} />
        <TipRow label="Source"       value={item.source ?? "—"} />
      </HoverTooltip>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontFamily: "var(--mono)", fontSize: 13, color: "var(--t-text-1)", fontWeight: 700 }}>
            {item.ticker}
          </span>
          <span className="t-pill" style={{ color: sm.color, background: `${sm.color}18`, borderColor: `${sm.color}44`, fontSize: 9 }}>
            {sm.label}{streak ? <span style={{ fontWeight: 400, opacity: 0.7 }}> · {streak}</span> : null}
          </span>
          {item.pre_rotation && (
            <span className="t-pill t-pill-paper" style={{ fontSize: 9 }}>PRE-ROT</span>
          )}
        </div>
        <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--t-text-3)", marginTop: 2 }}>
          {item.sector}
          {item.source === "manual" && <span style={{ marginLeft: 8, opacity: 0.6 }}>manual</span>}
        </div>
      </div>

      <span className={cls("t-num-sm", scoreClass)} style={{ fontFamily: "var(--mono)", fontSize: 13, fontWeight: 700, minWidth: 40, textAlign: "right" }}>
        {score?.toFixed(3) ?? "—"}
      </span>

      <button
        onClick={() => onRemove(item.ticker)}
        className="t-btn"
        style={{ padding: "2px 6px", fontSize: 12, minWidth: "unset" }}
        title="Remove"
      >×</button>
    </div>
  );
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
    <div className="t-card">
      <div className="t-card-head" style={{ cursor: "pointer", userSelect: "none" }} onClick={() => setOpen(v => !v)}>
        <div className="t-card-title">
          WATCHLIST
          {!open && items.length > 0 && <span className="t-meta" style={{ marginLeft: 8 }}>{items.length}</span>}
        </div>
        <div className="t-row" style={{ gap: 10 }}>
          {open && <span className="t-meta">PRE-BREAKOUT CANDIDATES</span>}
          <span className="t-meta">{open ? "▲" : "▼"}</span>
        </div>
      </div>

      {open && (
        <>
          <div style={{ padding: "8px 14px", borderBottom: "1px solid var(--t-border)" }} onClick={e => e.stopPropagation()}>
            <form onSubmit={handleAdd} style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <input
                value={input}
                onChange={e => { setInput(e.target.value.toUpperCase()); setAddErr(null); }}
                placeholder="TICKER"
                maxLength={5}
                style={{
                  fontFamily: "var(--mono)", fontSize: 12,
                  background: "var(--t-bg)", border: "1px solid var(--t-border)",
                  borderRadius: 4, padding: "5px 10px", color: "var(--t-text-1)",
                  width: 80, textTransform: "uppercase", outline: "none",
                }}
              />
              <button type="submit" disabled={adding || !input.trim()} className="t-btn"
                style={{ opacity: adding || !input.trim() ? 0.4 : 1 }}>
                {adding ? "…" : "+ WATCH"}
              </button>
              {addErr && (
                <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--t-red)" }}>{addErr}</span>
              )}
            </form>
          </div>

          {error && (
            <div style={{ padding: "10px 14px", fontFamily: "var(--mono)", fontSize: 11, color: "var(--t-red)" }}>
              {error}
            </div>
          )}

          {!error && items.length === 0 && (
            <div className="t-card-body" style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--t-text-3)" }}>
              No tickers — RECOVERING tickers are added automatically after each poll.
            </div>
          )}

          {sorted.map(item => (
            <WatchlistRow key={item.ticker} item={item} onRemove={handleRemove} />
          ))}
        </>
      )}
    </div>
  );
}
