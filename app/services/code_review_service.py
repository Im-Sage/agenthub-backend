import json
import re

from sqlalchemy.orm import Session

from app.models.code_change import CodeChange
from app.models.code_review import CodeReview


HIGH_RISK_PATTERNS = [
    (re.compile(r"(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]+", re.IGNORECASE), "Possible hard-coded secret."),
    (re.compile(r"\beval\s*\(", re.IGNORECASE), "Use of eval-like execution."),
    (re.compile(r"dangerouslySetInnerHTML|innerHTML\s*=", re.IGNORECASE), "Potential unsafe HTML injection."),
    (re.compile(r"\bexec\s*\(|subprocess\.", re.IGNORECASE), "Shell or process execution introduced."),
]

MEDIUM_RISK_PATTERNS = [
    (re.compile(r"\b(auth|login|register|jwt|session|permission|role)\b", re.IGNORECASE), "Authentication or authorization code changed."),
    (re.compile(r"\b(sql|query|select|insert|update|delete)\b", re.IGNORECASE), "Database query logic changed."),
    (re.compile(r"\b(localStorage|sessionStorage|cookie)\b", re.IGNORECASE), "Client-side storage or cookie handling changed."),
]


def _added_lines(diff_text: str) -> list[str]:
    return [
        line[1:]
        for line in diff_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]


def _removed_lines(diff_text: str) -> list[str]:
    return [
        line[1:]
        for line in diff_text.splitlines()
        if line.startswith("-") and not line.startswith("---")
    ]


def _changed_files(code_change: CodeChange) -> list[str]:
    try:
        files = json.loads(code_change.changed_files)
    except json.JSONDecodeError:
        return []
    if not isinstance(files, list):
        return []
    return [str(item) for item in files]


def _has_test_file(files: list[str]) -> bool:
    return any(
        "test" in file.lower()
        or file.lower().endswith((".spec.ts", ".spec.tsx", ".test.ts", ".test.tsx", "_test.py"))
        for file in files
    )


def generate_code_review(db: Session, code_change: CodeChange) -> CodeReview:
    files = _changed_files(code_change)
    added_lines = _added_lines(code_change.diff_text)
    removed_lines = _removed_lines(code_change.diff_text)
    added_text = "\n".join(added_lines)

    findings: list[dict[str, str]] = []
    recommendations: list[str] = []
    risk_score = 0

    for pattern, message in HIGH_RISK_PATTERNS:
        if pattern.search(added_text):
            findings.append({"severity": "high", "message": message})
            risk_score += 3

    for pattern, message in MEDIUM_RISK_PATTERNS:
        if pattern.search(added_text):
            findings.append({"severity": "medium", "message": message})
            risk_score += 1

    if len(files) >= 8:
        findings.append({"severity": "medium", "message": f"Large change touches {len(files)} files."})
        risk_score += 1

    if added_lines and not _has_test_file(files):
        findings.append({"severity": "low", "message": "No test file changes detected."})
        recommendations.append("Add or update focused tests for the changed behavior.")

    if not findings:
        findings.append({"severity": "low", "message": "No obvious rule-based risks detected."})

    if any(finding["severity"] == "high" for finding in findings):
        recommendations.append("Review the highlighted high-risk lines before accepting or creating a PR.")

    if any("Authentication" in finding["message"] for finding in findings):
        recommendations.append("Verify unauthorized, expired-session, and invalid-input cases manually or with tests.")

    if not recommendations:
        recommendations.append("Run the relevant app/test suite before merging.")

    if risk_score >= 3:
        risk_level = "high"
    elif risk_score >= 1:
        risk_level = "medium"
    else:
        risk_level = "low"

    summary = (
        f"Reviewed {len(files)} file(s), {len(added_lines)} added line(s), "
        f"{len(removed_lines)} removed line(s). Risk level: {risk_level}."
    )

    review = CodeReview(
        code_change_id=code_change.id,
        task_id=code_change.task_id,
        repository_id=code_change.repository_id,
        status="completed",
        risk_level=risk_level,
        summary=summary,
        findings_json=json.dumps(findings, ensure_ascii=False),
        recommendations_json=json.dumps(recommendations, ensure_ascii=False),
        raw_output=None,
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return review
