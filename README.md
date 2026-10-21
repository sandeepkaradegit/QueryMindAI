# QueryMind AI

QueryMind AI is a planned natural-language analytics application. A business user asks a question in everyday language; the system uses the currently configured database schema to create a read-only SQL query, checks it, runs it, and presents the results as a table with a short explanation.

This README is the project's main guide and implementation specification. It is written to help both a first-time user run the current code and a future developer understand what the complete application is expected to do. Features marked **Planned** are requirements, not claims about features already delivered.

## Project Status

The project is being developed in small, validated increments.

| Area | Status |
|---|---|
| E-commerce SQLite demo schema and deterministic synthetic data | Implemented |
| Database integrity and sample-query validator | Implemented |
| FastAPI application and typed initial API contracts | Implemented |
| SQLAlchemy database engine factory, defaulting to the demo SQLite file | Implemented |
| Table, column, primary-key, and foreign-key inspection for the configured database | Implemented |
| Query classifier with schema-based table/column mention extraction | Implemented |
| Schema linker with names, descriptions, synonyms, and ambiguity reporting | Implemented |
| Curated question-to-SQL library with FAISS vector retrieval (fastembed `BAAI/bge-small-en-v1.5`), lexical fallback, and reranking | Implemented (34 examples) |
| Groq SQL generation with configurable API key/model and injected-provider tests | Implemented |
| Input scope checks and dialect-aware generated-SQL guardrails | Implemented |
| OpenEvals prompt-injection and response safety checks with structured fail-closed verdicts | Implemented (mock-tested) |
| Request tracing, deterministic example reranking, structured guardrail events, and latency/token metrics | Implemented (LangSmith export is opt-in) |
| Authentication (single admin role, session cookie) | Implemented (no per-user roles, rate limiting, or HTTPS-only cookies yet) |
| Per-user permissions and multi-role authorization | Planned |
| Guarded SQL generation output | Implemented |
| Labeled offline SQL/retrieval evaluation baseline | Implemented (fixture-based; 17-case legacy + 30-case current set) |
| Validated read-only SQL execution and result formatting | Implemented |
| Admin-managed database connection setup (`/admin/database`) | Implemented (SQLite and PostgreSQL verified; MySQL dialect-mapped but driver untested) |
| Persistent schema descriptions, synonyms, and sensitive-column flags (`/admin/schema/annotations`) | Implemented |
| Model configuration for provider/temperature/token budget (`/admin/config`, alias `/admin/model-config`) | Implemented (Groq, OpenAI, Anthropic, Ollama; SQL transpile toggle) |
| `schema.sql` file import | Planned |
| Examples Manager and semantic/vector retrieval | Implemented |
| Examples Manager CRUD (`/admin/examples`), Query Logs (`/admin/logs`), Guardrails Config (`/admin/guardrails`) | Implemented (keyword and semantic search; guardrails scope row cap and table prefixes) |
| Next.js query chat and six admin panels (`frontend/`) | Implemented (type-checked and production-built; not browser-tested or covered by automated UI tests) |
| 25+ question evaluation harness and report | Implemented (30-case set with offline and live reports) |
| Production authentication, deployment, and operational hardening | Planned |

The `/query` endpoint runs the full pipeline (classification, schema linking, example retrieval, Groq generation, guardrails, then SQL execution and result formatting) and returns the generated SQL, executed rows, and a natural-language summary. Every endpoint except `/health` and `/auth/*` now requires a logged-in session.

### Admin Configuration API

Admin configuration is stored locally in `data/querymind_admin.sqlite3` (gitignored, plaintext) — a separate metadata database from whatever business database `/query*` executes against. The Next.js Admin UI writes these settings through the same API routes.

- `GET/POST /admin/database`, `POST /admin/database/{id}/activate`, `DELETE /admin/database/{id}`: register, list, activate, and remove database connections. Registering a connection test-connects it first and rejects unreachable URLs or a URL/dialect mismatch with HTTP 422. The response masks any password in the connection URL. Once a connection is activated, `get_database_engine` uses it for every `/query*` and `/admin/schema` request instead of the `QUERYMIND_DATABASE_URL` environment variable; with no active connection, the environment variable (or the demo SQLite file) is still used, so existing setups keep working unchanged.
- `GET/PUT /admin/schema/annotations`: set a per-table or per-column `description`, `synonyms`, and an `is_sensitive` flag. Descriptions/synonyms feed the schema linker's alias matching (`/query/link` and generation). Columns flagged `is_sensitive` are removed from the schema catalog before it reaches the classifier, schema linker, prompt, and guardrails — they become invisible to the model and to generated SQL, not just hidden from the prompt text.
- `GET/PUT /admin/schema/value-normalizations`, `DELETE /admin/schema/value-normalizations/{id}`: manage strict alias-to-canonical value mappings per column (for example `customers.country: "united kingdom" -> "GB"`). These mappings are applied during question normalization, injected into the SQL generation prompt, and used for guardrail-stage SQL literal normalization.
- Saving or deleting schema annotations/value-normalization rules clears the in-memory semantic response cache, so metadata changes apply immediately without backend restart.
- `GET/PUT /admin/config` (alias `/admin/model-config`): set `provider` (`groq`, `openai`, `anthropic`, `ollama`), `model`, `guardrail_model`, `temperature` (0-2), `max_tokens`, and `transpile_dialect` (`sqlite`/`postgresql`/`mysql`/none). Credentials come from `.env`: `GROQ_API_KEY`, `OPENAI_API_KEY` (optional `OPENAI_BASE_URL`), `ANTHROPIC_API_KEY`, and `OLLAMA_BASE_URL` (default `http://localhost:11434`); a missing key returns HTTP 503. A blank `model` uses the provider default (`GROQ_MODEL`, `gpt-4o-mini`, `claude-sonnet-4-20250514`, `llama3.1`). The SQL is always generated and executed in the active database's dialect; `transpile_dialect` additionally returns a `sqlglot.transpile` copy as `transpiled_sql` in `/query` and `/query/generate`. The OpenEvals safety judge always runs on Groq (`guardrail_model`), so without `GROQ_API_KEY` requests fail closed regardless of the generation provider.
- `GET /admin/scripts`: returns allowlisted script metadata and any required run options (for example offline vs live evaluation mode).
- `POST /admin/scripts/run`: runs one allowlisted script command under the backend process (no arbitrary shell), returns command, exit code, duration, and captured output.

