import { useEffect, useState } from "react";
import { api } from "../api/client";
import type {
  Summary,
  Capabilities,
  InspectResponse,
  ValidateResponse,
  CommitResponse,
  SavedProfile,
  StudioState,
} from "../api/types";
import Panel from "../components/Panel";
import DataCapabilityPanel from "../components/DataCapabilityPanel";
import Stepper from "../components/studio/Stepper";
import UploadStep from "../components/studio/UploadStep";
import MappingStep from "../components/studio/MappingStep";
import ValidateStep from "../components/studio/ValidateStep";
import DoneStep from "../components/studio/DoneStep";

type Step = "upload" | "map" | "validate" | "done";

function mappingFromSuggestions(inspect: InspectResponse, table: string): Record<string, string> {
  const out: Record<string, string> = {};
  const sugg = inspect.suggestions_by_table[table] ?? {};
  for (const [col, s] of Object.entries(sugg)) out[col] = s.target;
  return out;
}

export default function DataStudio({
  caps,
  summary,
  onState,
  navigate,
}: {
  caps: Capabilities;
  summary: Summary;
  onState: (s: StudioState) => void;
  navigate: (to: string) => void;
}) {
  const [step, setStep] = useState<Step>("upload");
  const [inspect, setInspect] = useState<InspectResponse | null>(null);
  const [table, setTable] = useState<string>("lifts");
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [saveProfileName, setSaveProfileName] = useState("");
  const [validation, setValidation] = useState<ValidateResponse | null>(null);
  const [mode, setMode] = useState("replace");
  const [result, setResult] = useState<CommitResponse | null>(null);
  const [profiles, setProfiles] = useState<SavedProfile[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  function loadProfiles() {
    api.studio.profiles().then((d) => setProfiles(d.profiles)).catch(() => {});
  }
  useEffect(loadProfiles, []);

  function resetWizard() {
    setStep("upload");
    setInspect(null);
    setMapping({});
    setValidation(null);
    setResult(null);
    setSaveProfileName("");
    setError(null);
  }

  async function handleFile(file: File) {
    setBusy("inspect");
    setError(null);
    setNotice(null);
    try {
      const data = await api.studio.inspect(file);
      setInspect(data);
      const t = data.matched_profile?.target_table ?? data.suggested_table;
      setTable(t);
      setMapping(data.matched_profile?.mapping ?? mappingFromSuggestions(data, t));
      if (data.matched_profile) setNotice(`Applied saved profile “${data.matched_profile.name}”.`);
      setStep("map");
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(null);
    }
  }

  function changeTable(t: string) {
    setTable(t);
    if (inspect) setMapping(mappingFromSuggestions(inspect, t));
  }

  function changeMapping(source: string, target: string) {
    setMapping((prev) => ({ ...prev, [source]: target }));
  }

  async function handleValidate() {
    if (!inspect) return;
    setBusy("validate");
    setError(null);
    try {
      const v = await api.studio.validate({ upload_id: inspect.upload_id, table, mapping });
      setValidation(v);
      setStep("validate");
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(null);
    }
  }

  async function handleCommit() {
    if (!inspect) return;
    setBusy("commit");
    setError(null);
    try {
      const r = await api.studio.commit({
        upload_id: inspect.upload_id,
        table,
        mapping,
        mode,
        save_profile: saveProfileName.trim() || null,
      });
      setResult(r);
      onState({ summary: r.summary, capabilities: r.capabilities });
      loadProfiles();
      setStep("done");
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(null);
    }
  }

  async function handleLoadDemo(profile: string) {
    setBusy("demo");
    setError(null);
    try {
      const r = await api.studio.loadDemo(profile);
      onState({ summary: r.summary, capabilities: r.capabilities });
      resetWizard();
      setNotice(`Loaded the “${profile}” demo book — ${r.capabilities.summary.enabled}/${r.capabilities.summary.total} capabilities enabled.`);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(null);
    }
  }

  async function handleReset() {
    setBusy("reset");
    setError(null);
    try {
      const r = await api.studio.reset();
      onState({ summary: r.summary, capabilities: r.capabilities });
      resetWizard();
      setNotice("Cleared the store — feed RackIQ a file to begin.");
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(null);
    }
  }

  async function handleDeleteProfile(name: string) {
    await api.studio.deleteProfile(name).catch(() => {});
    loadProfiles();
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold tracking-tight text-slate-900">Data Studio</h1>
          <p className="text-xs text-slate-500">
            The front door for feeding RackIQ — upload, map, validate, and commit your book.
          </p>
        </div>
        <Stepper current={step} />
      </div>

      {notice && (
        <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-xs text-blue-700">{notice}</div>
      )}
      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700">{error}</div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <section className="lg:col-span-2">
          <Panel>
            {step === "upload" && (
              <UploadStep
                onFile={handleFile}
                onLoadDemo={handleLoadDemo}
                onReset={handleReset}
                profiles={profiles}
                onDeleteProfile={handleDeleteProfile}
                busy={busy}
              />
            )}
            {step === "map" && inspect && (
              <MappingStep
                inspect={inspect}
                table={table}
                mapping={mapping}
                onChangeTable={changeTable}
                onChangeMapping={changeMapping}
                saveProfileName={saveProfileName}
                onChangeSaveProfileName={setSaveProfileName}
                onBack={() => setStep("upload")}
                onValidate={handleValidate}
                busy={busy}
              />
            )}
            {step === "validate" && validation && (
              <ValidateStep
                validation={validation}
                mode={mode}
                onChangeMode={setMode}
                onBack={() => setStep("map")}
                onCommit={handleCommit}
                busy={busy}
              />
            )}
            {step === "done" && result && (
              <DoneStep
                result={result}
                onImportAnother={resetWizard}
                onGoDashboard={() => navigate("")}
              />
            )}
          </Panel>
        </section>

        <section className="space-y-4">
          <Panel>
            <DataCapabilityPanel caps={caps} />
          </Panel>
          {summary.last_import?.filename && (
            <Panel title="Last import">
              <div className="text-xs text-slate-600">
                <div className="font-medium text-slate-700">{summary.last_import.filename}</div>
                <div className="mt-0.5 text-slate-400">
                  → {summary.last_import.table} · {summary.last_import.at?.replace("T", " ")}
                </div>
              </div>
            </Panel>
          )}
        </section>
      </div>
    </div>
  );
}
