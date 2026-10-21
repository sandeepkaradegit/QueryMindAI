# QueryMind SQL Evaluation Report

- Mode: `live_llm`
- Model: `openai/gpt-oss-120b`
- Dataset: v1.0 (sha256 `eaf15de03f91`)
- Dialect: `sqlite`
- Total cases: 30 (25 answer cases with ground-truth SQL)
- Case pass rate: 83.3%
- **Execution accuracy:** 80.0%
- **Exact match rate (sqlglot):** 44.0%
- Context precision@3 / recall@3: 0.32 / 0.94

## Per-query-type breakdown

| Query type | Cases | Execution accuracy | Exact match |
|---|---|---|---|
| SELECT_AGGREGATE | 12 | 83.3% | 50.0% |
| SELECT_JOIN | 4 | 75.0% | 25.0% |
| SELECT_SIMPLE | 2 | 50.0% | 50.0% |
| SELECT_TEMPORAL | 7 | 85.7% | 42.9% |

## Failed cases

- `delivered-shipment-times`: checks {'execution_match': False, 'query_type_matches': True, 'retrieved_examples_match_response': True}
- `product-count-by-category`: checks {'execution_match': False, 'query_type_matches': True, 'retrieved_examples_match_response': True}
- `revenue-per-category-eval`: checks {'execution_match': False, 'query_type_matches': True, 'retrieved_examples_match_response': True}
- `category-names-eval`: checks {'execution_match': False, 'query_type_matches': True, 'retrieved_examples_match_response': True}
- `line-items-eval`: checks {'execution_match': False, 'query_type_matches': True, 'retrieved_examples_match_response': True}