### PostgreSQL and MySQL Support

SQLAlchemy dialect inspection, SQLGlot dialect mapping, and the SQL executor's defense-in-depth read-only enforcement (`SET TRANSACTION READ ONLY` for PostgreSQL, `SET SESSION TRANSACTION READ ONLY` for MySQL) all support `postgresql` and `mysql` in addition to `sqlite`. The `psycopg[binary]` driver is installed so `postgresql+psycopg://` connection URLs work out of the box; no MySQL driver is installed, so MySQL is dialect-mapped in code but not yet runnable or tested end to end.

PostgreSQL is verified with a real integration test, [`scripts/validate_postgres_integration.py`](scripts/validate_postgres_integration.py), that creates a temporary table, runs schema discovery and a guardrail-validated `SELECT` through the real executor, confirms a write is blocked by the read-only enforcement, then drops the table. This test only runs against a real PostgreSQL server and is skipped by default (exit code 0, with setup instructions printed) because this assistant cannot create database credentials on your behalf. To run it: create a dedicated PostgreSQL role/database yourself, set `QUERYMIND_POSTGRES_TEST_URL` in your local `.env`, then run the script.

The committed `.env.sample` is a safe starter template. The actual `.env` is local-only, ignored by Git, and should be created from that sample. Keep `.env.sample` free of passwords, API keys, and other private values.

## Who It Is For

- **Analysts and operations staff** who know what information they need but do not want to write SQL.
- **Data engineers and administrators** who configure the database schema, explain business-specific terms, manage examples, and set query safety limits.
- **Developers and evaluators** who need independently testable pipeline components and a repeatable accuracy and safety evaluation.

The intended product is domain-agnostic. The included e-commerce database is sample data for development and evaluation, not a fixed schema that future application code should assume.

## How a Query Should Work

The `POST /query` endpoint implements this flow end to end:

1. The user selects the configured database and asks a question, for example, "What were the top 10 products by gross revenue last quarter?"
2. The query classifier labels the request as simple, aggregate, join, temporal, or unsupported. Unsupported write or schema-change requests stop here.
3. The schema linker uses the active database metadata to identify relevant tables and columns. If a phrase has multiple plausible meanings, it asks the user to clarify. Administrator-defined descriptions, synonyms, and per-column value-normalization aliases are persisted and used in live linking/generation.
4. The examples retriever finds a few similar, approved question-to-SQL examples.
5. The SQL generator receives the question, dialect, relevant schema, and retrieved examples. It returns SQL and a plain-language explanation.
6. The SQL validator parses the SQL for the selected dialect, checks that it is read-only and uses allowed schema objects, and applies the configured maximum row count. If validation fails, the generator receives one bounded correction retry with the validation error before the request is refused.
7. The SQL executor runs only validated SQL through a connection with a defense-in-depth read-only check, enforces the configured row cap and execution timeout, and records latency and row count.
8. The result formatter returns table data and a concise natural-language summary.

Every pipeline component has typed inputs and outputs and is testable without requiring a live LLM call. The implementation makes it possible to replace one model provider or database connection without rewriting the other components.

## Database and Domain Design

### Included Demo Database

The demo domain is e-commerce and uses SQLite by default. Its schema is in `database/schema.sql`; `scripts/seed_db.py` recreates realistic, deterministic synthetic records in `data/querymind.sqlite3`.

| Table | Meaning |
|---|---|
| `categories` | Product category lookup values |
| `customers` | Customer profile and signup date |
| `products` | Catalog items and current list price |
| `orders` | Customer purchase headers, date, status, and stored total |
| `order_items` | Products and quantities purchased in each order |
| `payments` | Charge and refund events associated with an order |
| `shipments` | Shipment and delivery details for an order |

The six primary tables each have at least 1,000 rows. Foreign keys connect the entities, multiple timestamp columns support temporal questions, and the item, payment, and order tables give aggregate and join patterns to test. See `docs/data_dictionary.md` for column meanings and metric definitions.

Money is represented as integer cents, not floating-point dollars. In this sample, **gross ordered revenue** is the sum of `order_items.line_total_cents`; **net collected amount** is charge payments minus refunds. These measures can differ. Do not use the word "revenue" as if it always had one universal database meaning: an administrator must document the organization's intended definition.

### Connecting Another Database

The application uses SQLAlchemy database URLs and schema inspection rather than hardcoded table names. It uses SQLite by default; PostgreSQL is supported and integration-tested (see PostgreSQL and MySQL Support above). MySQL is dialect-mapped in the SQL guardrails and executor but has no installed driver or integration test yet. Oracle and SQL Server are not assignment requirements; adding either would require a driver, dialect-aware validation, and its own integration tests.

A user may provide either:

