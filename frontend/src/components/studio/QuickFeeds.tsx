import { useState } from "react";
import { api } from "../../api/client";
import type { Summary, StudioState } from "../../api/types";

/**
 * Quick-entry forms for the early data feeds that "start accumulating now":
 *   • Rack benchmark — a daily street/OPIS rack price (date · terminal · product · price).
 *   • Quote logger   — a single quote outcome (the elasticity training set; rejections matter).
 * Both write through the same hygiene pipeline as a file import and refresh capabilities so
 * the "collecting — N logged" counters tick up live. Bulk CSV import uses the wizard.
 */

const today = () => new Date().toISOString().slice(0, 10);
const nowLocal = () => new Date().toISOString().slice(0, 16);

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-[11px]">
      <span className="text-slate-400">{label}</span>
      <div className="mt-0.5">{children}</div>
    </label>
  );
}

const inputCls = "w-full rounded border border-slate-300 px-2 py-1 text-xs";

export default function QuickFeeds({
  summary,
  onState,
}: {
  summary: Summary;
  onState: (s: StudioState) => void;
}) {
  const [tab, setTab] = useState<"rack" | "quote">("rack");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const terminals = summary.terminals.length ? summary.terminals : ["Linden", "Providence", "Albany"];
  const products = summary.products.length ? summary.products : ["RBOB", "ULSD", "ULSHO"];

  // rack benchmark form
  const [rDate, setRDate] = useState(today());
  const [rTerm, setRTerm] = useState(terminals[0]);
  const [rProd, setRProd] = useState(products[0]);
  const [rPrice, setRPrice] = useState("");

  // quote form
  const [qCust, setQCust] = useState("");
  const [qTime, setQTime] = useState(nowLocal());
  const [qProd, setQProd] = useState(products[0]);
  const [qPrice, setQPrice] = useState("");
  const [qMarket, setQMarket] = useState("");
  const [qOutcome, setQOutcome] = useState("reject");
  const [qFinal, setQFinal] = useState("");

  async function submitRack() {
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const r = await api.studio.rackBenchmark([
        { price_date: rDate, terminal: rTerm, product: rProd, rack_benchmark: parseFloat(rPrice) },
      ]);
      onState({ summary: r.summary, capabilities: r.capabilities });
      const days = r.capabilities.features.find((f) => f.key === "pricing_sandbox")?.collecting?.count ?? 0;
      setMsg(`Logged rack benchmark — ${days} day(s) collected.`);
      setRPrice("");
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  async function submitQuote() {
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const r = await api.studio.quote([
        {
          customer_id: qCust.trim(),
          quote_time: qTime.replace("T", " "),
          product: qProd,
          quoted_price: parseFloat(qPrice),
          outcome: qOutcome,
          market_price_at_quote: qMarket ? parseFloat(qMarket) : null,
          final_gallons: qOutcome === "accept" && qFinal ? parseFloat(qFinal) : null,
        },
      ]);
      onState({ summary: r.summary, capabilities: r.capabilities });
      const f = r.capabilities.features.find((x) => x.key === "quote_elasticity")?.collecting;
      setMsg(`Logged quote — ${f?.count ?? 0} quotes (${f?.rejections ?? 0} rejections) collected.`);
      setQPrice("");
      setQFinal("");
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  const rackValid = rPrice !== "" && !Number.isNaN(parseFloat(rPrice));
  const quoteValid = qCust.trim() !== "" && qPrice !== "" && !Number.isNaN(parseFloat(qPrice));

  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between">
        <span className="text-xs uppercase tracking-wide text-slate-500">Quick feeds</span>
        <span className="text-[10px] text-slate-400">collect history now</span>
      </div>
      <div className="flex gap-1 rounded-lg bg-slate-100 p-0.5 text-xs">
        <button
          onClick={() => setTab("rack")}
          className={`flex-1 rounded-md px-2 py-1 font-medium ${tab === "rack" ? "bg-white text-slate-900 shadow-sm" : "text-slate-500"}`}
        >
          Rack benchmark
        </button>
        <button
          onClick={() => setTab("quote")}
          className={`flex-1 rounded-md px-2 py-1 font-medium ${tab === "quote" ? "bg-white text-slate-900 shadow-sm" : "text-slate-500"}`}
        >
          Quote logger
        </button>
      </div>

      {tab === "rack" ? (
        <div className="space-y-2">
          <Field label="Date">
            <input type="date" value={rDate} onChange={(e) => setRDate(e.target.value)} className={inputCls} />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Terminal">
              <select value={rTerm} onChange={(e) => setRTerm(e.target.value)} className={inputCls}>
                {terminals.map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
            </Field>
            <Field label="Product">
              <select value={rProd} onChange={(e) => setRProd(e.target.value)} className={inputCls}>
                {products.map((p) => (
                  <option key={p}>{p}</option>
                ))}
              </select>
            </Field>
          </div>
          <Field label="Street / OPIS rack ($/gal)">
            <input type="number" step="0.0001" value={rPrice} onChange={(e) => setRPrice(e.target.value)} placeholder="2.7100" className={inputCls} />
          </Field>
          <button
            onClick={submitRack}
            disabled={busy || !rackValid}
            className="w-full rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            Log rack benchmark
          </button>
        </div>
      ) : (
        <div className="space-y-2">
          <Field label="Customer (resolved via crosswalk)">
            <input value={qCust} onChange={(e) => setQCust(e.target.value)} placeholder="e.g. C001 or Riverside Fuel" className={inputCls} />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Quote time">
              <input type="datetime-local" value={qTime} onChange={(e) => setQTime(e.target.value)} className={inputCls} />
            </Field>
            <Field label="Product">
              <select value={qProd} onChange={(e) => setQProd(e.target.value)} className={inputCls}>
                {products.map((p) => (
                  <option key={p}>{p}</option>
                ))}
              </select>
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Quoted price ($/gal)">
              <input type="number" step="0.0001" value={qPrice} onChange={(e) => setQPrice(e.target.value)} placeholder="2.7000" className={inputCls} />
            </Field>
            <Field label="Market @ quote ($/gal)">
              <input type="number" step="0.0001" value={qMarket} onChange={(e) => setQMarket(e.target.value)} placeholder="optional" className={inputCls} />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Outcome">
              <select value={qOutcome} onChange={(e) => setQOutcome(e.target.value)} className={inputCls}>
                <option value="accept">accept</option>
                <option value="reject">reject</option>
                <option value="no_response">no_response</option>
              </select>
            </Field>
            {qOutcome === "accept" && (
              <Field label="Final gallons">
                <input type="number" step="1" value={qFinal} onChange={(e) => setQFinal(e.target.value)} placeholder="5000" className={inputCls} />
              </Field>
            )}
          </div>
          <button
            onClick={submitQuote}
            disabled={busy || !quoteValid}
            className="w-full rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            Log quote
          </button>
          <p className="text-[10px] text-slate-400">
            Tip: log the <b>rejections</b> too — they’re the most valuable rows for elasticity.
          </p>
        </div>
      )}

      {msg && <div className="rounded border border-emerald-200 bg-emerald-50 p-2 text-[11px] text-emerald-700">{msg}</div>}
      {err && <div className="rounded border border-red-200 bg-red-50 p-2 text-[11px] text-red-700">{err}</div>}
    </div>
  );
}
