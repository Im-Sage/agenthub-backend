import json
from pathlib import Path


def markdown_report(report: dict) -> str:
    lines = [
        "# AgentHub Evaluation Report",
        "",
        f"- Mode: `{report['mode']}`",
        f"- Gate passed: `{report['gate_passed']}`",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    for name, value in sorted(report["metrics"].items()):
        lines.append(f"| `{name}` | {value:.4f} |")
    lines.extend(["", "## Failed cases", ""])
    failures = report.get("failed_cases", [])
    if not failures:
        lines.append("None.")
    for failure in failures:
        lines.extend(
            [
                f"### {failure['id']}",
                "",
                f"- Type: `{failure['type']}`",
                f"- Reason: {failure['reason']}",
                "- Expected: "
                + json.dumps(
                    failure.get("expected"),
                    ensure_ascii=False,
                ),
                "- Actual: "
                + json.dumps(
                    failure.get("actual"),
                    ensure_ascii=False,
                ),
                "",
            ]
        )
    if report.get("threshold_failures"):
        lines.extend(["## Threshold failures", ""])
        lines.extend(
            f"- {failure}"
            for failure in report["threshold_failures"]
        )
    return "\n".join(lines).rstrip() + "\n"


def write_report(
    report: dict,
    output_path: str | Path,
) -> tuple[Path, Path]:
    json_path = Path(output_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path = json_path.with_suffix(".md")
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(
        markdown_report(report),
        encoding="utf-8",
    )
    return json_path, markdown_path
