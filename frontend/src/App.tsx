import { useEffect, useState, type ReactNode } from "react";
import { api } from "./api/client";
import type { Summary, Capabilities, StudioState } from "./api/types";
import { useHashRoute } from "./lib/useHashRoute";

// The convergence spine — one view per real-world unit, behind one front door.
import Home from "./pages/Home";
import Customers from "./pages/Customers";
import CustomerProfile from "./pages/CustomerProfile";
import Terminals from "./pages/Terminals";
import TerminalProfile from "./pages/TerminalProfile";
import Opportunity from "./pages/Opportunity";
import DataSources from "./pages/DataSources";
import Glossary from "./pages/Glossary";

// The original per-engine pages — kept, reachable under "Advanced", never required for the daily path.
import VarHome from "./pages/VarHome";
import Variability from "./pages/Variability";
import Dashboard from "./pages/Dashboard";
import DataStudio from "./pages/DataStudio";
import DataHealth from "./pages/DataHealth";
import Scores from "./pages/Scores";
import Reconciliation from "./pages/Reconciliation";
import DailyOps from "./pages/DailyOps";
import DemandCockpit from "./pages/DemandCockpit";
import Hedging from "./pages/Hedging";
import Calendar from "./pages/Calendar";
import Pricing from "./pages/Pricing";
import BookOverview from "./pages/BookOverview";
import Radar from "./pages/Radar";
import Scorecards from "./pages/Scorecards";
import Playbook from "./pages/Playbook";

function Centered({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-6 text-center text-slate-600">
      <div>{children}</div>
    </div>
  );
}

const PRIMARY = [
  { key: "", label: "Home" },
  { key: "customers", label: "Customers" },
  { key: "terminals", label: "Terminals" },
  { key: "opportunity", label: "Opportunity" },
  { key: "data", label: "Data" },
  { key: "glossary", label: "Glossary" },
];

// "Advanced" preserves every original per-engine view (nothing removed) — grouped, de-emphasized,
// and never needed for the morning path.
const ADVANCED: { title: string; items: { key: string; label: string }[] }[] = [
  {
    title: "Operate", items: [
      { key: "daily", label: "Daily Operating" },
      { key: "demand", label: "Demand Cockpit" },
      { key: "hedging", label: "Demand Hedging" },
      { key: "pricing", label: "Pricing Sandbox" },
      { key: "scorecards", label: "Scorecards" },
      { key: "playbook", label: "Sales Playbook" },
    ],
  },
  {
    title: "Analyze", items: [
      { key: "varhome", label: "VAR Home" },
      { key: "overview", label: "Book Overview" },
      { key: "variability", label: "Spot vs Rack" },
      { key: "radar", label: "Early-Warning Radar" },
      { key: "scores", label: "Scores & Quadrant" },
      { key: "reconciliation", label: "Reconciliation" },
      { key: "capabilities", label: "Capabilities" },
    ],
  },
  {
    title: "Data tools", items: [
      { key: "studio", label: "Data Studio" },
      { key: "calendar", label: "Working-Day Calendar" },
      { key: "health", label: "Data Health" },
    ],
  },
];

