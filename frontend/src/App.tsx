import { useEffect, useState, type ReactNode } from "react";
import { api } from "./api/client";
import type { Summary, Capabilities, StudioState } from "./api/types";
import { useHashRoute } from "./lib/useHashRoute";
import ProfileBadge from "./components/ProfileBadge";
import Dashboard from "./pages/Dashboard";
import DataStudio from "./pages/DataStudio";
import DataHealth from "./pages/DataHealth";
import Scores from "./pages/Scores";
import Reconciliation from "./pages/Reconciliation";

function Centered({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-100 px-6 text-center text-slate-600">
      <div>{children}</div>
    </div>
  );
}

function NavLink({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${
        active ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"
      }`}
    >
      {label}
    </button>
  );
}

export default function App() {
  const [route, navigate] = useHashRoute();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.summary(), api.capabilities()])
      .then(([s, c]) => {
        setSummary(s);
        setCaps(c);
      })
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

  const onStudio = route === "studio";
  const onHealth = route === "health";
  const onScores = route === "scores";
  const onRecon = route === "reconciliation";
  const onDashboard = !onStudio && !onHealth && !onScores && !onRecon;
  const quarantine = summary.quarantine_total ?? 0;

  return (
    <div className="min-h-screen bg-slate-100 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-3">
          <div className="flex items-center gap-6">
            <div>
              <h1 className="text-lg font-bold tracking-tight">RackIQ</h1>
              <p className="text-[11px] text-slate-500">Customer Demand &amp; Margin Intelligence</p>
            </div>
            <nav className="flex items-center gap-1">
              <NavLink label="Dashboard" active={onDashboard} onClick={() => navigate("")} />
              <NavLink label="Scores" active={onScores} onClick={() => navigate("scores")} />
              <NavLink label="Reconciliation" active={onRecon} onClick={() => navigate("reconciliation")} />
              <NavLink label="Data Studio" active={onStudio} onClick={() => navigate("studio")} />
              <button
                onClick={() => navigate("health")}
                className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition ${
                  onHealth ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"
                }`}
              >
                Data Health
                {quarantine > 0 && (
                  <span className="rounded-full bg-amber-500 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                    {quarantine}
                  </span>
                )}
              </button>
            </nav>
          </div>
          <ProfileBadge profile={caps.profile} enabled={caps.summary.enabled} total={caps.summary.total} />
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-6">
        {onStudio && <DataStudio caps={caps} summary={summary} onState={applyState} navigate={navigate} />}
        {onHealth && <DataHealth navigate={navigate} onState={applyState} />}
        {onScores && <Scores summary={summary} />}
        {onRecon && <Reconciliation summary={summary} navigate={navigate} />}
        {onDashboard && <Dashboard summary={summary} caps={caps} navigate={navigate} />}
      </main>

      <footer className="mx-auto max-w-7xl px-6 pb-6 text-center text-xs text-slate-400">
        RackIQ · profile <span className="font-mono">{caps.profile}</span>
        {summary.generated_at && <> · data generated {summary.generated_at}</>}
      </footer>
    </div>
  );
}
