"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, type SchemaAnnotation, type SchemaTable, type ValueNormalization } from "@/lib/api";

type Draft = { description: string; synonyms: string; is_sensitive: boolean };

export default function SchemaManagerPage() {
  const [tables, setTables] = useState<SchemaTable[]>([]);
  const [annotations, setAnnotations] = useState<SchemaAnnotation[]>([]);
  const [valueNormalizations, setValueNormalizations] = useState<ValueNormalization[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [normalizationDraft, setNormalizationDraft] = useState({
    column_name: "",
    alias_value: "",
    canonical_value: "",
  });
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const [schema, saved, normalizations] = await Promise.all([
        api.schema(),
        api.annotations(),
        api.valueNormalizations(),
      ]);
      setTables(schema.tables);
      setAnnotations(saved);
      setValueNormalizations(normalizations);
      setSelected((current) => current ?? schema.tables[0]?.name ?? null);
    } catch (caught) {
      setMessage({ kind: "error", text: caught instanceof ApiError ? caught.message : "Failed to load schema." });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const annotationFor = useMemo(() => {
    const lookup = new Map<string, SchemaAnnotation>();
    annotations.forEach((annotation) => lookup.set(`${annotation.table_name}.${annotation.column_name ?? ""}`, annotation));
    return (table: string, column: string | null) => lookup.get(`${table}.${column ?? ""}`);
  }, [annotations]);

  const table = tables.find((candidate) => candidate.name === selected);
  const tableValueNormalizations = useMemo(
    () => valueNormalizations.filter((record) => record.table_name === selected),
    [valueNormalizations, selected],
  );

  function draftFor(tableName: string, column: string | null): Draft {
    const key = `${tableName}.${column ?? ""}`;
    if (drafts[key]) return drafts[key];
    const saved = annotationFor(tableName, column);
    return {
      description: saved?.description ?? "",
      synonyms: saved?.synonyms.join(", ") ?? "",
      is_sensitive: saved?.is_sensitive ?? false,
    };
  }

  function updateDraft(tableName: string, column: string | null, patch: Partial<Draft>) {
    const key = `${tableName}.${column ?? ""}`;
    setDrafts((previous) => ({ ...previous, [key]: { ...draftFor(tableName, column), ...patch } }));
  }

  async function save(tableName: string, column: string | null) {
    const draft = draftFor(tableName, column);
    try {
      await api.saveAnnotation({
        table_name: tableName,
        column_name: column,
        description: draft.description.trim() || null,
        synonyms: draft.synonyms.split(",").map((value) => value.trim()).filter(Boolean),
        is_sensitive: draft.is_sensitive,
      });
      setMessage({ kind: "ok", text: `Saved ${tableName}${column ? `.${column}` : ""}.` });
      setDrafts((previous) => {
        const next = { ...previous };
        delete next[`${tableName}.${column ?? ""}`];
        return next;
      });
      await load();
    } catch (caught) {
      setMessage({ kind: "error", text: caught instanceof ApiError ? caught.message : "Save failed." });
    }
  }

  async function saveValueNormalization() {
    if (!table) return;
    if (!normalizationDraft.column_name || !normalizationDraft.alias_value.trim() || !normalizationDraft.canonical_value.trim()) {
      setMessage({ kind: "error", text: "Column, alias value, and canonical value are required." });
      return;
    }
    try {
      await api.saveValueNormalization({
        table_name: table.name,
        column_name: normalizationDraft.column_name,
        alias_value: normalizationDraft.alias_value,
        canonical_value: normalizationDraft.canonical_value,
      });
      setNormalizationDraft((previous) => ({ ...previous, alias_value: "", canonical_value: "" }));
      setMessage({ kind: "ok", text: "Saved value normalization rule." });
      await load();
    } catch (caught) {
      setMessage({ kind: "error", text: caught instanceof ApiError ? caught.message : "Save failed." });
    }
  }

  async function removeValueNormalization(id: number) {
    try {
      await api.deleteValueNormalization(id);
      setMessage({ kind: "ok", text: "Deleted value normalization rule." });
      await load();
    } catch (caught) {
      setMessage({ kind: "error", text: caught instanceof ApiError ? caught.message : "Delete failed." });
    }
  }

  return (
    <div>
      <h2>Schema Manager</h2>
      <p className="muted">
        Add descriptions and synonyms to improve schema linking. Columns marked sensitive are excluded from the
        model&apos;s context entirely.
      </p>
      {message && <div className={message.kind === "ok" ? "ok" : "error"}>{message.text}</div>}

      <div className="row" style={{ alignItems: "flex-start" }}>
        <div className="card" style={{ minWidth: 200 }}>
          {tables.map((candidate) => (
            <div key={candidate.name}>
              <a href="#" onClick={(event) => { event.preventDefault(); setSelected(candidate.name); }}>
                {candidate.name === selected ? <strong>{candidate.name}</strong> : candidate.name}
              </a>
            </div>
          ))}
        </div>

        {table && (
          <div className="card" style={{ flex: 1 }}>
            <h3>{table.name}</h3>
            <TableRow
              label={`Table: ${table.name}`}
              draft={draftFor(table.name, null)}
              onChange={(patch) => updateDraft(table.name, null, patch)}
              onSave={() => save(table.name, null)}
              showSensitive={false}
            />
            <table>
              <thead>
                <tr>
                  <th>Column</th>
                  <th>Type</th>
                  <th>Description</th>
                  <th>Synonyms (comma-separated)</th>
                  <th>Sensitive</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {table.columns.map((column) => {
                  const draft = draftFor(table.name, column.name);
                  return (
                    <tr key={column.name}>
                      <td>
                        {column.name} {column.primary_key && <span className="badge">PK</span>}
                      </td>
                      <td className="muted">{column.data_type}</td>
                      <td>
                        <input
                          value={draft.description}
                          onChange={(event) => updateDraft(table.name, column.name, { description: event.target.value })}
                        />
                      </td>
                      <td>
                        <input
                          value={draft.synonyms}
                          onChange={(event) => updateDraft(table.name, column.name, { synonyms: event.target.value })}
                        />
                      </td>
                      <td>
                        <input
                          type="checkbox"
                          checked={draft.is_sensitive}
                          onChange={(event) => updateDraft(table.name, column.name, { is_sensitive: event.target.checked })}
                        />
                      </td>
                      <td>
                        <button onClick={() => void save(table.name, column.name)}>Save</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <h4 style={{ marginTop: 20 }}>Value Normalization Rules</h4>
            <p className="muted">
              Map user-entered values to canonical values for this table&apos;s text columns (used in query generation and SQL normalization).
            </p>
            <div className="row" style={{ gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
              <select
                value={normalizationDraft.column_name}
                onChange={(event) => setNormalizationDraft((previous) => ({ ...previous, column_name: event.target.value }))}
              >
                <option value="">Select column</option>
                {table.columns.map((column) => (
                  <option key={column.name} value={column.name}>
                    {column.name}
                  </option>
                ))}
              </select>
              <input
                placeholder="Alias value (e.g. united kingdom)"
                value={normalizationDraft.alias_value}
                onChange={(event) => setNormalizationDraft((previous) => ({ ...previous, alias_value: event.target.value }))}
              />
              <input
                placeholder="Canonical value (e.g. GB)"
                value={normalizationDraft.canonical_value}
                onChange={(event) => setNormalizationDraft((previous) => ({ ...previous, canonical_value: event.target.value }))}
              />
              <button onClick={() => void saveValueNormalization()}>Save value rule</button>
            </div>
            <table>
              <thead>
                <tr>
                  <th>Column</th>
                  <th>Alias Value</th>
                  <th>Canonical Value</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {tableValueNormalizations.map((record) => (
                  <tr key={record.id}>
                    <td>{record.column_name}</td>
                    <td>{record.alias_value}</td>
                    <td>{record.canonical_value}</td>
                    <td>
                      <button className="secondary" onClick={() => void removeValueNormalization(record.id)}>
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
                {!tableValueNormalizations.length && (
                  <tr>
                    <td colSpan={4} className="muted">
                      No value normalization rules for this table.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function TableRow({
  label,
  draft,
  onChange,
  onSave,
}: {
  label: string;
  draft: Draft;
  onChange: (patch: Partial<Draft>) => void;
  onSave: () => void;
  showSensitive: boolean;
}) {
  return (
    <div style={{ marginBottom: 16 }}>
      <label>{label} — description</label>
      <input value={draft.description} onChange={(event) => onChange({ description: event.target.value })} />
      <label>Synonyms (comma-separated)</label>
      <input value={draft.synonyms} onChange={(event) => onChange({ synonyms: event.target.value })} />
      <div style={{ marginTop: 8 }}>
        <button onClick={onSave}>Save table metadata</button>
      </div>
    </div>
  );
}
