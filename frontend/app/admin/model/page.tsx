"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type DatabaseConnection } from "@/lib/api";

export default function ModelConfigPage() {
  const [provider, setProvider] = useState("groq");
  const [transpile, setTranspile] = useState("");
  const [databaseDialect, setDatabaseDialect] = useState("sqlite");
  const [defaultModels, setDefaultModels] = useState<Record<string, string>>({});
  const [model, setModel] = useState("");
  const [guardrailModel, setGuardrailModel] = useState("");
  const [temperature, setTemperature] = useState(0);
  const [maxTokens, setMaxTokens] = useState("");
  const [databases, setDatabases] = useState<DatabaseConnection[]>([]);
  const [newDb, setNewDb] = useState({ name: "", dialect: "sqlite", connection_url: "" });
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const loadDatabases = useCallback(async () => {
    try {
      setDatabases(await api.databases());
    } catch (caught) {
      setMessage({ kind: "error", text: caught instanceof ApiError ? caught.message : "Failed to load connections." });
    }
  }, []);

  useEffect(() => {
    api
      .modelConfig()
      .then((config) => {
        setModel(config.model ?? "");
        setProvider(config.provider);
        setTranspile(config.transpile_dialect ?? "");
        setDatabaseDialect(config.database_dialect ?? "sqlite");
        setDefaultModels(config.default_models);
        setGuardrailModel(config.guardrail_model ?? "");
        setTemperature(config.temperature);
        setMaxTokens(config.max_tokens?.toString() ?? "");
      })
      .catch((caught) =>
        setMessage({ kind: "error", text: caught instanceof ApiError ? caught.message : "Failed to load model config." }),
      );
    void loadDatabases();
  }, [loadDatabases]);

  async function saveModel() {
    try {
      await api.saveModelConfig({
        provider,
        model: model.trim() || null,
        guardrail_model: guardrailModel.trim() || null,
        temperature,
        max_tokens: maxTokens ? Number(maxTokens) : null,
        transpile_dialect: transpile || null,
      });
      setMessage({ kind: "ok", text: "Model configuration saved." });
    } catch (caught) {
      setMessage({ kind: "error", text: caught instanceof ApiError ? caught.message : "Save failed." });
    }
  }

  async function addDatabase() {
    try {
      await api.createDatabase(newDb);
      setNewDb({ name: "", dialect: "sqlite", connection_url: "" });
      setMessage({ kind: "ok", text: "Connection added." });
      await loadDatabases();
    } catch (caught) {
      setMessage({ kind: "error", text: caught instanceof ApiError ? caught.message : "Could not add connection." });
    }
  }

  return (
    <div>
      <h2>Model Config</h2>
      {message && <div className={message.kind === "ok" ? "ok" : "error"}>{message.text}</div>}

      <div className="card">
        <h3>LLM settings</h3>
        <label htmlFor="provider">Provider</label>
        <select id="provider" value={provider} onChange={(event) => setProvider(event.target.value)}>
          <option value="groq">Groq</option>
          <option value="openai">OpenAI</option>
          <option value="anthropic">Anthropic</option>
          <option value="ollama">Ollama (local)</option>
        </select>
        <div className="muted">
          Keys come from the server .env (GROQ_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY; Ollama uses
          OLLAMA_BASE_URL). The safety judge always runs on Groq.
        </div>
        <label htmlFor="model">
          Generation model (blank = {defaultModels[provider] ?? "provider default"})
        </label>
        <input id="model" value={model} onChange={(event) => setModel(event.target.value)} />
        <label htmlFor="guardrail-model">Safety judge model (blank = environment default)</label>
        <input id="guardrail-model" value={guardrailModel} onChange={(event) => setGuardrailModel(event.target.value)} />
        <label htmlFor="temperature">Temperature (0–2)</label>
        <input
          id="temperature"
          type="number"
          step="0.1"
          min={0}
          max={2}
          value={temperature}
          onChange={(event) => setTemperature(Number(event.target.value))}
        />
        <label htmlFor="max-tokens">Max completion tokens (blank = provider default)</label>
        <input id="max-tokens" type="number" min={1} value={maxTokens} onChange={(event) => setMaxTokens(event.target.value)} />
        <label htmlFor="transpile">Also show SQL transpiled to</label>
        <select id="transpile" value={transpile} onChange={(event) => setTranspile(event.target.value)}>
          <option value="">None</option>
          <option value="sqlite">SQLite</option>
          <option value="postgresql">PostgreSQL</option>
          <option value="mysql">MySQL</option>
        </select>
        <div className="muted">
          SQL is generated and executed in the active database&apos;s dialect ({databaseDialect}). This option only
          adds a sqlglot-rewritten copy for another dialect.
        </div>
        <div style={{ marginTop: 12 }}>
          <button onClick={() => void saveModel()}>Save</button>
        </div>
      </div>

      <div className="card">
        <h3>Database connections</h3>
        <p className="muted">
          The active connection is used for all queries. With none active, the server&apos;s environment default
          applies. SQLite and PostgreSQL are supported; connection URLs are stored unencrypted locally.
        </p>
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Dialect</th>
              <th>URL</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {databases.map((database) => (
              <tr key={database.id}>
                <td>{database.name}</td>
                <td>{database.dialect}</td>
                <td className="muted">{database.connection_url}</td>
                <td>{database.is_active ? <span className="badge good">active</span> : ""}</td>
                <td>
                  <div className="row">
                    {!database.is_active && (
                      <button
                        className="secondary"
                        onClick={async () => {
                          await api.activateDatabase(database.id);
                          await loadDatabases();
                        }}
                      >
                        Activate
                      </button>
                    )}
                    <button
                      className="danger"
                      onClick={async () => {
                        if (!window.confirm(`Remove connection "${database.name}"?`)) return;
                        await api.deleteDatabase(database.id);
                        await loadDatabases();
                      }}
                    >
                      Remove
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <h4>Add connection</h4>
        <label htmlFor="db-name">Name</label>
        <input id="db-name" value={newDb.name} onChange={(event) => setNewDb({ ...newDb, name: event.target.value })} />
        <label htmlFor="db-dialect">Dialect</label>
        <select
          id="db-dialect"
          value={newDb.dialect}
          onChange={(event) => setNewDb({ ...newDb, dialect: event.target.value })}
        >
          <option value="sqlite">sqlite</option>
          <option value="postgresql">postgresql</option>
          <option value="mysql">mysql (no driver installed)</option>
        </select>
        <label htmlFor="db-url">Connection URL</label>
        <input
          id="db-url"
          value={newDb.connection_url}
          placeholder="postgresql+psycopg://user:password@host:5432/dbname"
          onChange={(event) => setNewDb({ ...newDb, connection_url: event.target.value })}
        />
        <div style={{ marginTop: 12 }}>
          <button onClick={() => void addDatabase()}>Test &amp; add</button>
        </div>
      </div>
    </div>
  );
}
