"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";

export default function GuardrailsConfigPage() {
  const [maxRows, setMaxRows] = useState(100);
  const [prefixes, setPrefixes] = useState("");
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  useEffect(() => {
    api
      .guardrails()
      .then((config) => {
        setMaxRows(config.max_rows);
        setPrefixes(config.allowed_table_prefixes.join(", "));
      })
      .catch((caught) =>
        setMessage({ kind: "error", text: caught instanceof ApiError ? caught.message : "Failed to load guardrails." }),
      );
  }, []);

  async function save() {
    try {
      await api.saveGuardrails({
        max_rows: maxRows,
        allow_write_operations: false,
        allowed_table_prefixes: prefixes.split(",").map((value) => value.trim()).filter(Boolean),
      });
      setMessage({ kind: "ok", text: "Guardrails saved." });
    } catch (caught) {
      setMessage({ kind: "error", text: caught instanceof ApiError ? caught.message : "Save failed." });
    }
  }

  return (
    <div>
      <h2>Guardrails Config</h2>
      {message && <div className={message.kind === "ok" ? "ok" : "error"}>{message.text}</div>}
      <div className="card">
        <label htmlFor="max-rows">Maximum rows returned (1–1000)</label>
        <input
          id="max-rows"
          type="number"
          min={1}
          max={1000}
          value={maxRows}
          onChange={(event) => setMaxRows(Number(event.target.value))}
        />
        <label htmlFor="prefixes">Allowed table prefixes (comma-separated; empty = all tables)</label>
        <input id="prefixes" value={prefixes} onChange={(event) => setPrefixes(event.target.value)} />

        <label>Destructive SQL operations (DROP, DELETE, UPDATE, INSERT, TRUNCATE, ALTER)</label>
        <div className="muted">
          Always blocked. Read-only enforcement is a fixed safety boundary and cannot be toggled from this panel.
        </div>

        <div style={{ marginTop: 12 }}>
          <button onClick={() => void save()}>Save</button>
        </div>
      </div>
    </div>
  );
}
