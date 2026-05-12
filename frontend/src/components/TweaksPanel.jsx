import React from "react";

const STORAGE_KEY = "apex:tweaks:v1";

const STYLE = `
  .twk-fab{position:fixed;right:16px;bottom:16px;z-index:2147483645;width:36px;height:36px;
    border-radius:50%;background:rgba(40,38,30,.88);color:#f0f0ec;border:1px solid rgba(255,255,255,.12);
    display:grid;place-items:center;cursor:pointer;font-size:16px;
    box-shadow:0 2px 12px rgba(0,0,0,.4);transition:background .15s}
  .twk-fab:hover{background:rgba(60,58,50,.92)}
  .twk-panel{position:fixed;right:16px;bottom:58px;z-index:2147483646;width:280px;
    max-height:calc(100vh - 90px);display:flex;flex-direction:column;
    background:rgba(250,249,247,.82);color:#29261b;
    -webkit-backdrop-filter:blur(24px) saturate(160%);backdrop-filter:blur(24px) saturate(160%);
    border:.5px solid rgba(255,255,255,.6);border-radius:14px;
    box-shadow:0 1px 0 rgba(255,255,255,.5) inset,0 12px 40px rgba(0,0,0,.22);
    font:11.5px/1.4 ui-sans-serif,system-ui,-apple-system,sans-serif;overflow:hidden}
  .twk-hd{display:flex;align-items:center;justify-content:space-between;
    padding:10px 8px 10px 14px;cursor:move;user-select:none;border-bottom:.5px solid rgba(0,0,0,.06)}
  .twk-hd b{font-size:12px;font-weight:600;letter-spacing:.01em}
  .twk-body{padding:8px 14px 14px;display:flex;flex-direction:column;gap:10px;
    overflow-y:auto;overflow-x:hidden;min-height:0;
    scrollbar-width:thin;scrollbar-color:rgba(0,0,0,.15) transparent}
  .twk-body::-webkit-scrollbar{width:8px}
  .twk-body::-webkit-scrollbar-track{background:transparent;margin:2px}
  .twk-body::-webkit-scrollbar-thumb{background:rgba(0,0,0,.15);border-radius:4px;
    border:2px solid transparent;background-clip:content-box}
  .twk-row{display:flex;flex-direction:column;gap:5px}
  .twk-row-h{flex-direction:row;align-items:center;justify-content:space-between;gap:10px}
  .twk-lbl{display:flex;justify-content:space-between;align-items:baseline;color:rgba(41,38,27,.72)}
  .twk-lbl>span:first-child{font-weight:500}
  .twk-val{color:rgba(41,38,27,.5);font-variant-numeric:tabular-nums}
  .twk-sect{font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;
    color:rgba(41,38,27,.45);padding:8px 0 0}
  .twk-field{appearance:none;width:100%;height:26px;padding:0 8px;
    border:.5px solid rgba(0,0,0,.1);border-radius:7px;
    background:rgba(255,255,255,.6);color:inherit;font:inherit;outline:none}
  .twk-field:focus{border-color:rgba(0,0,0,.25);background:rgba(255,255,255,.85)}
  select.twk-field{padding-right:22px;
    background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'><path fill='rgba(0,0,0,.5)' d='M0 0h10L5 6z'/></svg>");
    background-repeat:no-repeat;background-position:right 8px center}
  .twk-seg{position:relative;display:flex;padding:2px;border-radius:8px;
    background:rgba(0,0,0,.06);user-select:none}
  .twk-seg-thumb{position:absolute;top:2px;bottom:2px;border-radius:6px;
    background:rgba(255,255,255,.9);box-shadow:0 1px 2px rgba(0,0,0,.12);
    transition:left .15s cubic-bezier(.3,.7,.4,1),width .15s}
  .twk-seg button{appearance:none;position:relative;z-index:1;flex:1;border:0;
    background:transparent;color:inherit;font:inherit;font-weight:500;min-height:22px;
    border-radius:6px;cursor:default;padding:4px 6px;line-height:1.2;overflow-wrap:anywhere}
  .twk-chips{display:flex;gap:6px}
  .twk-chip{position:relative;appearance:none;flex:1;min-width:0;height:32px;
    padding:0;border:0;border-radius:6px;overflow:hidden;cursor:default;
    box-shadow:0 0 0 .5px rgba(0,0,0,.12),0 1px 2px rgba(0,0,0,.06);transition:box-shadow .12s}
  .twk-chip[data-on="1"]{box-shadow:0 0 0 1.5px rgba(0,0,0,.85),0 2px 6px rgba(0,0,0,.15)}
  .twk-chip svg{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:13px;height:13px;
    filter:drop-shadow(0 1px 1px rgba(0,0,0,.3))}
  .twk-divider{border:none;border-top:.5px solid rgba(0,0,0,.08);margin:2px 0}
  .twk-action-btn{appearance:none;width:100%;height:26px;padding:0 10px;border:0;border-radius:7px;
    background:rgba(0,0,0,.06);color:inherit;font:inherit;font-weight:500;cursor:pointer;
    text-align:left;transition:background .12s}
  .twk-action-btn:hover{background:rgba(0,0,0,.1)}
`;

