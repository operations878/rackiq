import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import type { ProductMapResult, StudioState, UnmappedProduct } from "../../api/types";
import { fmtGal } from "../../lib/scoreui";

/** Product Reference chart — upload a two-column file (Raw Product Code → Standardized Code).
 *  Loads it as the confirmed source of truth, restates the product on every loaded + future row
 *  to the standardized code, and lists any raw codes still unmapped so the user can extend it. */
export default function ProductMapPanel({
  onState,
  compact = false,
}: {
  onState?: (s: StudioState) => void;
  compact?: boolean;
}) {
  const [unmapped, setUnmapped] = useState<UnmappedProduct[]>([]);
  const [standards, setStandards] = useState(0);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ProductMapResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  function refresh() {
    api.studio
      .unmappedProducts()
      .then((u) => {
        setUnmapped(u.unmapped);
        setStandards(u.product_standards);
      })
      .catch(() => {});
  }
  useEffect(refresh, []);

  async function handleFile(file: File) {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await api.studio.uploadProductMap(file);
      setResult(r);
      setUnmapped(r.unmapped);
      setStandards(r.product_standards);
      onState?.({ summary: r.summary, capabilities: r.capabilities });
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-indigo-700">
          Product Reference Chart
        </h3>
        <p className="text-[11px] text-slate-500">
          Upload a two-column file — <span className="font-medium">Raw Product Code → Standardized Code</span>.
          Every raw description rolls up into one standardized product code. Re-upload any time to extend it.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <label className="cursor-pointer rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500">
          {busy ? "Loading…" : "Upload product chart"}
          <input
            ref={inputRef}
            type="file"
            accept=".csv,.tsv,.txt,.xlsx,.xls"
            className="hidden"
            disabled={busy}
            onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
          />
        </label>
        <span className="text-[11px] text-slate-400">{standards} standardized code(s)</span>
      </div>

      {error && <div className="rounded bg-red-50 p-2 text-[11px] text-red-700">{error}</div>}

      {result && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-2.5 text-[11px] text-emerald-800">
          Loaded <b>{result.loaded}</b> mapping(s) → <b>{result.standards}</b> standardized code(s); restated{" "}
          <b>{result.total_remapped.toLocaleString()}</b> existing row(s). Detected columns:{" "}
          <span className="font-mono">{result.raw_column}</span> → <span className="font-mono">{result.standard_column}</span>.
        </div>
      )}

      <div>
        <div className="mb-1 flex items-center justify-between">
          <h4 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Unmapped products {unmapped.length > 0 && <span className="text-amber-600">({unmapped.length})</span>}
          </h4>
        </div>
        {unmapped.length === 0 ? (
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-2.5 text-[11px] text-emerald-700">
            ✓ Every product code is standardized.
          </div>
        ) : (
          <>
            <p className="mb-1 text-[10px] text-slate-400">
              These raw product codes aren't in your chart yet — shown as-is. Add them and re-upload.
            </p>
            <div className={`overflow-auto ${compact ? "max-h-44" : "max-h-72"} rounded-lg border border-slate-200`}>
              <table className="w-full text-[11px]">
                <thead className="sticky top-0 bg-slate-50 text-left text-[10px] uppercase tracking-wide text-slate-400">
                  <tr>
                    <th className="px-2 py-1">Raw product code</th>
                    <th className="px-2 py-1 text-right">Lifts</th>
                    <th className="px-2 py-1 text-right">Gallons</th>
                  </tr>
                </thead>
                <tbody>
                  {unmapped.map((u) => (
                    <tr key={u.product} className="border-t border-slate-100">
                      <td className="px-2 py-1 font-mono text-slate-700">{u.product}</td>
                      <td className="px-2 py-1 text-right text-slate-500">{u.lift_count}</td>
                      <td className="px-2 py-1 text-right text-slate-500">{fmtGal(u.total_net_gallons)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