- **A live database connection**, via `POST /admin/database` followed by `POST /admin/database/{id}/activate`. The application test-connects the URL, then inspects the accessible tables, columns, and foreign keys through the standard schema/generation/query endpoints. A least-privilege, read-only account is still recommended for the target database; the application's own defense-in-depth read-only enforcement is not a substitute for database-level permissions.
- **A `schema.sql` file.** Parsing uploaded DDL to learn a database's shape without a live connection is not implemented. The user must connect the corresponding database through `/admin/database` to get real query results.

Database credentials belong in environment configuration or a secrets manager, never source control, API responses, or query logs. The admin metadata database (`data/querymind_admin.sqlite3`) currently stores connection URLs in plaintext; protect that file's filesystem permissions like any other credential store. Schema inspection and query execution are restricted to configured objects, and columns flagged `is_sensitive` through `/admin/schema/annotations` are omitted from model context.

## API Contract

The backend uses FastAPI and Pydantic. All endpoints except `/health` and `/auth/*` require an authenticated session (see Authentication below). The current API is:

| Method and path | Current behavior |
|---|---|
| `GET /health` | Returns `{"status":"ok"}` when the API process is responding. |
| `GET /admin/schema` | Returns the configured database schema. |
| `POST /query/classify` | Classifies a question against the configured schema. |
| `POST /query/link` | Links terms against the configured schema. |
| `POST /query/examples/retrieve` | Retrieves similar curated examples. |
| `POST /query/generate` | Runs OpenEvals, generates against the configured schema, validates SQL, then runs output checks. SQL is not executed. |
| `POST /query` | Runs the full pipeline and executes the validated SQL, returning columns, rows, row count, latency, and a natural-language summary. Optional `include_explanation: false` suppresses the explanation text in the response. |
| `GET/POST /admin/database`, `POST /admin/database/{id}/activate`, `DELETE /admin/database/{id}` | Register, list, activate, and remove database connections used by `/query*` and `/admin/schema`. |
| `GET/PUT /admin/schema/annotations`, `DELETE /admin/schema/annotations/{id}` | Manage per-table/column descriptions, synonyms, and sensitive-column flags. |
| `GET/PUT /admin/schema/value-normalizations`, `DELETE /admin/schema/value-normalizations/{id}` | Manage per-column alias-to-canonical value mappings used during generation and SQL normalization. |
| `GET/PUT /admin/config` (alias `/admin/model-config`) | Configure provider (Groq/OpenAI/Anthropic/Ollama), model, guardrail model, temperature, max token budget, and SQL transpile target. |
| `GET/POST /admin/examples`, `PUT/DELETE /admin/examples/{id}` | Persistent CRUD and substring search for curated question-to-SQL examples. The starter JSON library is imported once, on first read, into the admin database; after that the database is the source of truth. |
| `GET /admin/logs` | Paginated query log (`status_filter`, `limit` up to 500, `offset`) recording every `/query` and `/query/generate` outcome. |
| `GET/PUT /admin/guardrails` | Row cap and allowed table prefixes. `allow_write_operations: true` is always rejected with HTTP 422. |
| `GET /admin/scripts`, `POST /admin/scripts/run` | List allowlisted script commands and execute a selected command (seed, evaluation variants, validator scripts) with captured stdout/stderr and exit code. |
| `POST /auth/bootstrap`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` | Session authentication (public except `/auth/me`). |

### Authentication

Authentication is a single `admin` role with an `HttpOnly`, `SameSite=Lax` session cookie. Passwords are salted PBKDF2-SHA256 hashes; only a SHA-256 hash of the session token is stored. `POST /auth/bootstrap` creates the first admin and refuses to run once any user exists, so create it immediately after first start on any reachable machine. Usernames are trimmed for leading/trailing spaces, and login matches usernames case-insensitively. This is enough to stop anonymous access, but it is **not** a complete multi-user security model: there are no per-user roles or permissions, no password reset or user-management endpoints, no login rate limiting or lockout, and no CSRF token (the `SameSite=Lax` cookie and a single allowed CORS origin are the only mitigations). The cookie is not marked `Secure`, so serve it over HTTPS (for example behind a reverse proxy) before any non-local use. Session tokens are not rotated on login.

### Frontend

The `frontend/` directory is a Next.js app with the query chat and six admin panels (Schema Manager, Examples Manager, Query Logs, Guardrails Config, Model Config, Script Runner). Run the API and the UI in separate terminals:

```powershell
uv run uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
cd frontend; npm install; npm run dev
```

Open `http://localhost:3000` and use "Create first admin" once. Use `localhost` (not `127.0.0.1`) for both the UI and the API: the session cookie is `SameSite=Lax`, and browsers treat the two hostnames as different sites, so mixing them makes login silently fail. Set `NEXT_PUBLIC_API_BASE_URL` if the API is elsewhere (no trailing slash needed; it is normalized automatically), and set `QUERYMIND_FRONTEND_ORIGIN` on the API to match the UI's origin. If `NEXT_PUBLIC_API_BASE_URL` is missing, the frontend falls back to `http://localhost:8000` and logs a warning when opened from a non-local host. The frontend passes `npm run typecheck` and `npm run build`, and the API's CORS/cookie handshake was checked against a running server, but the UI has not been exercised in a real browser and has no automated UI tests.

For frontend environment setup, copy [`frontend/.env.example`](frontend/.env.example) to `frontend/.env.local` and adjust as needed.

The query chat persists conversation turns in browser local storage and sends the most recent successful turns as `conversation_history` with each new `/query` request so follow-up questions can use prior context.
Semantic response caching skips time-sensitive questions (for example `today`, `this week`, explicit dates) so temporal filters are always regenerated instead of reusing a semantically similar cached response. Cache reuse also requires salient intent tokens to match between the current question and the cached question, preventing near-neighbor reuse when a new field is explicitly requested (for example adding `order date`).

