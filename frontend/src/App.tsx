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
import DailyOps from "./pages/DailyOps";
import DemandCockpit from "./pages/DemandCockpit";
import Pricing from "./pages/Pricing";
import BookOverview from "./pages/BookOverview";
import Radar from "./pages/Radar";
import Scorecards from "./pages/Scorecards";
import Playbook from "./pages/Playbook";

function Centered({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-100 px-6 text-center text-slate-600">
      <div>{children}</div>
    </div>
  );
}

interface NavItem {
  key: string;
  label: string;
  icon: string;
  match: (base: string) => boolean;
  badge?: number;
}

interface NavSection {
  title: string;
  items: NavItem[];
}

function SideLink({ item, active, onClick }: { item: NavItem; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition ${
        active ? "bg-slate-900 text-white shadow-sm" : "text-slate-600 hover:bg-slate-200/60"
      }`}
    >
      <span className="w-4 text-center text-[13px]">{item.icon}</span>
      <span className="flex-1 text-left">{item.label}</span>
      {item.badge ? (
        <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${active ? "bg-white/20 text-white" : "bg-amber-500 text-white"}`}>
          {item.badge}
        </span>
      ) : null}
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
  const scorecardId = route.startsWith("scorecard/") ? route.slice("scorecard/".length) : undefined;
  const quarantine = summary.quarantine_total ?? 0;

  const sections: NavSection[] = [
    {
      title: "Operate",
      items: [
        { key: "", label: "Daily Operating", icon: "◎", match: (b) => b === "" },
        { key: "demand", label: "Demand Cockpit", icon: "↗", match: (b) => b === "demand" },
        { key: "pricing", label: "Pricing Sandbox", icon: "◇", match: (b) => b === "pricing" },
        { key: "scorecards", label: "Scorecards", icon: "▤", match: (b) => b === "scorecards" || b === "scorecard" },
        { key: "playbook", label: "Sales Playbook", icon: "✺", match: (b) => b === "playbook" },
      ],
    },
    {
      title: "Analyze",
      items: [
        { key: "overview", label: "Book Overview", icon: "☰", match: (b) => b === "overview" },
        { key: "radar", label: "Early-Warning Radar", icon: "◔", match: (b) => b === "radar" },
        { key: "scores", label: "Scores & Quadrant", icon: "✦", match: (b) => b === "scores" },
        { key: "reconciliation", label: "Reconciliation", icon: "⚖", match: (b) => b === "reconciliation" },
        { key: "capabilities", label: "Capabilities", icon: "▦", match: (b) => b === "capabilities" },
      ],
    },
    {
      title: "Data",
      items: [
        { key: "studio", label: "Data Studio", icon: "⥁", match: (b) => b === "studio" },
        { key: "health", label: "Data Health", icon: "♥", match: (b) => b === "health", badge: quarantine },
      ],
    },
  ];

  return (
    <div className="min-h-screen bg-slate-100 text-slate-900">
      <div className="mx-auto flex max-w-[1600px]">
        {/* Left nav */}
        <aside className="sticky top-0 flex h-screen w-60 shrink-0 flex-col border-r border-slate-200 bg-slate-50">
          <div className="border-b border-slate-200 px-5 py-4">
            <h1 className="text-lg font-bold tracking-tight">RackIQ</h1>
            <p className="text-[11px] text-slate-500">Demand &amp; Margin Intelligence</p>
          </div>
          <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-4">
            {sections.map((sec) => (
              <div key={sec.title}>
                <div className="px-3 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">{sec.title}</div>
                <div className="space-y-0.5">
                  {sec.items.map((item) => (
                    <SideLink key={item.key} item={item} active={item.match(base)} onClick={() => navigate(item.key)} />
                  ))}
                </div>
              </div>
            ))}
          </nav>
          <div className="border-t border-slate-200 px-4 py-3">
            <ProfileBadge profile={caps.profile} enabled={caps.summary.enabled} total={caps.summary.total} />
            <p className="mt-2 text-[10px] text-slate-400">
              {summary.connected ? `${summary.customers} customers · ${summary.lifts.toLocaleString()} lifts` : "No data — open Data Studio"}
            </p>
          </div>
        </aside>

        {/* Main content */}
        <main className="min-w-0 flex-1 px-6 py-6">
          {base === "" && <DailyOps summary={summary} navigate={navigate} />}
          {base === "demand" && <DemandCockpit summary={summary} navigate={navigate} />}
          {base === "pricing" && <Pricing summary={summary} navigate={navigate} />}
          {(base === "scorecards" || base === "scorecard") && <Scorecards summary={summary} customerId={scorecardId} />}
          {base === "playbook" && <Playbook summary={summary} />}
          {base === "overview" && <BookOverview summary={summary} navigate={navigate} />}
          {base === "radar" && <Radar summary={summary} />}
          {base === "scores" && <Scores summary={summary} />}
          {base === "reconciliation" && <Reconciliation summary={summary} navigate={navigate} />}
          {base === "capabilities" && <Dashboard summary={summary} caps={caps} navigate={navigate} />}
          {base === "studio" && <DataStudio caps={caps} summary={summary} onState={applyState} navigate={navigate} />}
          {base === "health" && <DataHealth navigate={navigate} onState={applyState} />}

          <footer className="mt-8 text-center text-xs text-slate-400">
            RackIQ · profile <span className="font-mono">{caps.profile}</span>
            {summary.generated_at && <> · data generated {summary.generated_at}</>}
          </footer>
        </main>
      </div>
    </div>
  );
}
