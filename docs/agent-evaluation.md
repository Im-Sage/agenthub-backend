# Agent Evaluation Guide

AgentHub 的评测目标是让 Planner、Retrieval、Context、Tool 与 Verification 变成可重复回归，而不是只依赖一次成功演示。

## Offline gate

```powershell
$env:PYTHONPATH='.'
python -m evals.run --mode offline --output evals/reports/latest.json
```

命令不访问外部 LLM。它使用：

- 至少 20 条 Planner case 和 Fake Structured Planner；
- 至少 15 条本仓库真实符号 Retrieval case；
- `HashEmbeddingProvider` 建立临时仓库索引；
- Fake CommandRunner 产生确定性 Tool/Verification 结果；
- 固定指标与阈值决定退出码。

同时生成 `latest.json` 与 `latest.md`。Markdown 列出总体指标、失败 case、期望路径、实际 Top-K 和 planner fallback 原因。

## Metrics and thresholds

| Metric | Definition | Offline gate |
| --- | --- | ---: |
| planner_schema_success_rate | 可被 `OrchestratorPlan` 校验的比例 | = 1.0 |
| planner_fallback_rate | 使用安全 fallback 的比例 | report only |
| retrieval_recall_at_5 | expected paths 在 Top-5 中的命中比例 | >= 0.80 |
| retrieval_mrr | 第一个相关路径排名倒数的均值 | report only |
| context_truncation_rate | 触发预算截断的 case 比例 | <= 0.30 |
| tool_call_success_rate | fake tool 调用成功比例 | = 1.0 |
| verification_pass_rate | fake verification 成功比例 | = 1.0 |
| average_tool_rounds | 每 case 平均工具轮数 | report only |

阈值不通过时 runner 返回 1，适合作为 CI smoke gate。失败 case 可以存在，只要总体门槛仍通过；报告必须保留它们以防指标掩盖具体退化。

## Live mode

```powershell
python -m evals.run --mode live --output evals/reports/live.json
```

Live mode 仅在配置 API key 时允许执行。缺少 key 会生成明确的 skipped 报告并返回 0，因此“没有外部凭证”不会被误判为代码回归。Live 结果不应覆盖 offline 基线。

## Adding cases

Planner JSONL：

```json
{"id":"planner-021","instruction":"...","expected_agents":["backend"],"min_steps":1,"max_steps":3}
```

Retrieval JSONL：

```json
{"id":"retrieval-016","query":"class ExampleService:","expected_paths":["app/services/example.py"]}
```

新增 Retrieval case 应指向真实、已跟踪文件，并优先描述实际开发意图；若使用定义签名，应再保留一定比例的自然语言语义 case。修改数据集或排序算法后必须重新生成并审查 JSON/Markdown 报告。
