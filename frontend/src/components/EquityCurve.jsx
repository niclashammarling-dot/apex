import {
  LineChart, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, ReferenceLine, CartesianGrid,
} from "recharts";

function CustomTooltip({ active, payload, starting }) {
  if (!active || !payload?.length) return null;
  const pt    = payload[0].payload;
  const val   = payload[0].value;
  const diff  = val - starting;
  const sign  = diff >= 0 ? "+" : "";
  const color = diff >= 0 ? "var(--green)" : "var(--red)";
  return (
    <div style={{
      background: "var(--bg-header)", border: "1px solid var(--border)",
      padding: "10px 14px", borderRadius: 6,
      fontFamily: "'Inconsolata', monospace", fontSize: 13,
    }}>
      <div style={{ color: "var(--text-3)", marginBottom: 4 }}>
        {pt.ts}{pt.mtm ? " — live" : ""}
      </div>
      <div style={{ color: "var(--text-1)", fontWeight: 500 }}>
        ${val.toLocaleString("en-US", { minimumFractionDigits: 2 })}
      </div>
      <div style={{ color, marginTop: 2 }}>{sign}${diff.toFixed(2)}</div>
      {pt.mtm && pt.unrealised != null && (
        <div style={{ color: pt.unrealised >= 0 ? "var(--green)" : "var(--red)", marginTop: 4, fontSize: 11 }}>
          unrealised {pt.unrealised >= 0 ? "+" : ""}${pt.unrealised.toFixed(2)}
        </div>
      )}
    </div>
  );
}

export default function EquityCurve({ equity }) {
  if (!equity || equity.length < 2) {
    return (
      <div className="card">
        <div className="card-header"><div className="card-title">Equity Curve</div></div>
        <div className="card-body muted" style={{ height: 120, display: "flex", alignItems: "center" }}>
          No closed trades yet.
        </div>
      </div>
    );
  }

  const starting = equity[0].balance;
  const last    = equity[equity.length - 1].balance;
  const diff    = last - starting;
  const sign    = diff >= 0 ? "+" : "";
  const color   = diff >= 0 ? "var(--green)" : "var(--red)";
  const stroke  = diff >= 0 ? "#faff69" : "#ff7575";
  const minVal  = Math.min(...equity.map(d => d.balance));
  const maxVal  = Math.max(...equity.map(d => d.balance));
  const padding = (maxVal - minVal) * 0.1 || 100;

  return (
    <div className="card">
      <div className="card-header">
        <div className="card-title">Equity Curve</div>
        <div className="card-meta" style={{ color }}>
          {sign}${diff.toFixed(2)} ({sign}{((diff / starting) * 100).toFixed(2)}%)
        </div>
      </div>
      <div className="card-body" style={{ padding: "16px 16px 10px" }}>
        <ResponsiveContainer width="100%" height={150}>
          <LineChart data={equity} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#343434" vertical={false} />
            <XAxis
              dataKey="ts"
              tick={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, fill: "#a0a0a0" }}
              axisLine={false} tickLine={false}
            />
            <YAxis
              domain={[minVal - padding, maxVal + padding]}
              tick={{ fontFamily: "'Inconsolata', monospace", fontSize: 11, fill: "#a0a0a0" }}
              axisLine={false} tickLine={false} width={62}
              tickFormatter={v => `$${(v / 1000).toFixed(1)}k`}
            />
            <Tooltip content={<CustomTooltip starting={starting} />} />
            <ReferenceLine y={starting} stroke="#343434" strokeDasharray="4 4" />
            <Line
              type="monotone" dataKey="balance"
              stroke={stroke} strokeWidth={2}
              dot={(props) => {
                const pt = props.payload;
                if (!pt.mtm) return equity.length < 20
                  ? <circle key={props.key} cx={props.cx} cy={props.cy} r={3} fill={stroke} />
                  : null;
                // MTM point — hollow dot with dashed look
                return (
                  <circle key={props.key} cx={props.cx} cy={props.cy} r={5}
                    fill="var(--bg-card)" stroke={stroke} strokeWidth={2} strokeDasharray="3 2" />
                );
              }}
              activeDot={{ r: 5, fill: stroke }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
