# QueryMind SQL Evaluation Report

- Mode: `live_llm`
- Model: `openai/gpt-oss-120b`
- Dataset: v1.0 (sha256 `eaf15de03f91`)
- Dialect: `sqlite`
- Total cases: 30 (25 answer cases with ground-truth SQL)
- Case pass rate: 76.7%
- **Execution accuracy:** 72.0%
- **Exact match rate (sqlglot):** 40.0%
- Context precision@3 / recall@3: 0.32 / 0.94

## Per-query-type breakdown

| Query type | Cases | Execution accuracy | Exact match |
|---|---|---|---|
| SELECT_AGGREGATE | 12 | 75.0% | 41.7% |
| SELECT_JOIN | 4 | 50.0% | 25.0% |
| SELECT_SIMPLE | 2 | 50.0% | 0.0% |
| SELECT_TEMPORAL | 7 | 85.7% | 57.1% |

## Failed cases

- `delivered-shipment-times`: checks {'execution_match': False, 'query_type_matches': True, 'retrieved_examples_match_response': True}
- `product-count-by-category`: checks {'execution_match': False, 'query_type_matches': True, 'retrieved_examples_match_response': True}
- `revenue-per-category-eval`: checks {'execution_match': False, 'query_type_matches': True, 'retrieved_examples_match_response': True}
- `category-names-eval`: checks {'execution_match': False, 'query_type_matches': True, 'retrieved_examples_match_response': True}
- `total-refunds-eval`: checks {'execution_match': False, 'query_type_matches': True, 'retrieved_examples_match_response': True}
- `refund-details-eval`: checks {'execution_match': False, 'query_type_matches': True, 'retrieved_examples_match_response': True}
- `line-items-eval`: checks {'execution_match': False, 'query_type_matches': True, 'retrieved_examples_match_response': True}
