"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type Example } from "@/lib/api";

const QUERY_TYPES = ["SELECT_SIMPLE", "SELECT_AGGREGATE", "SELECT_JOIN", "SELECT_TEMPORAL"];
const EMPTY = { id: "", question: "", sql: "", query_type: "SELECT_SIMPLE" };

export default function ExamplesManagerPage() {
  const [examples, setExamples] = useState<Example[]>([]);
  const [search, setSearch] = useState("");
  const [semantic, setSemantic] = useState(true);
  const [typeFilter, setTypeFilter] = useState("");
  const [form, setForm] = useState(EMPTY);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      setExamples(await api.examples(search || undefined, semantic));
    } catch (caught) {
      setMessage({ kind: "error", text: caught instanceof ApiError ? caught.message : "Failed to load examples." });
    }
  }, [search, semantic]);

  useEffect(() => {
    void load();
  }, [load]);

  async function submit() {
    try {
      if (editingId) {
        await api.updateExample(editingId, { question: form.question, sql: form.sql, query_type: form.query_type });
      } else {
        await api.createExample(form);
      }
      setMessage({ kind: "ok", text: editingId ? "Example updated." : "Example added." });
      setForm(EMPTY);
      setEditingId(null);
      await load();
    } catch (caught) {
      setMessage({ kind: "error", text: caught instanceof ApiError ? caught.message : "Save failed." });
    }
  }

  async function remove(id: string) {
    if (!window.confirm(`Delete example "${id}"?`)) return;
    try {
      await api.deleteExample(id);
      setMessage({ kind: "ok", text: "Example deleted." });
      await load();
    } catch (caught) {
      setMessage({ kind: "error", text: caught instanceof ApiError ? caught.message : "Delete failed." });
    }
  }

  const visible = typeFilter ? examples.filter((example) => example.query_type === typeFilter) : examples;

  return (
    <div>
      <h2>Examples Manager</h2>
      <p className="muted">
        Curated question→SQL pairs used as few-shot context. Search is lexical (substring match), not semantic.
      </p>
      {message && <div className={message.kind === "ok" ? "ok" : "error"}>{message.text}</div>}

      <div className="card">
        <h3>{editingId ? `Edit ${editingId}` : "Add example"}</h3>
        <label htmlFor="example-id">ID</label>
        <input
          id="example-id"
          value={form.id}
          disabled={editingId !== null}
          onChange={(event) => setForm({ ...form, id: event.target.value })}
        />
        <label htmlFor="example-question">Question</label>
        <input
          id="example-question"
          value={form.question}
          onChange={(event) => setForm({ ...form, question: event.target.value })}
        />
        <label htmlFor="example-sql">SQL</label>
        <textarea id="example-sql" value={form.sql} onChange={(event) => setForm({ ...form, sql: event.target.value })} />
        <label htmlFor="example-type">Query type</label>
        <select
          id="example-type"
          value={form.query_type}
          onChange={(event) => setForm({ ...form, query_type: event.target.value })}
        >
          {QUERY_TYPES.map((type) => (
            <option key={type}>{type}</option>
          ))}
        </select>
        <div className="row" style={{ marginTop: 12 }}>
          <button onClick={() => void submit()}>{editingId ? "Update" : "Add"}</button>
          {editingId && (
            <button
              className="secondary"
              onClick={() => {
                setEditingId(null);
                setForm(EMPTY);
              }}
            >
              Cancel
            </button>
          )}
        </div>
      </div>

      <div className="card">
        <div className="row">
          <input
            style={{ maxWidth: 300 }}
            placeholder="Search questions or SQL"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <label className="muted" style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <input
              type="checkbox"
              style={{ width: "auto" }}
              checked={semantic}
              onChange={(event) => setSemantic(event.target.checked)}
            />
            Semantic search
          </label>
          <select style={{ maxWidth: 200 }} value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
            <option value="">All query types</option>
            {QUERY_TYPES.map((type) => (
              <option key={type}>{type}</option>
            ))}
          </select>
          <span className="muted">{visible.length} examples</span>
        </div>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Question</th>
              <th>SQL</th>
              <th>Type</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {visible.map((example) => (
              <tr key={example.id}>
                <td>{example.id}</td>
                <td>{example.question}</td>
                <td>
                  <pre>{example.sql}</pre>
                </td>
                <td>
                  <span className="badge">{example.query_type}</span>
                </td>
                <td>
                  <div className="row">
                    <button
                      className="secondary"
                      onClick={() => {
                        setEditingId(example.id);
                        setForm({
                          id: example.id,
                          question: example.question,
                          sql: example.sql,
                          query_type: example.query_type,
                        });
                      }}
                    >
                      Edit
                    </button>
                    <button className="danger" onClick={() => void remove(example.id)}>
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
