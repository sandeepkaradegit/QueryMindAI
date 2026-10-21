# Assignment 3 — Text-to-SQL Analytics Engine with Admin UI
### QueryMind AI | Natural Language Analytics for Any Domain

---

## 🏢 Business Context

**Product:** QueryMind AI
**Target Users:** Business analysts, operations managers, and non-technical stakeholders
**Use Case:** Natural language querying over structured business databases

Enterprises spend an average of 3–5 days waiting for data analysts to fulfill ad-hoc SQL requests. Business stakeholders who understand their domain deeply are blocked by the technical barrier of SQL syntax. At the same time, data teams are overwhelmed with simple queries like "What were the top 10 products by revenue last quarter?" that take minutes to answer but hours to fulfill through ticketing systems.

**QueryMind AI** is a Text-to-SQL platform that lets any business user type a question in plain English (or their preferred language) and get accurate query results instantly — without writing a single line of SQL. The platform includes an **Admin UI** where data engineers can manage the database schema, curate few-shot examples, monitor query logs, and configure guardrails.

**Your role:** You are a full-stack ML engineer tasked with building a production-grade Text-to-SQL system. The platform must be domain-agnostic — learners are encouraged to choose their own database domain (e-commerce, healthcare, logistics, finance, etc.) and SQL dialect (PostgreSQL, MySQL, or SQLite).

**Why this matters:** Getting Text-to-SQL right is hard. The model must understand schema relationships, resolve ambiguous column names, handle aggregations and date arithmetic, and avoid generating destructive queries. This assignment covers the full pipeline from natural language understanding to safe SQL execution, with a feedback loop to improve accuracy over time.

---

## 🏗️ Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                         USER INTERFACE                             │
│              (Next.js — Query Chat + Admin Dashboard)              │
└──────────────────────────────┬─────────────────────────────────────┘
                               │ NL Query
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│                        FASTAPI BACKEND                             │
└──┬──────────────┬────────────────────────┬────────────────────────┘
   │              │                        │
   ▼              ▼                        ▼
┌──────────┐ ┌──────────────┐   ┌──────────────────────┐
│  Query   │ │   Schema     │   │    Examples          │
│Classifier│ │   Linker     │   │    Retriever         │
│          │ │              │   │  (Vector Search)     │
└──────────┘ └──────────────┘   └──────────────────────┘
      │              │                    │
      └──────────────┴────────────────────┘
                          │
                          ▼
             ┌────────────────────────┐
             │    SQL GENERATOR       │
             │  (LLM + few-shot ctx)  │
             └───────────┬────────────┘
                         │
                         ▼
             ┌────────────────────────┐
             │   SQL VALIDATOR        │
             │  (Syntax + Guardrails) │
             └───────────┬────────────┘
                         │
                         ▼
             ┌────────────────────────┐
             │    SQL EXECUTOR        │
             │  (Safe DB Connection)  │
             └───────────┬────────────┘
                         │
                         ▼
             ┌────────────────────────┐
             │   RESULT FORMATTER     │
             │  (Table + NL Summary)  │
             └────────────────────────┘
