import ReactDOM from "react-dom";

export function TipRow({ label, value, color = "var(--text-2)" }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 16, marginTop: 2 }}>
      <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 10, color: "var(--text-3)" }}>{label}</span>
      <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 10, color, fontWeight: 600 }}>{value}</span>
    </div>
  );
}

export function TipHeader({ children }) {
  return (
    <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, color: "var(--text-1)", fontWeight: 700, marginBottom: 6 }}>
      {children}
    </div>
  );
}

export default function HoverTooltip({ pos, children }) {
  if (!pos || !children) return null;
  return ReactDOM.createPortal(
    <div style={{
      position: "fixed", left: pos.x + 14, top: pos.y - 10, zIndex: 9999,
      background: "#141412", border: "1px solid #333",
      borderRadius: 6, padding: "8px 10px", pointerEvents: "none",
      boxShadow: "0 4px 16px rgba(0,0,0,0.6)", minWidth: 160,
    }}>
      {children}
    </div>,
    document.body
  );
}
