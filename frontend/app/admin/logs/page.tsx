"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type QueryLog } from "@/lib/api";

const STATUSES = ["success", "generated", "refused", "clarify", "execution_error", "execution_timeout", "error"];

function toCsv(logs: QueryLog[]): string {
  const header = ["id", "created_at", "status", "question", "sql", "error_reason", "row_count", "latency_ms", "trace_id"];
  const escape = (value: unknown) => `"${String(value ?? "").replace(/"/g, '""')}"`;
  return [header.join(","), ...logs.map((log) => header.map((key) => escape(log[key as keyof QueryLog])).join(","))].join("\n");
}

export default function QueryLogsPage() {
  const [logs, setLogs] = useState<QueryLog[]>([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLogs(await api.logs(statusFilter || undefined));
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Failed to load logs.");
    }
  }, [statusFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  const visible = text
    ? logs.filter((log) => `${log.question} ${log.sql ?? ""}`.toLowerCase().includes(text.toLowerCase()))
    : logs;

  function exportCsv() {
    const blob = new Blob([toCsv(visible)], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "querymind-logs.csv";
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div>
      <h2>Query Logs</h2>
      <p className="muted">
        Logs include the user&apos;s question text and generated SQL. They are stored unencrypted in the local admin
        database.
      </p>
      {error && <div className="error">{error}</div>}
      <div className="card">
        <div className="row">
          <select style={{ maxWidth: 200 }} value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">All statuses</option>
            {STATUSES.map((status) => (
              <option key={status}>{status}</option>
            ))}
          </select>
          <input
            style={{ maxWidth: 300 }}
            placeholder="Filter question or SQL"
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
          <button className="secondary" onClick={() => void load()}>
            Refresh
          </button>
          <button onClick={exportCsv} disabled={visible.length === 0}>
            Export CSV
          </button>
        </div>
        <table>
          <thead>
            <tr>
              <th>Time (UTC)</th>
              <th>Status</th>
              <th>Question</th>
              <th>SQL</th>
              <th>Rows</th>
              <th>Latency (ms)</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((log) => (
              <tr key={log.id}>
                <td>{log.created_at}</td>
                <td>
                  <span className={`badge ${log.status === "success" || log.status === "generated" ? "good" : "bad"}`}>
                    {log.status}
                  </span>
                </td>
                <td>{log.question}</td>
                <td>{log.sql ? <pre>{log.sql}</pre> : "—"}</td>
                <td>{log.row_count ?? "—"}</td>
                <td>{log.latency_ms ?? "—"}</td>
                <td className="muted">{log.error_reason ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