```

**Core Pipeline:**

`NL Query → Query Classifier → Schema Linker → Example Retriever → SQL Generator → SQL Validator → SQL Executor → Result Formatter`

---

## 🧩 Components

### 1. Database & Schema
- Learners choose their own domain and database (examples: e-commerce orders, hospital billing, logistics fleet, SaaS subscriptions)
- Minimum complexity requirements:
  - ≥ 6 tables with meaningful relationships (foreign keys, many-to-many joins)
  - ≥ 3 aggregate query patterns (SUM, COUNT, AVG with GROUP BY)
  - ≥ 2 date/time fields requiring temporal arithmetic
  - At least one schema ambiguity case (e.g., two columns that could mean "total revenue")
- Supported dialects: **SQLite** (default, easiest to set up), **PostgreSQL** (bonus), or **MySQL** (bonus)
- Include a seed script (`seed_db.py` or SQL file) with realistic synthetic data (minimum 1,000 rows per primary table)

### 2. Query Classifier
- Determines the intent of the user's question before generating SQL
- Categories: `SELECT_SIMPLE`, `SELECT_AGGREGATE`, `SELECT_JOIN`, `SELECT_TEMPORAL`, `UNSUPPORTED` (for UPDATE/DELETE/DDL)
- Unsupported intents return a helpful error without reaching the SQL generator
- Outputs: `{ query_type, tables_mentioned, columns_mentioned }`

### 3. Schema Linker
- Maps entities and column names mentioned in the NL query to the actual database schema
- Handles synonyms and aliases (e.g., "revenue" → `billing.total_amount`, "doctor" → `providers.name`)
- Uses the **Schema Manager** (see Admin UI) to look up human-readable column descriptions
- Outputs: `{ resolved_tables: [...], resolved_columns: [...], ambiguities: [...] }`

### 4. Examples Retriever
- Embeds the user's query and retrieves the top-k most similar (question → SQL) examples from the **Examples Manager**
- Uses ChromaDB or FAISS for vector search
- Returns 3–5 few-shot examples to include in the SQL Generator's prompt context
- Outputs: `{ examples: [{ question, sql }, ...] }`

### 5. SQL Generator
- Constructs a prompt containing: schema context, column descriptions, resolved entities, and few-shot examples
- Calls the LLM (Claude claude-sonnet-4-20250514 recommended) to generate the SQL query
- The prompt explicitly states the dialect and enforces SELECT-only generation
- Outputs: `{ sql: str, explanation: str }`

### 6. SQL Validator
- **Syntax check:** Parses the generated SQL using `sqlparse` or `sqlglot`
- **Guardrails check:** Rejects any query containing `DROP`, `DELETE`, `UPDATE`, `INSERT`, `TRUNCATE`, or `ALTER` unless the admin has toggled the relevant guardrail on
- **Schema check:** Verifies all referenced tables and columns exist in the actual schema
- **Row limit:** Appends `LIMIT N` if not present (configurable in Guardrails Config panel)
- If validation fails, the validator sends an error message back to the SQL Generator for one retry
- Outputs: `{ valid: bool, sanitized_sql: str, errors: [...] }`

### 7. SQL Executor
- Executes the validated SQL against the configured database connection
- Uses a read-only database role or connection string to prevent accidental writes
- Captures execution time (latency in ms) and row count returned
- Outputs: `{ rows: [...], columns: [...], row_count, latency_ms, error? }`

### 8. Result Formatter
- Converts raw query results into a user-friendly format:
  - Tabular view (rendered in the UI as a sortable data table)
  - 1–2 sentence natural language summary of the result (e.g., "The top product by revenue last quarter was Widget Pro at $142,000")
- Outputs: `{ table: [...], nl_summary: str }`

### 9. Admin UI — 5 Panels (Next.js)

| Panel | Description |
|---|---|
| **Schema Manager** | Browse all tables and columns; add human-readable descriptions per column; mark columns as sensitive (excluded from LLM context) |
| **Examples Manager** | Add, edit, and delete few-shot (question → SQL) pairs; semantic search across existing examples; label examples by query type |
| **Query Logs** | Full log of all user queries: NL input, generated SQL, execution status (success / error), latency, row count returned; filterable and exportable |
| **Guardrails Config** | Toggle: allow/block specific SQL operations; set max row return limit; restrict query scope to specific schemas or table prefixes |
| **Model Config** | Switch between LLM providers (OpenAI, Anthropic, Ollama); adjust temperature; select SQL dialect (SQLite / PostgreSQL / MySQL); set max token budget |

---

## 📦 Deliverables

1. **Text-to-SQL Pipeline** — end-to-end pipeline from NL query to formatted result, all 7 components implemented
2. **Database & Seed Data** — your chosen domain database with schema DDL, seed script, and a documented data dictionary
3. **FastAPI Backend** — REST API exposing: `/query` (NL → SQL → result), `/admin/schema`, `/admin/examples`, `/admin/logs`, `/admin/guardrails`, `/admin/config`
4. **Next.js Frontend** — query chat interface and all 5 Admin UI panels functional
5. **Few-shot Examples Library** — minimum 30 curated (question → SQL) pairs covering all query types in your schema
6. **Evaluation Report** — results of the SQL evaluation harness (see Evaluation Criteria) on a 25-query test set with ground-truth SQL
7. **README** — domain choice rationale, setup steps, sample queries, and known limitations

---

## 📊 Evaluation Criteria

| Criteria | Weight | Description |
|---|---|---|
| **Execution Accuracy** | 30% | % of generated SQL queries that return the same result set as the ground-truth SQL on the 25-query test set (exact row match) |
| **Exact Match Rate** | 15% | % of generated SQL queries that are syntactically identical or semantically equivalent to ground-truth SQL (checked with `sqlglot`) |
| **Guardrails Compliance** | 15% | 100% of destructive SQL attempts (DELETE, DROP, etc.) must be blocked; partial credit if some pass |
| **Admin UI Completeness** | 20% | All 5 panels render correctly and CRUD operations (add/edit/delete examples, toggle guardrails) persist to the backend |
| **Pipeline Modularity** | 10% | Each component is independently callable and testable; typed request/response schemas between components |
| **Documentation & Eval Report** | 10% | Evaluation harness is reproducible; report includes per-query-type breakdown of accuracy |

**Minimum passing threshold:** 65% overall weighted score, with Execution Accuracy ≥ 50% on the test set.

> **Evaluation Harness Note:** Unlike RAG evaluation (which uses RAGAS), Text-to-SQL uses a custom harness. Two metrics matter:
> - **Execution Accuracy:** Run both the generated SQL and ground-truth SQL; compare result sets row-by-row
> - **Exact Match Rate:** Normalize both SQL strings (strip whitespace, lowercase keywords) and compare, or use `sqlglot.diff()` for semantic comparison

---

## 🌟 Bonus Challenges

- **Multi-dialect Support:** Add a dialect toggle in Model Config that rewrites the generated SQL for a second dialect using `sqlglot.transpile()`
- **Ambiguity Resolution:** When the Schema Linker detects ambiguous column references, surface a clarification prompt to the user before generating SQL ("Did you mean `billing.amount` or `claims.amount`?")
- **Query Explanation Mode:** Add a toggle in the chat UI that shows a plain English breakdown of the generated SQL ("This query joins the orders table with customers, filters to last quarter, and sums the revenue column")
- **Self-healing SQL:** If SQL execution fails (e.g., type mismatch), automatically retry once with the error message injected back into the LLM prompt
- **Chart Auto-generation:** If the result set has ≤ 2 columns and one is numeric, automatically render a bar or line chart alongside the table in the UI
- **Semantic Caching:** Cache NL queries and their SQL results using embedding similarity so repeated or near-identical questions don't consume LLM tokens
- **Voice Input:** Add a microphone button in the query UI that uses the Web Speech API to transcribe spoken questions into text
