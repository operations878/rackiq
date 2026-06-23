const STEPS = [
  { key: "upload", label: "Upload" },
  { key: "map", label: "Map Columns" },
  { key: "clean", label: "Clean" },
  { key: "validate", label: "Validate" },
  { key: "done", label: "Commit" },
];

export default function Stepper({ current }: { current: string }) {
  const idx = STEPS.findIndex((s) => s.key === current);
  return (
    <ol className="flex items-center gap-2 text-xs">
      {STEPS.map((s, i) => {
        const state = i < idx ? "done" : i === idx ? "active" : "todo";
        return (
          <li key={s.key} className="flex items-center gap-2">
            <span
              className={`flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-semibold ${
                state === "active"
                  ? "bg-slate-900 text-white"
                  : state === "done"
                    ? "bg-emerald-500 text-white"
                    : "bg-slate-200 text-slate-500"
              }`}
            >
              {state === "done" ? "✓" : i + 1}
            </span>
            <span className={state === "active" ? "font-semibold text-slate-800" : "text-slate-500"}>
              {s.label}
            </span>
            {i < STEPS.length - 1 && <span className="mx-1 text-slate-300">→</span>}
          </li>
        );
      })}
    </ol>
  );
}
