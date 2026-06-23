import type { HygieneOptions, InspectResponse } from "../../api/types";
import ProfilingScorecard from "./ProfilingScorecard";
import FixOptions from "./FixOptions";
import CustomerMasterPanel from "./CustomerMasterPanel";

const CUSTOMER_KEY_TABLES = new Set(["lifts", "invoices"]);

export default function CleanStep({
  inspect,
  table,
  mapping,
  options,
  onChangeOptions,
  onResolved,
  onBack,
  onValidate,
  busy,
}: {
  inspect: InspectResponse;
  table: string;
  mapping: Record<string, string>;
  options: HygieneOptions;
  onChangeOptions: (next: HygieneOptions) => void;
  onResolved: () => void;
  onBack: () => void;
  onValidate: () => void;
  busy: string | null;
}) {
  const hasCustomerKey =
    CUSTOMER_KEY_TABLES.has(table) && Object.values(mapping).includes("customer_id");

  return (
    <div className="space-y-4">
      <ProfilingScorecard inspect={inspect} />

      {hasCustomerKey && (
        <CustomerMasterPanel
          inspect={inspect}
          uploadId={inspect.upload_id}
          table={table}
          mapping={mapping}
          onResolved={onResolved}
        />
      )}

      <FixOptions table={table} mapping={mapping} options={options} onChange={onChangeOptions} />

      <div className="flex justify-between gap-2">
        <button
          onClick={onBack}
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
        >
          ← Back
        </button>
        <button
          onClick={onValidate}
          disabled={!!busy}
          className="rounded-lg bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-40"
        >
          {busy === "validate" ? "Validating…" : "Validate →"}
        </button>
      </div>
    </div>
  );
}
