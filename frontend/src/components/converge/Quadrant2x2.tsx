/**
 * The planning 2×2 with THIS customer plotted — the "introduce the strangers" drill-down under the
 * channel/steadiness tile. X = cadence consistency (when), Y = size consistency (how much), with the
 * quadrant cutoffs drawn (60 / 65). Pure presentation off the two existing axis scores — no new math.
 */
const X_CUT = 60; // cadence ≥ 60 ⇒ regular timing
const Y_CUT = 65; // size ≥ 65 ⇒ consistent size

export default function Quadrant2x2({ cadence, size, label }: {
  cadence: number | null; size: number | null; label?: string;
}) {
  const W = 240, H = 200, pad = 28;
  const px = (v: number) => pad + (v / 100) * (W - 2 * pad);
  const py = (v: number) => H - pad - (v / 100) * (H - 2 * pad);
  const has = cadence != null && size != null;
  const cx = has ? px(cadence!) : null;
  const cy = has ? py(size!) : null;

  const cells = [
    { x: X_CUT, y: Y_CUT, w: 100 - X_CUT, h: 100 - Y_CUT, fill: "#ecfdf5", label: "Metronome" }, // TR
    { x: 0, y: Y_CUT, w: X_CUT, h: 100 - Y_CUT, fill: "#fffbeb", label: "Predictable size" }, // TL
    { x: X_CUT, y: 0, w: 100 - X_CUT, h: Y_CUT, fill: "#fffbeb", label: "Predictable timing" }, // BR
    { x: 0, y: 0, w: X_CUT, h: Y_CUT, fill: "#fff1f2", label: "Unpredictable" }, // BL
  ];

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full max-w-[280px]">
        {cells.map((c, i) => (
          <g key={i}>
            <rect x={px(c.x)} y={py(c.y + c.h)} width={px(c.x + c.w) - px(c.x)}
              height={py(c.y) - py(c.y + c.h)} fill={c.fill} />
            <text x={px(c.x) + 4} y={py(c.y + c.h) + 11} className="fill-slate-400" style={{ fontSize: 7 }}>
              {c.label}
            </text>
          </g>
        ))}
        {/* cut lines */}
        <line x1={px(X_CUT)} y1={py(0)} x2={px(X_CUT)} y2={py(100)} stroke="#cbd5e1" strokeWidth={1} strokeDasharray="3 3" />
        <line x1={px(0)} y1={py(Y_CUT)} x2={px(100)} y2={py(Y_CUT)} stroke="#cbd5e1" strokeWidth={1} strokeDasharray="3 3" />
        {/* axes */}
        <line x1={px(0)} y1={py(0)} x2={px(100)} y2={py(0)} stroke="#94a3b8" strokeWidth={1} />
        <line x1={px(0)} y1={py(0)} x2={px(0)} y2={py(100)} stroke="#94a3b8" strokeWidth={1} />
        <text x={W / 2} y={H - 6} textAnchor="middle" className="fill-slate-500" style={{ fontSize: 8 }}>
          cadence — when they lift →
        </text>
        <text x={10} y={H / 2} textAnchor="middle" transform={`rotate(-90 10 ${H / 2})`}
          className="fill-slate-500" style={{ fontSize: 8 }}>
          size — how much →
        </text>
        {/* the customer */}
        {has && cx != null && cy != null && (
          <>
            <circle cx={cx} cy={cy} r={5} fill="#4338ca" stroke="white" strokeWidth={1.5} />
            <text x={cx} y={cy - 9} textAnchor="middle" className="fill-indigo-700" style={{ fontSize: 8, fontWeight: 600 }}>
              {label ?? "this account"}
            </text>
          </>
        )}
      </svg>
      {has ? (
        <div className="mt-1 text-[11px] text-slate-500">
          Plotted at cadence <b>{Math.round(cadence!)}</b> × size <b>{Math.round(size!)}</b>. The dashed
          lines are the cutoffs (60 / 65) that name the quadrant.
        </div>
      ) : (
        <div className="mt-1 text-[11px] text-slate-400">Not enough history to plot this account yet.</div>
      )}
    </div>
  );
}
