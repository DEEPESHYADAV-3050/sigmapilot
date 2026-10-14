from __future__ import annotations
import json
from typing import Any


def analysis_markdown(filename: str, config: dict, result: dict, created_at: str) -> str:
    lines = [f"# Six Sigma Analysis Report: {filename}", "", f"**Created:** {created_at}", "", "## Analysis configuration", "", f"- Measurement column: `{config.get('value_column')}`", f"- LSL: `{config.get('lsl')}`", f"- USL: `{config.get('usl')}`", "", "## Calculated results", ""]
    for key in ["n", "mean", "median", "sample_std_dev", "min", "max", "q1", "q3", "iqr", "missing_or_nonfinite_count"]:
        if key in result: lines.append(f"- **{key.replace('_', ' ').title()}:** {result[key]}")
    spec = result.get("specification")
    if spec:
        lines += ["", "### Specification and capability", ""]
        for key, value in spec.items():
            if key != "assumptions": lines.append(f"- **{key.replace('_', ' ').title()}:** {value}")
    if result.get("defects"):
        lines += ["", "### Defect metrics", ""]
        for key, value in result["defects"].items():
            if key != "assumptions": lines.append(f"- **{key.replace('_', ' ').title()}:** {value}")
    lines += ["", "## Diagnostics", "", f"- **Normality diagnostic:** {json.dumps(result.get('normality', {}), ensure_ascii=False)}", f"- **I-MR diagnostic:** {json.dumps(result.get('imr', {}), ensure_ascii=False)}", "", "## Assumptions and limitations", ""]
    for note in result.get("evidence_notes", []): lines.append(f"- {note}")
    for group in [spec or {}, result.get("defects") or {}]:
        for note in group.get("assumptions", []): lines.append(f"- {note}")
    quality = result.get("data_quality") or {}
    if quality:
        lines += ["", "## Raw-data quality audit", "", f"- **Screening status:** {quality.get('status')}", f"- **Rows / columns:** {quality.get('row_count')} / {quality.get('column_count')}", f"- **Exact duplicate rows (not removed):** {quality.get('exact_duplicate_rows')}", f"- **Measurement valid finite rows:** {(quality.get('measurement') or {}).get('valid_finite_numeric_count')}", f"- **Measurement invalid/missing rows:** {(quality.get('measurement') or {}).get('invalid_or_missing_count')}", "", "### Data-quality warnings", ""]
        lines += [f"- {warning}" for warning in quality.get("warnings", [])] or ["- No automated screening warnings were detected."]
        lines += ["", f"**Scope limitation:** {quality.get('scope_note', 'Automated screening is not a full data validation study.')}"]
    validation = result.get("calculation_validation") or {}
    if validation:
        lines += ["", "## Independent calculation cross-check", "", f"- **Status:** {validation.get('status')}", f"- **Failed checks:** {validation.get('failed_check_count', 0)}", ""]
        for check in validation.get("checks", []):
            lines.append(f"- **{check.get('metric')} — {check.get('status')}:** reported `{check.get('reported')}`, independently recomputed `{check.get('independently_recomputed')}`" + (f"; {check.get('reason')}" if check.get('reason') else ""))
        lines += ["", validation.get("note", "Cross-checks cover selected arithmetic only.")]
    lines += ["", "## Evidence discipline", "", "These metrics are computed from the selected local dataset and the supplied configuration. Capability indices do not prove process stability. Observed rates describe this sample and are not guarantees of future performance. AI-generated hypotheses, if any, are not statistical findings.", ""]
    return "\n".join(lines)


def agent_markdown(project: dict | None, agent_result: dict) -> str:
    title = project.get("name") if project else "Ad-hoc DMAIC analysis"
    lines = [f"# SigmaPilot Agent Report — {title}", "", f"**Run ID:** {agent_result.get('run_id', 'unknown')}", f"**Status:** {agent_result.get('status', 'unknown')}", "", "## Verified findings", ""]
    findings = agent_result.get("verified_findings", [])
    lines += [f"- {f['text']} (evidence: `{f.get('source', 'calculation')}`)" for f in findings] or ["- No verified findings were available for this run."]
    lines += ["", "## AI interpretation (not independently verified)", "", agent_result.get("ai_interpretation", {}).get("summary", "No AI interpretation was generated.")]
    for section in ["hypotheses", "recommendations", "risks"]:
        lines += ["", f"### {section.title()}", ""]
        values = agent_result.get("ai_interpretation", {}).get(section, [])
        lines += [f"- {v}" for v in values] or ["- None provided."]
    lines += ["", "## DMAIC workflow trace", ""]
    for item in agent_result.get("trace", []): lines.append(f"- **{item.get('stage')} / {item.get('status')}:** {item.get('message')}")
    stats_context = (agent_result.get("analysis_context") or {}).get("statistics") or {}
    quality = stats_context.get("data_quality") or {}
    if quality:
        lines += ["", "## Raw-data quality audit", "", f"- **Status:** {quality.get('status')}", f"- **Rows:** {quality.get('row_count')}", f"- **Exact duplicate rows (not removed):** {quality.get('exact_duplicate_rows')}"]
        lines += [f"- Warning: {warning}" for warning in quality.get("warnings", [])] or ["- No automated screening warnings were detected."]
    validation = stats_context.get("calculation_validation") or {}
    if validation:
        lines += ["", "## Calculation validation", "", f"- **Status:** {validation.get('status')}", f"- **Failed checks:** {validation.get('failed_check_count', 0)}"]
        lines += [f"- {check.get('metric')}: {check.get('status')} (reported={check.get('reported')}; recomputed={check.get('independently_recomputed')})" for check in validation.get("checks", [])]
    lines += ["", "## Evidence limitations", "", "Recommendations are not proof of cause or improvement. Confirm root causes with appropriate studies and verify improvements using post-change data before claiming a benefit.", ""]
    return "\n".join(lines)