function AdvancedMenu({ base, navigate, quarantine }: {
  base: string; navigate: (to: string) => void; quarantine: number;
}) {
  const [open, setOpen] = useState(false);
  const activeInside = ADVANCED.some((g) => g.items.some((i) => i.key === base));
  return (
    <div className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className={`flex items-center gap-1 rounded-lg px-3 py-1.5 text-sm font-medium transition ${
          activeInside ? "bg-slate-100 text-slate-900" : "text-slate-500 hover:text-slate-800"}`}>
        Advanced
        {quarantine > 0 && <span className="rounded-full bg-amber-500 px-1.5 text-[10px] font-semibold text-white">{quarantine}</span>}
        <span className="text-[9px]">▾</span>
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-30 mt-1 w-64 rounded-xl border border-slate-200 bg-white p-2 shadow-lg">
            <div className="px-2 pb-1.5 pt-1 text-[10px] text-slate-400">
              The original per-engine views — everything's still here, one click away.
            </div>
            {ADVANCED.map((g) => (
              <div key={g.title} className="mb-1">
                <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400">{g.title}</div>
                {g.items.map((i) => (
                  <button key={i.key}
                    onClick={() => { navigate(i.key); setOpen(false); }}
                    className={`flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm ${
                      base === i.key ? "bg-indigo-50 text-indigo-700" : "text-slate-600 hover:bg-slate-100"}`}>
                    {i.label}
                    {i.key === "health" && quarantine > 0 && (
                      <span className="rounded-full bg-amber-500 px-1.5 text-[10px] font-semibold text-white">{quarantine}</span>
                    )}
                  </button>
                ))}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

export default function App() {
  const [route, navigate] = useHashRoute();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.summary(), api.capabilities()])
      .then(([s, c]) => { setSummary(s); setCaps(c); })
      .catch((e) => setError(String(e)));
  }, []);

  function applyState(s: StudioState) {
    setSummary(s.summary);
    setCaps(s.capabilities);
  }

  if (error) {
    return (
      <Centered>
        <div className="text-lg">⚠️ Could not reach the RackIQ API</div>
        <div className="mt-2 font-mono text-xs text-slate-400">{error}</div>
        <div className="mt-2 text-xs text-slate-400">
          Start the backend: <code>cd backend &amp;&amp; uv run rackiq-serve</code>
        </div>
      </Centered>
    );
  }
  if (!summary || !caps) return <Centered>Loading RackIQ…</Centered>;

  const base = route.split("/")[0];
  const after = (prefix: string) => (route.startsWith(prefix) ? route.slice(prefix.length) : undefined);
  const customerId = after("customer/");
  const terminalName = after("terminal/");
  const scorecardId = after("scorecard/");
  const quarantine = summary.quarantine_total ?? 0;

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      {/* one calm top nav — the single front door */}
      <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-1 px-4">
          <button onClick={() => navigate("")} className="mr-3 flex items-baseline gap-1.5">
            <span className="text-base font-bold tracking-tight text-slate-900">RackIQ</span>
          </button>
          <nav className="flex items-center gap-0.5">
            {PRIMARY.map((it) => {
              const active = it.key === base || (it.key === "customers" && base === "customer")
                || (it.key === "terminals" && base === "terminal");
              return (
                <button key={it.key} onClick={() => navigate(it.key)}
                  className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${
                    active ? "bg-slate-900 text-white" : "text-slate-500 hover:bg-slate-100 hover:text-slate-800"}`}>
                  {it.label}
                </button>
              );
            })}
          </nav>
          <div className="ml-auto">
            <AdvancedMenu base={base} navigate={navigate} quarantine={quarantine} />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-8">
        {/* convergence spine */}
        {base === "" && <Home navigate={navigate} />}
        {base === "customers" && <Customers navigate={navigate} />}
        {base === "customer" && customerId && <CustomerProfile id={customerId} navigate={navigate} />}
        {base === "terminals" && <Terminals navigate={navigate} />}
        {base === "terminal" && terminalName && <TerminalProfile name={decodeURIComponent(terminalName)} navigate={navigate} />}
        {base === "opportunity" && <Opportunity navigate={navigate} />}
        {base === "data" && <DataSources navigate={navigate} />}
        {base === "glossary" && <Glossary />}

        {/* advanced / original per-engine views */}
        {base === "varhome" && <VarHome summary={summary} navigate={navigate} />}
        {base === "daily" && <DailyOps summary={summary} navigate={navigate} />}
        {base === "demand" && <DemandCockpit summary={summary} navigate={navigate} />}
        {base === "hedging" && <Hedging summary={summary} navigate={navigate} />}
        {base === "calendar" && <Calendar summary={summary} navigate={navigate} />}
        {base === "pricing" && <Pricing summary={summary} navigate={navigate} />}
        {(base === "scorecards" || base === "scorecard") && <Scorecards summary={summary} customerId={scorecardId} />}
        {base === "playbook" && <Playbook summary={summary} />}
        {base === "overview" && <BookOverview summary={summary} navigate={navigate} />}
        {base === "variability" && <Variability summary={summary} navigate={navigate} />}
        {base === "radar" && <Radar summary={summary} />}
        {base === "scores" && <Scores summary={summary} />}
        {base === "reconciliation" && <Reconciliation summary={summary} navigate={navigate} />}
        {base === "capabilities" && <Dashboard summary={summary} caps={caps} navigate={navigate} />}
        {base === "studio" && <DataStudio caps={caps} summary={summary} onState={applyState} navigate={navigate} />}
        {base === "health" && <DataHealth navigate={navigate} onState={applyState} />}

        <footer className="mt-12 border-t border-slate-100 pt-4 text-center text-xs text-slate-400">
          RackIQ · {summary.connected ? `${summary.customers} customers · ${summary.lifts.toLocaleString()} lifts` : "no data loaded"}
          {summary.date_range?.end ? ` · through ${summary.date_range.end}` : ""}
        </footer>
      </main>
    </div>
  );
}