Known gaps: query logs store raw question text and SQL unencrypted; the Query Logs export is client-side CSV of the currently loaded page; and charting is intentionally lightweight (auto bar preview for simple two-column numeric results only, no configurable chart types).

Fine-grained per-user table/column permissions are not implemented. A real multi-user deployment still needs an identity provider or user management, authorization policies, HTTPS, secret management, and database-enforced least privilege before exposure.

The current classifier uses deterministic text cues and the configured schema catalog. It checks for unsafe write/DDL intent first, then temporal, aggregate, and join cues, and otherwise labels the request simple. A classifier label is an early routing hint, not a security guarantee: SQL validation and read-only database permissions are still required before execution. Because natural language is ambiguous, this lightweight classifier can misclassify unusual phrasing; later pipeline components must handle clarification and validation.

Schema inspection uses SQLAlchemy's dialect-aware inspector and does not assume the e-commerce table names. The endpoint reads the configured database URL from `QUERYMIND_DATABASE_URL` or uses the demo SQLite file by default. This is currently a developer-configured connection, not an admin UI for arbitrary end-user connection management. Only SQLite has been integration-tested so far; PostgreSQL and MySQL need their drivers and integration tests before being considered supported by this implementation. Parsing an uploaded `schema.sql` file is not implemented.

