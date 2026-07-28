# AgentHub Evaluation Report

- Mode: `offline`
- Gate passed: `True`

## Metrics

| Metric | Value |
| --- | ---: |
| `average_tool_rounds` | 1.0000 |
| `context_truncation_rate` | 0.0500 |
| `planner_fallback_rate` | 0.0500 |
| `planner_schema_success_rate` | 1.0000 |
| `retrieval_mrr` | 0.2356 |
| `retrieval_recall_at_5` | 0.8000 |
| `tool_call_success_rate` | 1.0000 |
| `verification_pass_rate` | 1.0000 |

## Failed cases

### retrieval-001

- Type: `retrieval`
- Reason: expected_path_not_in_top_5
- Expected: ["app/agents/tool_calling.py"]
- Actual: ["agenthub-backend.md", "agenthub_native_tool_calling_codex_plan.md", "agenthub_mcp_refactor_plan.md", "agenthub_native_tool_calling_codex_plan.md", "agenthub_mcp_refactor_plan.md"]

### retrieval-012

- Type: `retrieval`
- Reason: expected_path_not_in_top_5
- Expected: ["app/agents/graph/workflow.py"]
- Actual: ["app/agents/graph/runtime.py", "evals/cases/retrieval_cases.jsonl", "docs/superpowers/plans/agenthub_langgraph_hitl_codex_plan.md", "tests/test_langgraph_interrupt_resume.py", "agenthub_mcp_refactor_plan.md"]

### retrieval-015

- Type: `retrieval`
- Reason: expected_path_not_in_top_5
- Expected: ["app/agents/tool_calling.py"]
- Actual: ["evals/cases/retrieval_cases.jsonl", "tests/test_agent_evaluation.py", "agenthub_native_tool_calling_codex_plan.md", "tests/test_verification_service.py", "agenthub_mcp_refactor_plan.md"]
