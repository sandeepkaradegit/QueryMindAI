// Must share a site with the frontend origin (localhost, not 127.0.0.1) or the SameSite=Lax session cookie is dropped.
function resolveApiBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  if (configured) {
    return configured.replace(/\/+$/, "");
  }

  const fallback = "http://localhost:8000";
  if (typeof window !== "undefined") {
    const host = window.location.hostname.toLowerCase();
    const isLocalHost = host === "localhost" || host === "127.0.0.1";
    if (!isLocalHost) {
      console.warn(
        "NEXT_PUBLIC_API_BASE_URL is not set. Falling back to http://localhost:8000. " +
          "Set NEXT_PUBLIC_API_BASE_URL for non-local environments.",
      );
    }
  }
  return fallback;
}

const API_BASE_URL = resolveApiBaseUrl();

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail?: unknown,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  if (response.status === 204) {
    return undefined as T;
  }
  const body = await response.json().catch(() => undefined);
  if (!response.ok) {
    const detail = body?.detail;
    const message = typeof detail === "string" ? detail : detail?.message ?? `Request failed (${response.status})`;
    throw new ApiError(response.status, message, detail);
  }
  return body as T;
}

export type User = { username: string; role: string };

export type QueryResult = {
  status: string;
  question: string;
  sql: string;
  explanation: string | null;
  query_type: string;
  examples_used: string[];
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
  latency_ms: number;
  nl_summary: string;
  transpiled_sql?: string | null;
  transpile_dialect?: string | null;
  cache_hit?: boolean;
  cache_similarity?: number | null;
};

export type ConversationHistoryTurn = {
  question: string;
  sql: string;
  nl_summary?: string;
};

export type Example = {
  id: string;
  question: string;
  sql: string;
  query_type: string;
  created_at: string;
  updated_at: string;
};

export type QueryLog = {
  id: number;
  trace_id: string | null;
  question: string;
  status: string;
  sql: string | null;
  error_reason: string | null;
  row_count: number | null;
  latency_ms: number | null;
  created_at: string;
};

export type GuardrailsConfig = {
  max_rows: number;
  allow_write_operations: boolean;
  allowed_table_prefixes: string[];
  updated_at: string;
};

export type ModelConfig = {
  provider: string;
  model: string | null;
  guardrail_model: string | null;
  temperature: number;
  max_tokens: number | null;
  updated_at: string;
  transpile_dialect: string | null;
  database_dialect: string | null;
  default_models: Record<string, string>;
};

export type SchemaAnnotation = {
  id: number;
  table_name: string;
  column_name: string | null;
  description: string | null;
  synonyms: string[];
  is_sensitive: boolean;
  updated_at: string;
};

export type ValueNormalization = {
  id: number;
  table_name: string;
  column_name: string;
  alias_value: string;
  canonical_value: string;
  updated_at: string;
};

export type SchemaTable = {
  name: string;
  kind: string;
  columns: { name: string; data_type: string; nullable: boolean; primary_key: boolean }[];
};

export type DatabaseConnection = {
  id: number;
  name: string;
  dialect: string;
  connection_url: string;
  is_active: boolean;
};

export type ScriptOption = {
  id: string;
  label: string;
  description: string | null;
};

export type ScriptDescriptor = {
  id: string;
  name: string;
  description: string;
  category: "database" | "evaluation" | "validation";
  compatibility: "generic" | "demo_only";
  compatibility_note: string;
  new_db_flow_order: number | null;
  new_db_flow_title: string | null;
  new_db_flow_detail: string | null;
  new_db_flow_option_id: string | null;
  options: ScriptOption[];
};

export type ScriptRunResult = {
  script_id: string;
  option_id: string | null;
  command: string[];
  succeeded: boolean;
  exit_code: number;
  duration_ms: number;
  stdout: string;
  stderr: string;
  stdout_truncated: boolean;
  stderr_truncated: boolean;
};

