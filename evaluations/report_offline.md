# QueryMind SQL Evaluation Report

- Mode: `offline_fixture_pipeline_validation`
- Model: `deterministic-evaluation-fixture`
- Dataset: v1.0 (sha256 `eaf15de03f91`)
- Dialect: `sqlite`
- Total cases: 30 (25 answer cases with ground-truth SQL)
- Case pass rate: 96.7%
- **Execution accuracy:** 96.0%
- **Exact match rate (sqlglot):** 96.0%
- Context precision@3 / recall@3: 0.32 / 0.94

## Per-query-type breakdown

| Query type | Cases | Execution accuracy | Exact match |
|---|---|---|---|
| SELECT_AGGREGATE | 12 | 91.7% | 91.7% |
| SELECT_JOIN | 4 | 100.0% | 100.0% |
| SELECT_SIMPLE | 2 | 100.0% | 100.0% |
| SELECT_TEMPORAL | 7 | 100.0% | 100.0% |

## Failed cases

- `total-refunds-eval`: checks {'expected_action': False, 'expected_error': False, 'provider_not_called': False}
