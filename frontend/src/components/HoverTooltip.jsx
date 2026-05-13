import ReactDOM from "react-dom";

export function TipRow({ label, value, color = "#b0b0a8" }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 32, marginTop: 4 }}>
      <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 20, color: "#666" }}>{label}</span>
      <span style={{ fontFamily: "'Inconsolata', monospace", fontSize: 20, color, fontWeight: 600 }}>{value}</span>
    </div>
  );
}

export function TipHeader({ children }) {
  return (
    <div style={{ fontFamily: "'Inconsolata', monospace", fontSize: 22, color: "#e8e8e6", fontWeight: 700, marginBottom: 12 }}>
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
      borderRadius: 6, padding: "16px 20px", pointerEvents: "none",
      boxShadow: "0 4px 16px rgba(0,0,0,0.6)", minWidth: 320,
    }}>
      {children}
    </div>,
    document.body
  );
}