export function useTweaks(defaults) {
  const [values, setValues] = React.useState(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) return { ...defaults, ...JSON.parse(stored) };
    } catch (_) {}
    return defaults;
  });

  const setTweak = React.useCallback((keyOrEdits, val) => {
    const edits = typeof keyOrEdits === "object" && keyOrEdits !== null
      ? keyOrEdits : { [keyOrEdits]: val };
    setValues(prev => {
      const next = { ...prev, ...edits };
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)); } catch (_) {}
      return next;
    });
  }, []);

  return [values, setTweak];
}

export function TweaksPanel({ title = "Tweaks", children, onSettings, onPromote, onTest }) {
  const [open, setOpen] = React.useState(() => {
    try { return localStorage.getItem("apex:tweaks:open") !== "0"; } catch (_) { return true; }
  });
  const dragRef = React.useRef(null);
  const offsetRef = React.useRef({ x: 16, y: 58 });

  const persistOpen = (v) => {
    setOpen(v);
    try { localStorage.setItem("apex:tweaks:open", v ? "1" : "0"); } catch (_) {}
  };

  const clampToViewport = React.useCallback(() => {
    const panel = dragRef.current;
    if (!panel) return;
    const PAD = 16;
    const w = panel.offsetWidth, h = panel.offsetHeight;
    offsetRef.current = {
      x: Math.min(window.innerWidth - w - PAD, Math.max(PAD, offsetRef.current.x)),
      y: Math.min(window.innerHeight - h - PAD, Math.max(PAD + 42, offsetRef.current.y)),
    };
    panel.style.right = offsetRef.current.x + "px";
    panel.style.bottom = offsetRef.current.y + "px";
  }, []);

  React.useEffect(() => {
    if (!open) return;
    clampToViewport();
    const ro = typeof ResizeObserver !== "undefined"
      ? new ResizeObserver(clampToViewport) : null;
    if (ro) ro.observe(document.documentElement);
    else window.addEventListener("resize", clampToViewport);
    return () => { if (ro) ro.disconnect(); else window.removeEventListener("resize", clampToViewport); };
  }, [open, clampToViewport]);

  const onDragStart = (e) => {
    const panel = dragRef.current;
    if (!panel) return;
    const r = panel.getBoundingClientRect();
    const sx = e.clientX, sy = e.clientY;
    const startRight = window.innerWidth - r.right;
    const startBottom = window.innerHeight - r.bottom;
    const move = (ev) => {
      offsetRef.current = {
        x: startRight - (ev.clientX - sx),
        y: startBottom - (ev.clientY - sy),
      };
      clampToViewport();
    };
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  return (
    <>
      <style>{STYLE}</style>
      <button className="twk-fab" onClick={() => persistOpen(!open)} title="Tweaks">
        {open ? "✕" : "⚙"}
      </button>
      {open && (
        <div ref={dragRef} className="twk-panel"
             style={{ right: offsetRef.current.x, bottom: offsetRef.current.y }}>
          <div className="twk-hd" onMouseDown={onDragStart}>
            <b>{title}</b>
          </div>
          <div className="twk-body">
            {children}
            {(onSettings || onPromote || onTest) && (
              <>
                <hr className="twk-divider" />
                {onSettings && (
                  <button className="twk-action-btn" onClick={onSettings}>⚙ Settings</button>
                )}
                {onPromote && (
                  <button className="twk-action-btn" onClick={onPromote}>↑ Promote Demo → Live</button>
                )}
                {onTest && (
                  <button className="twk-action-btn" onClick={onTest}>⚗ Test tab</button>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}

export function TweakSection({ title, children }) {
  return (
    <>
      <div className="twk-sect">{title}</div>
      {children}
    </>
  );
}

export function TweakRadio({ label, value, options, onChange }) {
  const trackRef = React.useRef(null);
  const opts = options.map(o => typeof o === "object" ? o : { value: o, label: String(o) });
  const idx = Math.max(0, opts.findIndex(o => o.value === value));
  const n = opts.length;

  const segAt = (clientX) => {
    const r = trackRef.current.getBoundingClientRect();
    const i = Math.floor(((clientX - r.left - 2) / (r.width - 4)) * n);
    return opts[Math.max(0, Math.min(n - 1, i))].value;
  };

  const onPointerDown = (e) => {
    onChange(segAt(e.clientX));
    const move = (ev) => {
      if (!trackRef.current) return;
      const v = segAt(ev.clientX);
      if (v !== value) onChange(v);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  return (
    <div className="twk-row">
      <div className="twk-lbl"><span>{label}</span></div>
      <div ref={trackRef} className="twk-seg" onPointerDown={onPointerDown}>
        <div className="twk-seg-thumb"
             style={{ left: `calc(2px + ${idx} * (100% - 4px) / ${n})`, width: `calc((100% - 4px) / ${n})` }} />
        {opts.map(o => (
          <button key={o.value} type="button">{o.label}</button>
        ))}
      </div>
    </div>
  );
}

export function TweakSelect({ label, value, options, onChange }) {
  return (
    <div className="twk-row">
      <div className="twk-lbl"><span>{label}</span></div>
      <select className="twk-field" value={value} onChange={e => onChange(e.target.value)}>
        {options.map(o => {
          const v = typeof o === "object" ? o.value : o;
          const l = typeof o === "object" ? o.label : o;
          return <option key={v} value={v}>{l}</option>;
        })}
      </select>
    </div>
  );
}

export function TweakColor({ label, value, options, onChange }) {
  const key = o => String(JSON.stringify(o)).toLowerCase();
  const cur = key(value);
  return (
    <div className="twk-row">
      <div className="twk-lbl"><span>{label}</span></div>
      <div className="twk-chips">
        {options.map((o, i) => {
          const swatch = typeof o === "object" ? o.swatch : o;
          const on = key(o) === cur;
          const isLight = (() => {
            const h = String(swatch).replace("#", "").padEnd(6, "0");
            const n = parseInt(h.slice(0, 6), 16);
            if (isNaN(n)) return true;
            const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
            return r * 299 + g * 587 + b * 114 > 148000;
          })();
          return (
            <button key={i} type="button" className="twk-chip"
                    data-on={on ? "1" : "0"} title={typeof o === "object" ? o.value : o}
                    style={{ background: swatch }} onClick={() => onChange(o)}>
              {on && (
                <svg viewBox="0 0 14 14" aria-hidden="true">
                  <path d="M3 7.2 5.8 10 11 4.2" fill="none" strokeWidth="2.2"
                        strokeLinecap="round" strokeLinejoin="round"
                        stroke={isLight ? "rgba(0,0,0,.78)" : "#fff"} />
                </svg>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
