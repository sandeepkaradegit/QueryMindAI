"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, type ScriptDescriptor, type ScriptRunResult } from "@/lib/api";

const CATEGORY_ORDER: Record<ScriptDescriptor["category"], number> = {
  database: 0,
  evaluation: 1,
  validation: 2,
};

type FlowStep = {
  id: string;
  title: string;
  detail: string;
  scriptId: string;
  optionId?: string;
};

type MigrationChecklistItem = {
  id: string;
  title: string;
  detail: string;
  action: string;
};

const NEW_DB_MIGRATION_CHECKLIST: MigrationChecklistItem[] = [
  {
    id: "check-schema",
    title: "Replace schema + seeder",
    detail: "Create your new database schema and update scripts/seed_db.py to populate representative data.",
    action: "Run seed-db only after schema/seeder updates are complete.",
  },
  {
    id: "check-examples",
    title: "Regenerate examples library",
    detail: "Rewrite examples/question_sql_examples.json to match new table and column names.",
    action: "Use /admin/examples to review and correct imported examples.",
  },
  {
    id: "check-evals",
    title: "Regenerate evaluation dataset",
    detail: "Rewrite evaluations/sql_generation_v1.json expected SQL for the new domain.",
    action: "Then run run-evaluation (offline first, live optional).",
  },
  {
    id: "check-validators",
    title: "Adapt demo-specific validators",
    detail: "Most validate_*.py scripts assert demo-table names or seeded counts and must be updated.",
    action: "Use compatibility badges below to identify demo-only scripts quickly.",
  },
  {
    id: "check-generic",
    title: "Keep generic validators",
    detail: "Generic scripts can be reused without schema rewrite.",
    action: "Run generic scripts first to verify infra wiring before domain-specific checks.",
  },
];

function defaultOptionMap(scripts: ScriptDescriptor[]): Record<string, string> {
  const selected: Record<string, string> = {};
  for (const script of scripts) {
    if (script.options.length > 0) {
      selected[script.id] = script.options[0].id;
    }
  }
  return selected;
}

function asJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