Example request:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/admin/schema"
```

The response contains a `tables` array. Each table reports its name, schema, kind (`table` or `view`), columns, and foreign-key constraints. Each column reports its database type, nullability, and primary-key status.

Classify a question using the current configured schema:

```powershell
$body = @{ question = "How many orders were placed last month?" } | ConvertTo-Json -Compress
Invoke-RestMethod -Uri "http://127.0.0.1:8000/query/classify" -Method Post -ContentType "application/json" -Body $body
```

The response contains a `query_type` (`SELECT_SIMPLE`, `SELECT_AGGREGATE`, `SELECT_JOIN`, `SELECT_TEMPORAL`, or `UNSUPPORTED`), plus `tables_mentioned` and `columns_mentioned` arrays. These names are matched against live schema metadata; the classifier does not assume the e-commerce demo domain.

Resolve schema terms and check for ambiguous columns:

```powershell
$body = @{ question = "Show amount for payments" } | ConvertTo-Json -Compress
Invoke-RestMethod -Uri "http://127.0.0.1:8000/query/link" -Method Post -ContentType "application/json" -Body $body
```

The response contains `resolved_tables`, `resolved_columns` (table and column names), and `ambiguities` (the matched term and candidate columns). If more than one column matches a term and the question does not provide enough table context, the linker reports the candidates instead of choosing one.

The linker component accepts optional table/column descriptions and synonyms. Live endpoint linking uses persisted Schema Manager metadata (descriptions, synonyms, sensitive-column flags, and value-normalization aliases). The linker uses deterministic lexical matching; it is not an LLM and may not understand arbitrary paraphrases.

Retrieve similar saved examples:

```powershell
$body = @{ question = "Show monthly sales by product"; top_k = 3 } | ConvertTo-Json -Compress
Invoke-RestMethod -Uri "http://127.0.0.1:8000/query/examples/retrieve" -Method Post -ContentType "application/json" -Body $body
```

The response contains matching questions, their SQL, query-type labels, stable IDs, and similarity scores. Add or edit entries in `examples/question_sql_examples.json`; each entry needs a unique `id`, a natural-language `question`, SQL text, and a `query_type`. The default ranker embeds example questions with a local ONNX model (fastembed `BAAI/bge-small-en-v1.5`, downloaded on first use) and searches a FAISS inner-product index over normalized vectors, i.e. cosine similarity. Matches below `QUERYMIND_VECTOR_MIN_SIMILARITY` (default 0.6) are dropped, so unrelated questions retrieve nothing. If the packages or model are unavailable, or `QUERYMIND_EXAMPLE_RETRIEVAL=lexical` is set, retrieval falls back to local TF-IDF cosine similarity over words and word pairs. The Examples Manager's "Semantic search" checkbox (`GET /admin/examples?search=...&semantic=true`) ranks all examples by embedding similarity. The library has 34 examples covering simple, aggregate, join and temporal queries; `python scripts/validate_examples_library.py` checks the count, uniqueness, and that every SQL executes on the demo database. Validation that an example SQL matches a connected schema remains future work.

Generate SQL with the configured Groq model:

```powershell
$body = @{ question = "List the top 10 products by gross revenue" } | ConvertTo-Json -Compress
Invoke-RestMethod -Uri "http://127.0.0.1:8000/query/generate" -Method Post -ContentType "application/json" -Body $body
```

The response includes the generated `sql`, a short `explanation`, the selected `model`, classifier `query_type`, and IDs of retrieved `examples_used`. Unsupported write/DDL requests are rejected before generation. If schema linking finds an ambiguous column, the endpoint returns HTTP 409 with candidate columns so the user can clarify. SQL-validation guardrail failures return HTTP 422 with a user-actionable message, while provider failures or malformed provider output return HTTP 502. Missing provider configuration returns HTTP 503.

The generator reads `GROQ_API_KEY` and `GROQ_MODEL` from `.env` (or process environment variables). The starter model value is `llama-3.3-70b-versatile`; change it to a model currently available to your Groq account if necessary. Obtain the API key from Groq's official console and enter it directly into your local `.env`; never paste it into chat, commit it, or put it in `.env.sample`. Sending this endpoint a real request uses the configured Groq service and may consume account quota. Automated validation injects a fake provider and does not call Groq.

Generated SQL is untrusted model output. Before returning it, the API parses it with SQLGlot for the configured dialect, requires exactly one read-only query, rejects wildcard projections, checks tables and columns against the live schema and the question/example context, and adds or clamps a result limit. Rejected model SQL is not included in the error response. The returned explanation is still model-authored prose and is not independently fact-checked. These are application-level guardrails, not a substitute for database read-only permissions.

The input guardrail rejects clear code/programming requests and unrelated non-analytics requests before calling Groq. This is a deterministic scope filter, not a general-purpose intent classifier or a security boundary. It can reject unusual analytics wording or fail to recognize subtle off-topic requests, so continue reviewing its evaluation cases as the classifier evolves.

The generation endpoint also runs OpenEvals LLM-as-judge checks using Groq as the judge provider. The input evaluator checks for prompt injection, attempts to override access/instructions, and off-topic requests before SQL generation. The output evaluator checks the generated SQL/explanation for PII leakage, unsafe content, and unsupported claims after deterministic SQL validation. Both require a structured `allow`/`block` verdict with an enumerated reason code. Missing, malformed, inconsistent, or failed evaluations block the request. Block reasons are written to application logs as stage and reason code only; users receive a generic refusal, not the judge's reasoning.

The OpenEvals judge uses Groq's OpenAI-compatible API endpoint through the OpenAI SDK. It reuses `GROQ_API_KEY` and uses a separate `GROQ_GUARDRAIL_MODEL` so the judge can be configured for structured JSON output independently of SQL generation. The default judge model is `openai/gpt-oss-120b`; verify that this model is available to your Groq account and supports structured outputs in your selected Groq region/account. This setup does not require an OpenAI account or AWS account. The two LLM checks add requests before and after generation, increasing latency and token usage. Automated tests inject fake evaluators and do not make external calls. OpenEvals is probabilistic; SQLGlot validation and database permissions remain separate mandatory controls.

### Observability and Tracing

Every HTTP request receives a `X-QueryMind-Trace-Id` response header and emits structured JSON lifecycle and request-metric events through the `querymind.observability` logger. To export inspectable traces, set `LANGSMITH_API_KEY` in the ignored local `.env`; tracing is enabled when a key is present unless `LANGSMITH_TRACING=false`. Set `LANGSMITH_TRACING=true` and `LANGSMITH_PROJECT` explicitly for a deployment. With no API key, the application continues to emit local structured events and does not attempt LangSmith export.

Generation traces contain schema context, linked tables/columns, candidate examples and retrieval scores, reranked examples and scores, the exact system/user messages sent to Groq, the raw model response, sanitized SQL, guardrail decisions/reason codes, and end-to-end/stage latency. Reranking currently combines lexical similarity (65%) with query-term coverage (35%); it is a deterministic lexical baseline, not a learned cross-encoder. The generation model's prompt/completion/total tokens and OpenEvals judge token usage are recorded when returned by Groq. Metrics include `token_usage_complete` and `unreported_usage_calls`; a provider response without usage metadata is not presented as a complete total. OpenEvals request latency is included in the stage and end-to-end duration.

These traces and structured events include user questions, schema metadata, retrieved example SQL, prompts, and generated SQL. LangSmith export sends that data to the configured LangSmith service, and local structured logs may also contain it. Use only data approved for that destination, configure retention/access controls, and leave `LANGSMITH_API_KEY` unset when trace export is not approved. Provider API keys are never added to trace inputs.

Example request:

```json
{
  "question": "What were the top 10 products by gross revenue last quarter?"
}
```

Execute a question end to end with `POST /query`:

```powershell
$body = @{ question = "What are the top 10 products by gross revenue?" } | ConvertTo-Json -Compress
Invoke-RestMethod -Uri "http://127.0.0.1:8000/query" -Method Post -ContentType "application/json" -Body $body
```

The response includes `status`, the generated `sql` and `explanation`, `query_type`, `examples_used`, `columns`, `rows`, `row_count`, `latency_ms`, and a short `nl_summary`. The SQL executor runs only the already-validated, guardrail-checked query; it sets `PRAGMA query_only = ON` on SQLite connections as a defense-in-depth read-only check, enforces the configured row cap independently of the SQL `LIMIT` clause, and cancels queries that exceed `QUERYMIND_QUERY_TIMEOUT_SECONDS` (default 5 seconds, capped at 30). Execution timeouts return HTTP 504; other execution failures return HTTP 502 without exposing raw database errors. Unsupported/off-topic requests, ambiguous schema terms, and guardrail blocks are refused before any SQL is executed, exactly as in `/query/generate`.

Implemented admin route groups:

| Route group | Purpose |
|---|---|
| `/admin/schema` | Inspect tables and columns; edit descriptions, synonyms, sensitivity flags, and value-normalization aliases. |
| `/admin/examples` | Create, update, delete, search, and label approved question-to-SQL examples. |
| `/admin/logs` | Filter and export query outcomes, latency, and row counts without logging secrets. |
| `/admin/guardrails` | Configure allowed operations, maximum rows, and allowed schemas or table prefixes. |
| `/admin/config` | Configure model provider, model options, dialect, and active database connection. |
| `/admin/scripts` | List and run allowlisted seed, evaluation, and validation scripts from the admin UI. |

Admin changes are validated and persisted. Administrative access control is required before deploying the application for multiple users.

## Admin Interface

The Next.js application includes a natural-language query chat and six functional admin panels:

1. **Schema Manager:** browse schema objects; edit human-readable descriptions/synonyms; flag sensitive columns so they are excluded from model prompts; and configure per-column value-normalization aliases used by generation and guardrails.
2. **Examples Manager:** create, edit, delete, search, and label question-to-SQL examples by query type.
3. **Query Logs:** inspect status, duration, row count, and safe query metadata; filter and export records.
4. **Guardrails Config:** set row limits and allowed SQL operations and restrict accessible schemas or table prefixes.
5. **Model Config:** select Anthropic, OpenAI, or Ollama; choose model, temperature, token budget, SQL dialect, and database connection.
6. **Script Runner:** run allowlisted script commands for database seeding, evaluation harness runs, and validator checks with captured stdout/stderr.
   It also shows a migration checklist and compatibility badges (`generic` vs `current-demo-only`) so you can identify which scripts must be rewritten for a new database domain.

All admin settings should be validated by the backend. Provider API keys and database passwords must be entered as secrets and must never be returned by configuration endpoints.

## Safety and Reliability Requirements

- Default to `SELECT`-only execution. Reject DDL and data-changing statements, including destructive statements hidden inside multi-statement input, unless an explicitly designed and separately authorized workflow says otherwise. The analytics product should not need write access.
- Enforce safety in both application validation and database permissions. SQL text checks alone are not a security boundary.
- Use SQLGlot or an equivalent dialect-aware parser for syntax and statement checks. Never rely on matching dangerous keywords as plain substrings.
- Validate referenced tables and columns against the active schema. Apply row limits in syntax supported by the selected dialect.
- Use database timeouts and bounded result sizes. Do not reveal raw driver errors to ordinary users.
- Keep credentials, API keys, and sensitive query values out of source control and logs.
- Record enough metadata for debugging and evaluation while applying retention and privacy rules to user questions and returned rows.
- Make LLM calls replaceable and configurable. Tests for database safety and deterministic logic must not depend on network access to an LLM.

## Evaluation Requirements

The completed project should contain at least 30 curated question-to-SQL examples and a 25-question test set with ground-truth SQL. Evaluation must be reproducible against the same seeded database.

The labeled set in `evaluations/sql_generation_v1.json` has 30 cases: 25 answer cases with ground-truth SQL (simple, aggregate, join and temporal), plus refusal and clarification cases. Run it from the project root with:

```powershell
uv run python scripts/run_evaluation.py --output evaluations/report_offline.json --report evaluations/report_offline.md
uv run python scripts/run_evaluation.py --live --output evaluations/report_live.json --report evaluations/report_live.md
```

The default run uses the seeded demo SQLite database and fixed provider fixtures (no LLM calls), so it validates the pipeline, retrieval and reporting deterministically; its accuracy is trivially 100%. `--live` calls the configured Groq model (needs `GROQ_API_KEY`) and measures real accuracy. Both modes report:

- **Execution accuracy:** generated and ground-truth SQL are both executed and the result sets compared row by row (ordered when the reference has `ORDER BY`, otherwise as multisets; floats rounded to 6 places).
- **Exact match rate:** normalized SQL equality or an empty `sqlglot.diff` edit script.
- **Per-query-type breakdown** of both metrics, plus retrieval precision@3 / recall@3.

The latest live results are in `evaluations/report_live.md` (execution accuracy 76%, exact match 44% on the 25 answer cases). Exact match is low because equivalent SQL is often written differently (aliases, `LIMIT`, join style), which is why execution accuracy is the primary metric. RAGAS-style scoring and LLM-as-a-judge quality scores are not part of this harness.

The assignment's minimum target is 65% overall weighted score and at least 50% execution accuracy. The assignment weights execution accuracy at 30%, exact match at 15%, guardrails at 15%, admin completeness at 20%, modularity at 10%, and documentation/evaluation at 10%.

## Development Setup

### Prerequisites

- Windows, macOS, or Linux.
- Python 3.10 or newer.
- `uv`, the Python project and package manager: <https://docs.astral.sh/uv/>.

### Install and Prepare the Demo Data

Run these commands from the project root:

```powershell
uv sync
Copy-Item .env.sample .env
uv run python scripts/seed_db.py
```

`uv sync` reads `pyproject.toml` and the committed `uv.lock`, creates or updates the project virtual environment, and installs the declared dependencies. Use `uv add package-name` for runtime dependencies and `uv add --dev package-name` for development-only dependencies; commit the resulting manifest and lockfile changes together.

`Copy-Item .env.sample .env` creates your private local configuration file. If `.env` already exists, keep it and edit it instead of overwriting it. The starter URL uses the included SQLite demo database.

### Start the API

```powershell
uv run uvicorn app.main:app --app-dir backend --reload
```

Then open `http://127.0.0.1:8000/docs` for the generated API documentation. The health route is `http://127.0.0.1:8000/health`.

