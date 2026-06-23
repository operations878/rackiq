import { useEffect, useState } from "react";
import { api } from "../api/client";
import type {
  Summary,
  Capabilities,
  CustomersResponse,
  MonthlyVolume,
  MarketPrices,
} from "../api/types";
import ConnectionBanner from "../components/ConnectionBanner";
import CapabilityGrid from "../components/CapabilityGrid";
import VolumeChart from "../components/VolumeChart";
import MarketPriceChart from "../components/MarketPriceChart";
import Panel from "../components/Panel";

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
                {data.dso_enabled && <td className="py-1.5 text-right">{c.dso_days ?? "—"}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function EmptyState({ navigate }: { navigate: (to: string) => void }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center shadow-sm">
      <div className="text-3xl">🛢️</div>
      <h2 className="mt-3 text-lg font-semibold text-slate-800">No data loaded yet</h2>
      <p className="mx-auto mt-1 max-w-md text-sm text-slate-500">
        RackIQ's capabilities flex with the data you provide. Head to the Data Studio to upload a
        CSV or Excel file — or load the synthetic Soundview book to explore.
      </p>
      <button
        onClick={() => navigate("studio")}
        className="mt-5 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
      >
        Open Data Studio →
      </button>
    </div>
  );
}

export default function Dashboard({
  summary,
  caps,
  navigate,
}: {
  summary: Summary;
  caps: Capabilities;
  navigate: (to: string) => void;
}) {
  const [customers, setCustomers] = useState<CustomersResponse | null>(null);
  const [volume, setVolume] = useState<MonthlyVolume | null>(null);
  const [market, setMarket] = useState<MarketPrices | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!summary.connected) return;
    Promise.all([api.customers(), api.monthlyVolume(), api.marketPrices()])
      .then(([cu, v, m]) => {
        setCustomers(cu);
        setVolume(v);
        setMarket(m);
      })
      .catch((e) => setError(String(e)));
  }, [summary.connected]);

  function selectProduct(p: string) {
    api.marketPrices(p).then(setMarket).catch((e) => setError(String(e)));
  }

  if (!summary.connected) return <EmptyState navigate={navigate} />;

  return (
    <div className="space-y-6">
      <ConnectionBanner summary={summary} />
      {error && <div className="rounded-lg bg-red-50 p-3 text-xs text-red-700">{error}</div>}

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
    </div>
  );
}