export default function ScriptRunnerPage() {
  const [scripts, setScripts] = useState<ScriptDescriptor[]>([]);
  const [selectedOptions, setSelectedOptions] = useState<Record<string, string>>({});
  const [busyScriptId, setBusyScriptId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<ScriptRunResult | null>(null);
  const [runHistory, setRunHistory] = useState<Record<string, ScriptRunResult>>({});

  const loadScripts = useCallback(async () => {
    try {
      const loaded = await api.scripts();
      loaded.sort((left, right) => {
        const categoryGap = CATEGORY_ORDER[left.category] - CATEGORY_ORDER[right.category];
        return categoryGap !== 0 ? categoryGap : left.name.localeCompare(right.name);
      });
      setScripts(loaded);
      setSelectedOptions(defaultOptionMap(loaded));
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Failed to load scripts.");
    }
  }, []);

  useEffect(() => {
    void loadScripts();
  }, [loadScripts]);

  const scriptsByCategory = useMemo(() => {
    return {
      database: scripts.filter((script) => script.category === "database"),
      evaluation: scripts.filter((script) => script.category === "evaluation"),
      validation: scripts.filter((script) => script.category === "validation"),
    };
  }, [scripts]);

  const newDbFlowSteps = useMemo(() => {
    const explicitSteps = scripts
      .filter(
        (script) =>
          script.new_db_flow_order !== null &&
          script.new_db_flow_title !== null &&
          script.new_db_flow_detail !== null,
      )
      .map((script) => ({
        id: `flow-${script.id}-${script.new_db_flow_option_id ?? "default"}`,
        title: script.new_db_flow_title ?? script.name,
        detail: script.new_db_flow_detail ?? script.description,
        scriptId: script.id,
        optionId: script.new_db_flow_option_id ?? undefined,
        order: script.new_db_flow_order ?? 0,
      }))
      .sort((left, right) => left.order - right.order)
      .map(({ order: _order, ...step }) => step);

    const runEval = scripts.find((script) => script.id === "run-evaluation");
    if (!runEval || !runEval.options.some((option) => option.id === "live")) {
      return explicitSteps;
    }
    const hasLiveStep = explicitSteps.some((step) => step.scriptId === "run-evaluation" && step.optionId === "live");
    if (hasLiveStep) {
      return explicitSteps;
    }
    return [
      ...explicitSteps,
      {
        id: "flow-run-evaluation-live",
        title: "6) Run live evaluation (optional)",
        detail: "Uses configured model credentials for real accuracy measurement.",
        scriptId: "run-evaluation",
        optionId: "live",
      },
    ];
  }, [scripts]);

  const compatibilityCounts = useMemo(() => {
    return {
      generic: scripts.filter((script) => script.compatibility === "generic").length,
      demoOnly: scripts.filter((script) => script.compatibility === "demo_only").length,
    };
  }, [scripts]);

  async function run(script: ScriptDescriptor, forcedOptionId?: string) {
    setBusyScriptId(script.id);
    setError(null);
    try {
      const optionId = forcedOptionId ?? (script.options.length > 0 ? selectedOptions[script.id] : undefined);
      const result = await api.runScript(optionId ? { script_id: script.id, option_id: optionId } : { script_id: script.id });
      setLastRun(result);
      setRunHistory((previous) => ({ ...previous, [`${result.script_id}:${result.option_id ?? ""}`]: result }));
      if (!result.succeeded) {
        setError(`Script failed with exit code ${result.exit_code}.`);
      }
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Failed to run script.");
    } finally {
      setBusyScriptId(null);
    }
  }

  function renderCategory(title: string, items: ScriptDescriptor[]) {
    return (
      <div className="card">
        <h3>{title}</h3>
        {items.length === 0 ? (
          <p className="muted">No scripts available in this category.</p>
        ) : (
          <table>
          <thead>
            <tr>
              <th>Script</th>
              <th>Description</th>
              <th>Compatibility</th>
              <th>Option</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((script) => {
              const running = busyScriptId === script.id;
              return (
                <tr key={script.id}>
                  <td>{script.name}</td>
                  <td className="muted">{script.description}</td>
                  <td style={{ minWidth: 210 }}>
                    <span className={`badge ${script.compatibility === "generic" ? "good" : "bad"}`}>
                      {script.compatibility === "generic" ? "generic" : "current-demo-only"}
                    </span>
                    <div className="muted" style={{ marginTop: 6 }}>
                      {script.compatibility_note}
                    </div>
                  </td>
                  <td style={{ minWidth: 260 }}>
                    {script.options.length === 0 ? (
                      <span className="muted">Default</span>
                    ) : (
                      <select
                        value={selectedOptions[script.id] ?? script.options[0].id}
                        onChange={(event) =>
                          setSelectedOptions((previous) => ({ ...previous, [script.id]: event.target.value }))
                        }
                        disabled={running}
                      >
                        {script.options.map((option) => (
                          <option key={option.id} value={option.id}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    )}
                    {script.options.length > 0 && (
                      <div className="muted" style={{ marginTop: 6 }}>
                        {script.options.find((option) => option.id === selectedOptions[script.id])?.description ?? ""}
                      </div>
                    )}
                  </td>
                  <td>
                    <button disabled={busyScriptId !== null} onClick={() => void run(script)}>
                      {running ? "Running..." : "Run"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        )}
      </div>
    );
  }

  const scriptsById = useMemo(() => {
    return Object.fromEntries(scripts.map((script) => [script.id, script]));
  }, [scripts]);

  function renderFlow() {
    return (
      <div className="card">
        <h3>New DB Setup Flow</h3>
        <p className="muted">Recommended execution sequence to validate a newly configured database.</p>
        <div className="flow-diagram">
          {newDbFlowSteps.map((step, index) => {
            const script = scriptsById[step.scriptId];
            const historyKey = `${step.scriptId}:${step.optionId ?? ""}`;
            const stepResult = runHistory[historyKey];
            return (
              <div key={step.id}>
                <div className="flow-step">
                  <div className="flow-header">
                    <strong>{step.title}</strong>
                    {stepResult && (
                      <span className={`badge ${stepResult.succeeded ? "good" : "bad"}`}>
                        {stepResult.succeeded ? "passed" : "failed"}
                      </span>
                    )}
                  </div>
                  <div className="muted">{step.detail}</div>
                  <div className="flow-actions">
                    {script ? (
                      <button disabled={busyScriptId !== null} onClick={() => void run(script, step.optionId)}>
                        {busyScriptId === script.id ? "Running..." : "Run this step"}
                      </button>
                    ) : (
                      <span className="badge bad">script unavailable</span>
                    )}
                    <span className="muted">
                      {step.scriptId}
                      {step.optionId ? ` (${step.optionId})` : ""}
                    </span>
                    {script && (
                      <span className={`badge ${script.compatibility === "generic" ? "good" : "bad"}`}>
                        {script.compatibility === "generic" ? "generic" : "current-demo-only"}
                      </span>
                    )}
                  </div>
                  <details style={{ marginTop: 10 }}>
                    <summary className="muted">Show input / output</summary>
                    <label>Input sent to backend (/admin/scripts/run)</label>
                    <pre>
                      {asJson(
                        step.optionId
                          ? { script_id: step.scriptId, option_id: step.optionId }
                          : { script_id: step.scriptId },
                      )}
                    </pre>
                    <label>Output returned</label>
                    {stepResult ? (
                      <>
                        <pre>
                          {asJson({
                            succeeded: stepResult.succeeded,
                            exit_code: stepResult.exit_code,
                            duration_ms: stepResult.duration_ms,
                            command: stepResult.command,
                            stdout_truncated: stepResult.stdout_truncated,
                            stderr_truncated: stepResult.stderr_truncated,
                          })}
                        </pre>
                        <label>stdout</label>
                        <pre>{stepResult.stdout || "(empty)"}</pre>
                        <label>stderr</label>
                        <pre>{stepResult.stderr || "(empty)"}</pre>
                      </>
                    ) : (
                      <pre>(No output yet. Run this step first.)</pre>
                    )}
                  </details>
                </div>
                {index < newDbFlowSteps.length - 1 && <div className="flow-arrow">↓</div>}
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  function renderMigrationChecklist() {
    return (
      <div className="card">
        <h3>New DB Migration Checklist</h3>
        <p className="muted">
          Use this checklist when moving from the built-in demo schema to a new database domain.
        </p>
        <p className="muted">
          Script compatibility summary:{" "}
          <span className="badge good">generic: {compatibilityCounts.generic}</span>{" "}
          <span className="badge bad">current-demo-only: {compatibilityCounts.demoOnly}</span>
        </p>
        <div className="flow-diagram">
          {NEW_DB_MIGRATION_CHECKLIST.map((item) => (
            <div key={item.id} className="flow-step">
              <div className="flow-header">
                <strong>{item.title}</strong>
              </div>
              <div className="muted">{item.detail}</div>
              <div style={{ marginTop: 8 }}>{item.action}</div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div>
      <h2>Script Runner</h2>
      <p className="muted">
        Run seed, evaluation, and validation scripts from the admin UI. Commands are allowlisted server-side; arbitrary shell
        execution is not supported.
      </p>
      {error && <div className="error">{error}</div>}
      <div className="row" style={{ marginBottom: 12 }}>
        <button className="secondary" onClick={() => void loadScripts()} disabled={busyScriptId !== null}>
          Refresh scripts
        </button>
        <span className="muted">Total scripts loaded: {scripts.length}</span>
      </div>
      {renderMigrationChecklist()}
      {renderFlow()}
      {renderCategory("Database", scriptsByCategory.database)}
      {renderCategory("Evaluation", scriptsByCategory.evaluation)}
      {renderCategory("Validation", scriptsByCategory.validation)}

      {lastRun && (
        <div className="card">
          <h3>Last run result</h3>
          <p>
            <span className={`badge ${lastRun.succeeded ? "good" : "bad"}`}>
              {lastRun.succeeded ? "success" : "failed"}
            </span>{" "}
            exit code {lastRun.exit_code} in {lastRun.duration_ms} ms
          </p>
          <label>Command</label>
          <pre>{lastRun.command.join(" ")}</pre>
          <label>Standard output {lastRun.stdout_truncated ? "(truncated)" : ""}</label>
          <pre>{lastRun.stdout || "(empty)"}</pre>
          <label>Standard error {lastRun.stderr_truncated ? "(truncated)" : ""}</label>
          <pre>{lastRun.stderr || "(empty)"}</pre>
        </div>
      )}
    </div>
  );
}
