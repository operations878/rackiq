import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type {
  DataHealth as DataHealthT,
  QuarantineResponse,
  QuarantineRow,
  CrosswalkEntry,
  AuditEntry,
  StudioState,
} from "../api/types";
import Panel from "../components/Panel";
import { humanize } from "../lib/format";

function gradeTone(grade: string): string {
  return { A: "text-emerald-600", B: "text-emerald-600", C: "text-amber-600", D: "text-orange-600", F: "text-red-600" }[grade] ?? "text-slate-600";
}

function ScoreCard({ health }: { health: DataHealthT }) {
  return (
    <Panel title="Overall data-health score">
      <div className="flex items-center gap-6">
        <div className="text-center">
          <div className={`text-5xl font-bold ${gradeTone(health.grade)}`}>{health.score}</div>
          <div className="text-xs text-slate-400">grade {health.grade} · /100</div>
        </div>
        <div className="flex-1 space-y-2">
          {health.components.map((c) => (
            <div key={c.key}>
              <div className="flex justify-between text-[11px] text-slate-500">
                <span>{humanize(c.key)}</span>
                <span>
                  {c.score}% <span className="text-slate-300">· w {Math.round(c.weight * 100)}%</span>
                </span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded bg-slate-200">
                <div
                  className={`h-1.5 rounded ${c.score >= 90 ? "bg-emerald-500" : c.score >= 70 ? "bg-amber-500" : "bg-red-500"}`}
                  style={{ width: `${c.score}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      </div>
    </Panel>
  );
}

function DriftPanel({ health }: { health: DataHealthT }) {
  const d = health.drift;
  const variants = d.customers.filter((c) => c.kind === "possible_variant");
  const newCodes = d.customers.filter((c) => c.kind === "new_code");
  return (
    <Panel title="Drift alerts">
      <div className="space-y-3 text-xs">
        <div className="flex flex-wrap gap-2">
          <span className="rounded bg-slate-100 px-2 py-0.5 text-slate-600">
            crosswalk masters: <b>{health.crosswalk.masters}</b>
          </span>
          <span className="rounded bg-slate-100 px-2 py-0.5 text-slate-600">
            quarantined: <b>{health.quarantine.total}</b>
          </span>
        </div>

        {d.volume?.alert ? (
          <div className="rounded border border-amber-200 bg-amber-50 p-2 text-amber-700">
            ⚠ Volume drift: {d.volume.month} is {d.volume.direction} the historical mean
            ({d.volume.value.toLocaleString()} vs {d.volume.mean.toLocaleString()}, z={d.volume.z}).
          </div>
        ) : (
          <div className="rounded border border-slate-200 bg-slate-50 p-2 text-slate-500">
            Volume in line with history{d.volume ? ` (z=${d.volume.z})` : ""}.
          </div>
        )}

        {variants.length > 0 && (
          <div className="rounded border border-orange-200 bg-orange-50 p-2">
            <div className="font-semibold text-orange-700">
              {variants.length} possible un-merged customer variant(s)
            </div>
            <ul className="mt-1 space-y-0.5 text-orange-700">
              {variants.slice(0, 8).map((v) => (
                <li key={v.code} className="font-mono text-[11px]">
                  {v.code} <span className="text-orange-400">≈ {v.near} ({Math.round((v.similarity ?? 0) * 100)}%)</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="text-slate-500">
          {newCodes.length} new customer code(s) not yet in the crosswalk
          {newCodes.length > 0 && (
            <span className="ml-1 font-mono text-[11px] text-slate-400">
              {newCodes.slice(0, 6).map((c) => c.code).join(", ")}
              {newCodes.length > 6 ? " …" : ""}
            </span>
          )}
        </div>
      </div>
    </Panel>
  );
}

function QuarantineRowCard({
  row,
  onReimport,
  onDiscard,
  busy,
}: {
  row: QuarantineRow;
  onReimport: (id: string, edits: Record<string, unknown>) => void;
  onDiscard: (id: string) => void;
  busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const fields = Object.keys(row.payload);

  return (
    <div className="rounded-lg border border-slate-200 bg-white">
      <button onClick={() => setOpen((v) => !v)} className="flex w-full items-center justify-between px-3 py-2 text-left">
        <div className="flex flex-wrap items-center gap-2">
          {row.reasons.map((r) => (
            <span key={r} className="rounded bg-red-50 px-1.5 py-0.5 text-[10px] font-medium text-red-600">
              {humanize(r)}
            </span>
          ))}
          <span className="font-mono text-[11px] text-slate-500">{row.filename}</span>
        </div>
        <span className="text-slate-300">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="border-t border-slate-100 p-3">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {fields.map((f) => (
              <label key={f} className="text-[11px]">
                <span className="text-slate-400">{humanize(f)}</span>
                <input
                  defaultValue={row.payload[f] === null ? "" : String(row.payload[f])}
                  onChange={(e) => setEdits((p) => ({ ...p, [f]: e.target.value }))}
                  className="mt-0.5 w-full rounded border border-slate-300 px-2 py-1 text-xs"
                />
              </label>
            ))}
          </div>
          <div className="mt-3 flex justify-end gap-2">
            <button
              onClick={() => onDiscard(row.id)}
              disabled={busy}
              className="rounded border border-slate-300 px-2.5 py-1 text-[11px] text-slate-600 hover:bg-slate-50 disabled:opacity-50"
            >
              Discard
            </button>
            <button
              onClick={() => onReimport(row.id, edits)}
              disabled={busy}
              className="rounded bg-emerald-600 px-3 py-1 text-[11px] font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
            >
              Fix &amp; re-import
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function CrosswalkBrowser({ entries, onDelete }: { entries: CrosswalkEntry[]; onDelete: (k: string) => void }) {
  const byMaster: Record<string, CrosswalkEntry[]> = {};
  for (const e of entries) {
    (byMaster[e.master_id] ??= []).push(e);
  }
  const masters = Object.keys(byMaster).sort();
  return (
    <Panel title={`Customer Master crosswalk · ${entries.length} variant(s)`}>
      {masters.length === 0 ? (
        <p className="text-xs text-slate-400">No merge decisions yet. Resolve customers in Data Studio.</p>
      ) : (
        <div className="space-y-3">
          {masters.map((m) => (
            <div key={m}>
              <div className="text-xs font-semibold text-slate-700">
                {byMaster[m][0].master_name || m}{" "}
                <span className="font-mono text-[11px] text-slate-400">{m}</span>
              </div>
              <ul className="mt-1 space-y-0.5">
                {byMaster[m].map((e) => (
                  <li key={e.variant_key} className="flex items-center justify-between text-[11px]">
                    <span className="font-mono text-slate-600">
                      {e.variant_key}
                      {e.status === "rejected" && <span className="ml-1 text-slate-400">(kept separate)</span>}
                    </span>
                    <button onClick={() => onDelete(e.variant_key)} className="text-slate-400 hover:text-red-600">
                      remove
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function AuditLog({ entries }: { entries: AuditEntry[] }) {
  return (
    <Panel title="Hygiene audit log">
      {entries.length === 0 ? (
        <p className="text-xs text-slate-400">No transformations logged yet.</p>
      ) : (
        <div className="max-h-72 overflow-y-auto">
          <table className="w-full text-xs">
            <thead className="text-left text-[10px] uppercase tracking-wide text-slate-400">
              <tr>
                <th className="py-1">When</th>
                <th className="py-1">Table</th>
                <th className="py-1">Step</th>
                <th className="py-1">Detail</th>
                <th className="py-1 text-right">Rows</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e, i) => (
                <tr key={i} className="border-t border-slate-100">
                  <td className="py-1 font-mono text-[10px] text-slate-400">{e.at?.replace("T", " ").slice(0, 19)}</td>
                  <td className="py-1 text-slate-500">{humanize(e.target_table)}</td>
                  <td className="py-1 font-mono text-[11px] text-slate-600">{e.step}</td>
                  <td className="py-1 text-slate-600">{e.detail}</td>
                  <td className="py-1 text-right text-slate-500">{e.rows_affected || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

export default function DataHealth({
  navigate,
  onState,
}: {
  navigate: (to: string) => void;
  onState: (s: StudioState) => void;
}) {
  const [health, setHealth] = useState<DataHealthT | null>(null);
  const [quarantine, setQuarantine] = useState<QuarantineResponse | null>(null);
  const [crosswalk, setCrosswalk] = useState<CrosswalkEntry[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const reload = useCallback(() => {
    Promise.all([api.studio.dataHealth(), api.studio.quarantine(), api.studio.crosswalkList(), api.studio.audit(50)])
      .then(([h, q, c, a]) => {
        setHealth(h);
        setQuarantine(q);
        setCrosswalk(c.crosswalk);
        setAudit(a.audit);
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(reload, [reload]);

  async function reimport(id: string, edits: Record<string, unknown>) {
    setBusy(true);
    setNotice(null);
    try {
      const cleaned = Object.fromEntries(Object.entries(edits).filter(([, v]) => v !== "" && v != null));
      const r = await api.studio.quarantineReimport({ ids: [id], edits: { [id]: cleaned } });
      onState({ summary: r.summary, capabilities: r.capabilities });
      setNotice(r.reimported ? `Re-imported ${r.reimported} row.` : "Row still fails validation — adjust the values and try again.");
      reload();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  async function discard(id: string) {
    setBusy(true);
    try {
      await api.studio.quarantineDiscard({ ids: [id] });
      reload();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  async function reimportAll() {
    setBusy(true);
    setNotice(null);
    try {
      const r = await api.studio.quarantineReimport({});
      onState({ summary: r.summary, capabilities: r.capabilities });
      setNotice(`Re-imported ${r.reimported} row(s); ${r.still_quarantined} still need attention.`);
      reload();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  async function deleteCrosswalk(key: string) {
    await api.studio.crosswalkDelete(key).catch(() => {});
    reload();
  }

  if (error) return <div className="rounded-lg bg-red-50 p-3 text-xs text-red-700">{error}</div>;
  if (!health) return <div className="text-sm text-slate-500">Loading data health…</div>;

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold tracking-tight text-slate-900">Data Health</h1>
          <p className="text-xs text-slate-500">
            Standing quality score, drift alerts, quarantine review, and the hygiene audit trail.
          </p>
        </div>
        <button
          onClick={() => navigate("studio")}
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
        >
          + Import data
        </button>
      </div>

      {notice && <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-xs text-blue-700">{notice}</div>}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <ScoreCard health={health} />
        <DriftPanel health={health} />
      </div>

      <Panel
        title={`Quarantine — ${quarantine?.total ?? 0} row(s) held for review`}
      >
        {quarantine && quarantine.total > 0 ? (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <p className="text-[11px] text-slate-500">
                Rows that failed validation, never silently dropped. Fix the values and re-import, or discard.
              </p>
              <button
                onClick={reimportAll}
                disabled={busy}
                className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                Re-import all (re-run rules)
              </button>
            </div>
            {quarantine.rows.map((r) => (
              <QuarantineRowCard key={r.id} row={r} onReimport={reimport} onDiscard={discard} busy={busy} />
            ))}
          </div>
        ) : (
          <p className="text-xs text-emerald-600">✓ Quarantine is empty — every imported row passed validation.</p>
        )}
      </Panel>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <CrosswalkBrowser entries={crosswalk} onDelete={deleteCrosswalk} />
        <AuditLog entries={audit} />
      </div>
    </div>
  );
}
