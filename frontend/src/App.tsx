import { useEffect, useState, type ReactNode } from "react";
import { api } from "./api/client";
import type {
  Summary,
  Capabilities,
  CustomersResponse,
  MonthlyVolume,
  MarketPrices,
} from "./api/types";
import ConnectionBanner from "./components/ConnectionBanner";
import ProfileBadge from "./components/ProfileBadge";
import CapabilityGrid from "./components/CapabilityGrid";
import VolumeChart from "./components/VolumeChart";
import MarketPriceChart from "./components/MarketPriceChart";

function Centered({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-100 px-6 text-center text-slate-600">
      <div>{children}</div>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="mb-3 text-sm font-semibold text-slate-700">{title}</h2>
      {children}
    </div>
  );
}

function TopCustomers({ data }: { data: CustomersResponse }) {
  const rows = data.customers.slice(0, 8);
  return (
    <Panel title="Top Customers by Volume">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-slate-400">
              <th className="pb-2">Customer</th>
              <th className="pb-2">Archetype</th>
              <th className="pb-2 text-right">MM gal</th>
              <th className="pb-2 text-right">Lifts</th>
              {data.margin_enabled && <th className="pb-2 text-right">¢/gal</th>}
              {data.dso_enabled && <th className="pb-2 text-right">DSO</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.customer_id} className="border-t border-slate-100">
                <td className="py-1.5 font-medium text-slate-700">{c.name}</td>
                <td className="py-1.5 text-slate-500">{c.archetype.replace(/_/g, " ")}</td>
                <td className="py-1.5 text-right">{(c.total_net_gallons / 1e6).toFixed(2)}</td>
                <td className="py-1.5 text-right">{c.lift_count}</td>
                {data.margin_enabled && (
                  <td className="py-1.5 text-right">
                    {c.avg_margin_per_gal != null ? (c.avg_margin_per_gal * 100).toFixed(2) : "—"}
                  </td>
                )}
                {data.dso_enabled && (
                  <td className="py-1.5 text-right">{c.dso_days ?? "—"}</td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

export default function App() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [customers, setCustomers] = useState<CustomersResponse | null>(null);
  const [volume, setVolume] = useState<MonthlyVolume | null>(null);
  const [market, setMarket] = useState<MarketPrices | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.summary(),
      api.capabilities(),
      api.customers(),
      api.monthlyVolume(),
      api.marketPrices(),
    ])
      .then(([s, c, cu, v, m]) => {
        setSummary(s);
        setCaps(c);
        setCustomers(cu);
        setVolume(v);
        setMarket(m);
      })
      .catch((e) => setError(String(e)));
  }, []);

  function selectProduct(p: string) {
    api.marketPrices(p).then(setMarket).catch((e) => setError(String(e)));
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

  return (
    <div className="min-h-screen bg-slate-100 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
          <div>
            <h1 className="text-lg font-bold tracking-tight">RackIQ</h1>
            <p className="text-xs text-slate-500">Customer Demand &amp; Margin Intelligence</p>
          </div>
          <ProfileBadge
            profile={caps.profile}
            enabled={caps.summary.enabled}
            total={caps.summary.total}
          />
        </div>
      </header>

      <main className="mx-auto max-w-7xl space-y-6 px-6 py-6">
        <ConnectionBanner summary={summary} />

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          <section className="space-y-6 lg:col-span-2">
            <Panel title="Monthly Net Volume (winter distillate spikes)">
              {volume && <VolumeChart points={volume.points} />}
            </Panel>
            <Panel title="Market Prices — benchmark vs posted street rack">
              {market && <MarketPriceChart data={market} onSelect={selectProduct} />}
            </Panel>
            {customers && <TopCustomers data={customers} />}
          </section>

          <section>
            <Panel title={`Capability Matrix · ${caps.summary.enabled}/${caps.summary.total} enabled`}>
              <p className="mb-3 text-xs text-slate-500">
                Features light up based on which canonical fields are present in the loaded data.
              </p>
              <CapabilityGrid caps={caps} />
            </Panel>
          </section>
        </div>

        <footer className="pb-4 text-center text-xs text-slate-400">
          RackIQ · profile <span className="font-mono">{caps.profile}</span> · data generated{" "}
          {summary.generated_at ?? "—"}
        </footer>
      </main>
    </div>
  );
}