### Select a Database URL

The project-root `.env` file sets `QUERYMIND_DATABASE_URL`, Groq settings, and `QUERYMIND_MAX_ROWS` for local development. Create it by copying the committed `.env.sample`; it is excluded from Git because it may contain credentials. The app loads it automatically; values already set in the process environment take precedence. The sample URL points to the included SQLite file, the API key is blank, the generation model is `llama-3.3-70b-versatile`, the judge model is `openai/gpt-oss-120b`, and the row limit defaults to 100 (the application caps this setting at 1,000).

To connect another database locally, edit `.env`, for example:

```dotenv
QUERYMIND_DATABASE_URL=sqlite:///./data/querymind.sqlite3
```

For Groq generation and OpenEvals judging, set a valid `GROQ_API_KEY`, an available `GROQ_MODEL`, and a Groq model supporting structured JSON output as `GROQ_GUARDRAIL_MODEL`. Both use the same Groq API key but may use different models. Adjust `QUERYMIND_MAX_ROWS` to a value from 1 through 1,000; values outside that range are clamped by the application. For PostgreSQL or MySQL, install the appropriate SQLAlchemy driver and use that database's URL format. Do not put real credentials in README examples, shell history, source files, or committed `.env.sample` files. The `/admin/schema` endpoint can inspect the configured connection when its driver and permissions allow it. Generated SQL is validated but not executed.

