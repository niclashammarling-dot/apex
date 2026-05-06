import React, { useState, useRef } from "react";
import { createPortal } from "react-dom";

/**
 * Styled tooltip — renders via portal at document.body so it is never
 * clipped by ancestor overflow:hidden containers.
 *
 * Props:
 *   label    — optional bold header line (colored)
 *   content  — body text (string or ReactNode)
 *   color    — accent color for border and label (default "var(--text-2)")
 *   position — "above" (default) | "below"
 *   display  — CSS display for the trigger wrapper (default "inline-flex")
 *   wrap     — allow content to wrap with maxWidth 240px (default false)
 */
export default function Tooltip({
  children,
  label,
  content,
  color = "var(--text-2)",
  position = "above",
  display = "inline-flex",
  wrap = false,
}) {
  const [visible, setVisible] = useState(false);
  const [coords,  setCoords]  = useState({ top: 0, left: 0 });
  const ref = useRef(null);

  if (!content && !label) return children;

  function handleMouseEnter() {
    if (ref.current) {
      const r = ref.current.getBoundingClientRect();
      setCoords({
        top:  position === "above" ? r.top : r.bottom,
        left: r.left + r.width / 2,
      });
    }
    setVisible(true);
  }

  return (
    <div
      ref={ref}
      style={{ position: "relative", display, cursor: "help" }}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={() => setVisible(false)}
    >
      {children}
      {visible && createPortal(
        <div style={{
          position: "fixed",
          top:  coords.top,
          left: coords.left,
          transform: position === "above"
            ? "translate(-50%, calc(-100% - 5px))"
            : "translate(-50%, 5px)",
          background: "#1f1f1c",
          border: `1px solid ${color}55`,
          borderRadius: 4,
          padding: "6px 9px",
          zIndex: 9999,
          pointerEvents: "none",
          whiteSpace: wrap ? "normal" : "nowrap",
          ...(wrap ? { maxWidth: 240, minWidth: 160 } : {}),
        }}>
          {label && (
            <div style={{
              fontFamily: "'Inconsolata', monospace", fontSize: 10,
              color, fontWeight: 700,
              marginBottom: content ? 2 : 0,
            }}>
              {label}
            </div>
          )}
          {content && (
            <div style={{
              fontFamily: "'Inconsolata', monospace", fontSize: 10,
              color: "var(--text-2)", lineHeight: 1.5,
            }}>
              {content}
            </div>
          )}
        </div>,
        document.body
      )}
    </div>
  );
}