export const api = {
  me: () => request<User>("/auth/me"),
  login: (username: string, password: string) =>
    request<User>("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  bootstrap: (username: string, password: string) =>
    request<User>("/auth/bootstrap", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),

  query: (
    question: string,
    conversationHistory: ConversationHistoryTurn[] = [],
    includeExplanation = true,
  ) =>
    request<QueryResult>("/query", {
      method: "POST",
      body: JSON.stringify({
        question,
        conversation_history: conversationHistory,
        include_explanation: includeExplanation,
      }),
    }),

  schema: () => request<{ dialect: string; tables: SchemaTable[] }>("/admin/schema"),
  annotations: () => request<SchemaAnnotation[]>("/admin/schema/annotations"),
  valueNormalizations: (tableName?: string) =>
    request<ValueNormalization[]>(
      `/admin/schema/value-normalizations${tableName ? `?table_name=${encodeURIComponent(tableName)}` : ""}`,
    ),
  saveAnnotation: (body: {
    table_name: string;
    column_name: string | null;
    description: string | null;
    synonyms: string[];
    is_sensitive: boolean;
  }) => request<SchemaAnnotation>("/admin/schema/annotations", { method: "PUT", body: JSON.stringify(body) }),
  deleteAnnotation: (id: number) => request<void>(`/admin/schema/annotations/${id}`, { method: "DELETE" }),
  saveValueNormalization: (body: {
    table_name: string;
    column_name: string;
    alias_value: string;
    canonical_value: string;
  }) =>
    request<ValueNormalization>("/admin/schema/value-normalizations", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteValueNormalization: (id: number) =>
    request<void>(`/admin/schema/value-normalizations/${id}`, { method: "DELETE" }),

  examples: (search?: string, semantic = false) =>
    request<Example[]>(
      `/admin/examples${search ? `?search=${encodeURIComponent(search)}${semantic ? "&semantic=true" : ""}` : ""}`,
    ),
  createExample: (body: { id: string; question: string; sql: string; query_type: string }) =>
    request<Example>("/admin/examples", { method: "POST", body: JSON.stringify(body) }),
  updateExample: (id: string, body: { question: string; sql: string; query_type: string }) =>
    request<Example>(`/admin/examples/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteExample: (id: string) => request<void>(`/admin/examples/${encodeURIComponent(id)}`, { method: "DELETE" }),

  logs: (statusFilter?: string) =>
    request<QueryLog[]>(`/admin/logs${statusFilter ? `?status_filter=${encodeURIComponent(statusFilter)}` : ""}`),

  guardrails: () => request<GuardrailsConfig>("/admin/guardrails"),
  saveGuardrails: (body: { max_rows: number; allow_write_operations: boolean; allowed_table_prefixes: string[] }) =>
    request<GuardrailsConfig>("/admin/guardrails", { method: "PUT", body: JSON.stringify(body) }),

  modelConfig: () => request<ModelConfig>("/admin/config"),
  saveModelConfig: (body: {
    provider: string;
    model: string | null;
    guardrail_model: string | null;
    temperature: number;
    max_tokens: number | null;
    transpile_dialect: string | null;
  }) => request<ModelConfig>("/admin/config", { method: "PUT", body: JSON.stringify(body) }),

  databases: () => request<DatabaseConnection[]>("/admin/database"),
  createDatabase: (body: { name: string; dialect: string; connection_url: string }) =>
    request<DatabaseConnection>("/admin/database", { method: "POST", body: JSON.stringify(body) }),
  activateDatabase: (id: number) =>
    request<DatabaseConnection>(`/admin/database/${id}/activate`, { method: "POST" }),
  deleteDatabase: (id: number) => request<void>(`/admin/database/${id}`, { method: "DELETE" }),

  scripts: () => request<ScriptDescriptor[]>("/admin/scripts"),
  runScript: (body: { script_id: string; option_id?: string }) =>
    request<ScriptRunResult>("/admin/scripts/run", { method: "POST", body: JSON.stringify(body) }),
};