If a user clones the repository, they should run `Copy-Item .env.sample .env` from the project root, then edit `.env` for their setup. The `.env.sample` template is committed; the actual `.env` file is intentionally not committed.

## Validation Commands

Use this recommended sequence after setup.

### Recommended Sequence (CLI)

1. **Seed/reset demo data**
2. **Run core deterministic validators**
3. **Run API/admin validators**
4. **Run evaluation harness**
5. **(Optional) run live evaluation**

```powershell
# 1) Seed/reset deterministic demo data
uv run python scripts/seed_db.py

# 2) Core deterministic validators (safe default sequence)
uv run python scripts/validate_database.py
uv run python scripts/validate_schema_discovery.py
uv run python scripts/validate_query_classifier.py
uv run python scripts/validate_schema_linker.py
uv run python scripts/validate_example_retriever.py
uv run python scripts/validate_vector_retrieval.py
uv run python scripts/validate_model_config.py
uv run python scripts/validate_sql_generator.py
uv run python scripts/validate_sql_guardrails.py
uv run python scripts/validate_openevals_guardrails.py
uv run python scripts/validate_backend_api.py
uv run python scripts/validate_admin_backend.py
uv run python scripts/validate_admin_config.py
uv run python scripts/validate_query_pipeline.py

# 3) Offline evaluation (deterministic / no live LLM)
uv run python scripts/run_evaluation.py --output evaluations/report_offline.json --report evaluations/report_offline.md

# 4) Optional: live evaluation (requires configured provider credentials)
uv run python scripts/run_evaluation.py --live --output evaluations/report_live.json --report evaluations/report_live.md
```

### Same Sequence in Frontend (Admin → Script Runner)

Open **Script Runner** in the UI and run in this order:

1. **Seed demo database** (`seed-db`)
2. **All `validate-*` scripts** (start with `validate-database`, then schema/classifier/linker/retriever/generator/guardrails, then backend/admin/pipeline validations)
3. **Run SQL evaluation harness → Offline deterministic run**
4. **Run SQL evaluation harness → Live model run** (optional)

The Script Runner captures exit code, duration, stdout, and stderr for each run.

### Minimal Validation Set

If you want a quick pre-check before a larger run, use:

```powershell
uv run python scripts/seed_db.py
uv run python scripts/validate_database.py
uv run python scripts/validate_sql_guardrails.py
uv run python scripts/validate_backend_api.py
uv run python scripts/run_evaluation.py --output evaluations/report_offline.json --report evaluations/report_offline.md
```

Cross-verify QueryMind output directly against the SQLite fixture:

```powershell
uv run python scripts/verify_query.py --sql "SELECT status, COUNT(*) AS order_count FROM orders GROUP BY status ORDER BY order_count DESC"
uv run python scripts/verify_query.py --sql-file .\my_query.sql --output-csv .\tmp\verification.csv
```

## Actual Data Validation (Direct Query Result Check)

Use this section when you want to validate a real QueryMind response against the database records directly (not just script smoke checks).

### Steps

1. Run a question in QueryMind chat and copy the generated SQL (shown in the response details).
2. Execute the same SQL against the source database with [`scripts/verify_query.py`](scripts/verify_query.py).
3. Compare QueryMind output with direct SQL output: row count, ordering, and aggregate values.

### Command Patterns

```powershell
# Run copied SQL directly
uv run python scripts/verify_query.py --sql "SELECT p.product_name, SUM(oi.quantity) AS total_qty FROM order_items AS oi JOIN products AS p USING (product_id) GROUP BY p.product_name ORDER BY total_qty DESC LIMIT 10"

# Run SQL from file and export all rows to CSV for side-by-side comparison
uv run python scripts/verify_query.py --sql-file .\my_query.sql --output-csv .\tmp\querymind-cross-check.csv
```

### Sample Cross-Verification Queries

```powershell
# 1) "Give me top 10 products"
uv run python scripts/verify_query.py --sql "SELECT p.product_name, SUM(oi.quantity) AS total_quantity FROM order_items AS oi JOIN products AS p USING (product_id) GROUP BY p.product_name ORDER BY total_quantity DESC LIMIT 10"

# 2) "Give me in-transit products"
uv run python scripts/verify_query.py --sql "SELECT DISTINCT p.product_name FROM shipments AS s JOIN orders AS o USING (order_id) JOIN order_items AS oi USING (order_id) JOIN products AS p USING (product_id) WHERE s.status = 'in_transit' ORDER BY p.product_name"

# 3) Revenue by month
uv run python scripts/verify_query.py --sql "SELECT strftime('%Y-%m', o.order_date) AS month, SUM(oi.line_total_cents) AS revenue_cents FROM orders AS o JOIN order_items AS oi USING (order_id) GROUP BY month ORDER BY month"
```

If your local environment has package-lock issues with `uv run`, use `python scripts/verify_query.py ...` as a fallback.

