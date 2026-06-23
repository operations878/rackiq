import { useState } from "react";
import { api } from "../../api/client";
import type { InspectResponse, MergeGroup, ProposeResponse } from "../../api/types";

type Edit = { master_id: string; master_name: string; members: Set<string> };

function confTone(c: number): string {
  if (c >= 0.92) return "bg-emerald-100 text-emerald-700";
  if (c >= 0.85) return "bg-amber-100 text-amber-700";
  return "bg-orange-100 text-orange-700";
}

export default function CustomerMasterPanel({
  inspect,
  uploadId,
  table,
  mapping,
  onResolved,
}: {
  inspect: InspectResponse;
  uploadId: string;
  table: string;
  mapping: Record<string, string>;
  onResolved: () => void;
}) {
  const [proposal, setProposal] = useState<ProposeResponse | null>(null);
  const [edits, setEdits] = useState<Record<string, Edit>>({});
  const [done, setDone] = useState<Record<string, "merged" | "rejected">>({});
  const [nameSource, setNameSource] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function findDuplicates() {
    setBusy(true);
    setError(null);
    try {
      const p = await api.studio.crosswalkPropose({
        upload_id: uploadId,
        table,
        mapping,
        name_source: nameSource || null,
      });
      setProposal(p);
      const e: Record<string, Edit> = {};
      for (const g of p.groups) {
        e[g.group_id] = {
          master_id: g.master_id,
          master_name: g.master_name,
          members: new Set(g.members.map((m) => m.key)),
        };
      }
      setEdits(e);
      setDone({});
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }

  async function confirmGroup(g: MergeGroup) {
    const e = edits[g.group_id];
    const members = g.members.map((m) => m.key).filter((k) => e.members.has(k));
    if (members.length < 1 || !e.master_id.trim()) return;
    setBusy(true);
    try {
      await api.studio.crosswalkConfirm({
        groups: [{ master_id: e.master_id.trim(), master_name: e.master_name.trim(), members }],
        rejected_keys: [],
      });
      setDone((d) => ({ ...d, [g.group_id]: "merged" }));
      onResolved();
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }

  async function rejectGroup(g: MergeGroup) {
    setBusy(true);
    try {
      await api.studio.crosswalkConfirm({
        groups: [],
        rejected_keys: g.members.filter((m) => m.in_file).map((m) => m.key),
      });
      setDone((d) => ({ ...d, [g.group_id]: "rejected" }));
      onResolved();
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }

  function toggleMember(groupId: string, key: string) {
    setEdits((prev) => {
      const e = prev[groupId];
      const members = new Set(e.members);
      if (members.has(key)) members.delete(key);
      else members.add(key);
      return { ...prev, [groupId]: { ...e, members } };
    });
  }

  const mergedCount = Object.values(done).filter((v) => v === "merged").length;
  const rejectedCount = Object.values(done).filter((v) => v === "rejected").length;
  const open = proposal?.groups.filter((g) => !done[g.group_id]) ?? [];

  return (
    <div className="rounded-lg border border-indigo-200 bg-indigo-50/40 p-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-indigo-700">
            Customer Master · de-duplication
          </h3>
          <p className="text-[11px] text-slate-500">
            Detect the same customer under different spellings/ids and merge to one master.
          </p>
        </div>
        <button
          onClick={findDuplicates}
          disabled={busy}
          className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          {busy && !proposal ? "Scanning…" : proposal ? "Re-scan" : "Find duplicate customers"}
        </button>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
        <span>Name column (optional, aids matching):</span>
        <select
          value={nameSource}
          onChange={(e) => setNameSource(e.target.value)}
          className="rounded border border-slate-300 px-2 py-0.5 text-[11px]"
        >
          <option value="">— none —</option>
          {inspect.columns.map((c) => (
            <option key={c.name} value={c.name}>
              {c.name}
            </option>
          ))}
        </select>
      </div>

      {error && <div className="mt-2 rounded bg-red-50 p-2 text-[11px] text-red-700">{error}</div>}

      {proposal && (
        <div className="mt-3 space-y-3">
          <div className="flex flex-wrap gap-3 text-[11px] text-slate-600">
            <span>{proposal.n_distinct_keys} distinct ids</span>
            <span className="text-indigo-700">{proposal.n_groups} merge groups proposed</span>
            {mergedCount > 0 && <span className="text-emerald-700">✓ {mergedCount} merged</span>}
            {rejectedCount > 0 && <span className="text-slate-500">✕ {rejectedCount} kept separate</span>}
            <span>{proposal.n_new_singletons} unique (no action)</span>
          </div>

          {open.length === 0 && (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-700">
              {proposal.n_groups === 0
                ? "No likely duplicates found — every customer id looks distinct."
                : "All proposed groups handled. Decisions are saved to the crosswalk for future uploads."}
            </div>
          )}

          {open.map((g) => {
            const e = edits[g.group_id];
            return (
              <div key={g.group_id} className="rounded-lg border border-slate-200 bg-white p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${confTone(g.confidence)}`}>
                    {Math.round(g.confidence * 100)}% match
                  </span>
                  {g.from_existing && (
                    <span className="text-[10px] font-medium text-indigo-600">↳ existing master</span>
                  )}
                </div>

                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <label className="text-[11px] text-slate-500">Master id</label>
                  <input
                    value={e.master_id}
                    onChange={(ev) =>
                      setEdits((p) => ({ ...p, [g.group_id]: { ...e, master_id: ev.target.value } }))
                    }
                    className="w-44 rounded border border-slate-300 px-2 py-1 text-xs"
                  />
                  <label className="text-[11px] text-slate-500">name</label>
                  <input
                    value={e.master_name}
                    onChange={(ev) =>
                      setEdits((p) => ({ ...p, [g.group_id]: { ...e, master_name: ev.target.value } }))
                    }
                    className="w-44 rounded border border-slate-300 px-2 py-1 text-xs"
                  />
                </div>

                <ul className="mt-2 space-y-1">
                  {g.members.map((m) => (
                    <li key={m.key} className="flex items-center justify-between text-xs">
                      <label className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={e.members.has(m.key)}
                          onChange={() => toggleMember(g.group_id, m.key)}
                        />
                        <span className="font-mono text-slate-700">{m.key}</span>
                        {!m.in_file && <span className="text-[10px] text-slate-400">(known)</span>}
                        {m.already_confirmed && <span className="text-[10px] text-emerald-600">✓ confirmed</span>}
                      </label>
                      <span className="text-[11px] text-slate-400">
                        {m.count > 0 ? `${m.count} rows · ` : ""}
                        {Math.round(m.similarity * 100)}%
                      </span>
                    </li>
                  ))}
                </ul>

                <div className="mt-2 flex justify-end gap-2">
                  <button
                    onClick={() => rejectGroup(g)}
                    disabled={busy}
                    className="rounded border border-slate-300 px-2.5 py-1 text-[11px] text-slate-600 hover:bg-slate-50 disabled:opacity-50"
                  >
                    Keep separate
                  </button>
                  <button
                    onClick={() => confirmGroup(g)}
                    disabled={busy}
                    className="rounded bg-indigo-600 px-3 py-1 text-[11px] font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
                  >
                    Merge to master
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