`validate_schema_discovery.py` checks both the demo database and a separate in-memory departments/employees schema, including its foreign key. `validate_query_classifier.py` exercises every classifier category, edge cases such as "last name", and schema-based mention extraction against a different domain. These scripts are focused smoke/validation checks, not a replacement for broader end-to-end and UI automation.

`validate_schema_linker.py` checks description/synonym matching, table-context disambiguation, ambiguity reporting, and an API request against a separate billing/claims schema.

`validate_example_retriever.py` checks the curated library, ranking order, similarity bounds, top-k limits, no-match and empty-library behavior, and the retrieval API response (in deterministic lexical mode). `validate_vector_retrieval.py` checks FAISS/fastembed paraphrase retrieval, the similarity threshold and the lexical fallback; it needs the embedding model to be downloadable once.

`validate_sql_generator.py` checks prompt context (including dialect, schema, and foreign keys), example selection, mock-provider response handling, unsupported and ambiguous request handling, rejection of off-scope input and unrelated generated SQL, environment model selection, and missing-key errors. It never calls Groq. `validate_sql_guardrails.py` checks read-only/single-statement enforcement, schema and projection relevance, wildcard rejection, and row-limit insertion/clamping. Test a real generation request only after configuring a valid key and model in your private `.env`.

`validate_openevals_guardrails.py` checks typed verdict parsing, prompt-injection and PII rubric setup, generic API refusals, internal reason-code logging, input-before-generation ordering, output-before-response ordering, and fail-closed behavior for missing, malformed, or failing judges. It constructs real OpenEvals evaluator wrappers but injects fake evaluator functions for test runs, so it does not call Groq.

## Project Layout

```text
backend/app/          FastAPI application, routes, typed API models, and modular pipeline services
database/             SQLite demo schema
data/                 Locally generated SQLite database (ignored by Git)
examples/             Curated question-to-SQL examples used by retrieval
docs/                 Data dictionary and supporting project documentation
scripts/              Database seeder and database, API, schema, classifier, linker, retrieval, generation, OpenEvals, and access-policy validators
assignment_03_sql_agent.md
                      Original detailed assignment specification
Capstone_Submission_Instruction.pdf
                      Submission guide
pyproject.toml        Project metadata and dependency declarations for uv
uv.lock               Reproducible dependency lock generated by uv
pyrightconfig.json    Backend import path and supported Python analysis version
.vscode/settings.json Pylance backend import path for editor diagnostics
README.md             User guide and complete product implementation blueprint
.env.sample           Committed, secret-free local configuration template
.env                  Private local configuration (created from .env.sample; Git-ignored)
```

## Compliance Snapshot

This matrix maps the core assignment requirements to the current implementation status.

| Assignment requirement | Current status |
|---|---|
| End-to-end Text-to-SQL pipeline (classifier, linker, retriever, generator, validator, executor, formatter) | ✅ Implemented and script-validated |
| Domain database with seed data and minimum complexity constraints | ✅ Implemented and validated (`validate_database.py`) |
| FastAPI endpoints (`/query`, `/admin/schema`, `/admin/examples`, `/admin/logs`, `/admin/guardrails`, `/admin/config`, `/admin/scripts`) | ✅ Implemented (`/admin/model-config` kept as alias) |
| Next.js query chat + all 6 admin panels | ✅ Implemented (type-checked/build-checked) |
| Example library (≥30 question→SQL pairs) | ✅ Implemented (34 examples) |
| Evaluation harness on ≥25-query set with execution and exact-match metrics | ✅ Implemented (30-case set; offline + live reports) |
| Model Config provider switching (OpenAI/Anthropic/Ollama), temperature, token budget | ✅ Implemented |
| Dialect handling in Model Config | ✅ Implemented as active-DB execution dialect + optional `transpile_dialect` preview (`sqlglot.transpile`) |

Notes for reviewers:
- Generated SQL is always executed in the active database dialect to keep execution valid.
- `transpile_dialect` adds a second, rewritten SQL view for cross-dialect portability checks; it does not change execution target.
- Query explanation mode toggle is implemented in chat (`include_explanation` request flag).
- SQL execution self-healing is enabled: one retry regenerates SQL with execution error feedback.
- Chat includes a simple microphone input (Web Speech API; browser support varies) and semantic-response caching for near-identical questions.

## Expected Completion Checklist

- [x] Schema-driven pipeline with independently testable classifier, linker, retriever, generator, validator, executor, and formatter.
- [x] SQLite demo with at least six related tables, at least 1,000 rows per primary table, temporal fields, aggregation patterns, and documented ambiguity.
- [x] FastAPI query and admin APIs with typed contracts and persistent configuration.
- [x] Next.js query chat and six working admin panels.
- [x] At least 30 maintained examples and a 25-query ground-truth evaluation set.
- [x] Reproducible accuracy, semantic-match, and guardrail evaluation report.
- [x] Documentation of domain rationale, supported database/dialect combinations, setup, sample questions, architecture, security decisions, and limitations.
- [ ] Submission-ready GitHub repository with no secrets and a 5-10 minute demonstration video, as required by the capstone guide.

## Source Documents

- `assignment_03_sql_agent.md` is the detailed functional assignment and scoring rubric.
- `Capstone_Submission_Instruction.pdf` adds repository, documentation, architecture, demo, and presentation expectations.
- PPT deck path: `https://github.com/sandeepkaradegit/QueryMindAI/blob/main/QueryMindAI_Demo_Deck_Final.pptx`

When implementation changes, update this README in the same change so the status, commands, API behavior, and architecture remain accurate. When environment variables or their defaults change, update `.env.sample` and the corresponding README instructions in the same change. Never commit private values from `.env`, and do not overwrite a user's existing `.env` while applying configuration changes.