from __future__ import annotations
import json
import urllib.error
import urllib.request
import uuid
from typing import Any
from ..config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT_SECONDS
from ..db import connection, json_dump, now_iso, audit
from .ingestion import frame_for_dataset
from .statistics import analyze_measurements, defect_metrics, welch_two_sample_test

STAGES = ["Define", "Measure", "Analyze", "Improve", "Control"]


def _llm_interpret(context: dict, prompt: str) -> dict:
    if not LLM_API_KEY:
        return {
            "provider_status": "not_configured",
            "summary": "AI provider is not configured. The run completed using deterministic calculations and a conservative template; no model-generated claims were made.",
            "hypotheses": ["Investigate plausible process drivers with stratification, process mapping, and direct observation; no root cause is established by summary statistics alone."],
            "recommendations": ["Validate the measurement system and data definitions before acting on capability or defect metrics.", "Choose one testable change, define a primary outcome and guardrail, and compare pre/post data using an appropriate study design.", "Continue collecting time-ordered observations and review control-chart signals before treating capability as predictive."],
            "risks": ["The available dataset may not represent the full process or current operating conditions.", "Correlation or a group difference alone does not establish causation."],
            "evidence_policy": "All statements in this section are advisory templates, not measured findings."
        }
    system = (
        "You are SigmaPilot, a cautious Six Sigma improvement analyst. You receive already-computed statistical evidence. "
        "Never invent metrics, sample sizes, causes, results, or source citations. Separate hypotheses from verified findings. "
        "Do not claim an intervention worked without post-change evidence. Do not treat a p-value as practical importance or causation. "
        "Return ONLY valid JSON with keys summary (string), hypotheses (array of strings), recommendations (array of strings), risks (array of strings). "
        "Do not restate numeric results unless they appear verbatim in supplied evidence. If evidence is inadequate, say so."
    )
    body = {
        "model": LLM_MODEL,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({"user_request": prompt, "verified_context": context}, ensure_ascii=False)}
        ]
    }
    req = urllib.request.Request(
        f"{LLM_BASE_URL}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {LLM_API_KEY}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT_SECONDS) as response:
            raw = json.loads(response.read().decode("utf-8"))
        content = raw["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("Model content was not a text JSON response.")
        candidate = content.strip()
        if candidate.startswith("```"):
            candidate = candidate.split("\n", 1)[-1]
            if candidate.endswith("```"):
                candidate = candidate[:-3].strip()
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            first, last = candidate.find("{"), candidate.rfind("}")
            if first < 0 or last <= first:
                raise
            parsed = json.loads(candidate[first:last + 1])
        def strings(key):
            val = parsed.get(key, [])
            return [str(x)[:1500] for x in val[:12]] if isinstance(val, list) else []
        return {
            "provider_status": "configured_response",
            "model": LLM_MODEL,
            "summary": str(parsed.get("summary", "No summary returned."))[:5000],
            "hypotheses": strings("hypotheses"), "recommendations": strings("recommendations"), "risks": strings("risks"),
            "evidence_policy": "Model-generated interpretation; not independently verified as fact. Numeric findings are reported separately from deterministic calculations."
        }
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {
            "provider_status": "provider_error_fallback",
            "summary": "The configured AI provider could not return a valid response. The deterministic workflow completed and a safe template is shown instead.",
            "hypotheses": ["No root cause has been established. Use stratification and a designed follow-up study to test candidate causes."],
            "recommendations": ["Review data definitions and measurement-system reliability.", "Define an intervention, primary metric, guardrail, and verification period before implementation."],
            "risks": [f"AI provider error: {type(exc).__name__}. No provider response was treated as evidence."],
            "evidence_policy": "Fallback template; no model-generated claims were accepted."
        }


def _get_analysis(analysis_run_id: str | None) -> tuple[dict | None, dict | None]:
    if not analysis_run_id: return None, None
    with connection() as conn:
        row = conn.execute("SELECT * FROM analysis_runs WHERE id=?", (analysis_run_id,)).fetchone()
        ds = conn.execute("SELECT filename FROM datasets WHERE id=?", (row["dataset_id"],)).fetchone() if row else None
    if not row: return None, None
    return json.loads(row["result_json"]), {"id": row["dataset_id"], "filename": ds["filename"] if ds else "Unknown dataset", "config": json.loads(row["config_json"])}


def _verified_findings(analysis: dict | None, dataset_info: dict | None) -> list[dict]:
    findings = []
    if not analysis:
        return findings
    src = f"analysis:{dataset_info['id']}" if dataset_info else "deterministic_analysis"
    for key, label in [("n", "Finite observations"), ("mean", "Sample mean"), ("median", "Sample median"), ("sample_std_dev", "Sample standard deviation")]:
        if analysis.get(key) is not None:
            findings.append({"id": f"finding_{key}", "text": f"{label}: {analysis[key]}", "source": src, "kind": "calculated"})
    spec = analysis.get("specification") or {}
    if spec:
        findings.append({"id": "finding_oos", "text": f"Observed out-of-spec observations: {spec.get('out_of_spec_count')} of {analysis.get('n')} ({spec.get('out_of_spec_rate')}).", "source": src, "kind": "calculated", "assumption": "Observed sample rate only; not a future performance guarantee."})
        if spec.get("cpk") is not None:
            findings.append({"id": "finding_cpk", "text": f"Cpk estimate: {spec['cpk']} (Cp: {spec.get('cp')}).", "source": src, "kind": "calculated", "assumption": "Within sigma estimated from moving range; interpret only after process stability and distribution assumptions are reviewed."})
    defects = analysis.get("defects") or {}
    if defects.get("yield") is not None:
        findings.append({"id": "finding_yield", "text": f"Observed unit yield: {defects['yield']}.", "source": src, "kind": "calculated", "assumption": "Each row represents one unit and the selected flag indicates a defective unit."})
    if defects.get("dpmo") is not None:
        findings.append({"id": "finding_dpmo", "text": f"DPMO estimate: {defects['dpmo']}.", "source": src, "kind": "calculated", "assumption": "Based on supplied defect counts and opportunities per unit."})
    imr = analysis.get("imr") or {}
    if imr.get("status") == "calculated":
        count = len(imr.get("individual_signals", [])) + len(imr.get("moving_range_signals", []))
        findings.append({"id": "finding_imr", "text": f"I-MR 3-sigma limit signals flagged: {count}.", "source": src, "kind": "diagnostic", "assumption": "Row order is treated as process order; only basic limit rules are implemented."})
    quality = analysis.get("data_quality") or {}
    if quality:
        findings.append({"id": "finding_data_quality", "text": f"Raw-data screening status: {quality.get('status')}; {quality.get('row_count')} rows, {quality.get('exact_duplicate_rows')} exact duplicate rows, {len(quality.get('warnings', []))} warning(s).", "source": src, "kind": "data_quality", "assumption": quality.get("scope_note")})
        for idx, warning in enumerate(quality.get("warnings", [])[:8], start=1):
            findings.append({"id": f"finding_data_quality_warning_{idx}", "text": warning, "source": src, "kind": "limitation"})
    validation = analysis.get("calculation_validation") or {}
    if validation:
        findings.append({"id": "finding_calculation_validation", "text": f"Independent arithmetic cross-check status: {validation.get('status')}; {validation.get('failed_check_count', 0)} failed check(s).", "source": src, "kind": "validation"})
        for check in validation.get("checks", []):
            if check.get("status") == "failed":
                findings.append({"id": f"finding_calc_failed_{check.get('metric')}", "text": f"Calculation mismatch for {check.get('metric')}: reported {check.get('reported')} versus recomputed {check.get('independently_recomputed')}.", "source": src, "kind": "validation_error"})
    return findings


def _stage_content(stage: str, analysis: dict | None, prompt: str) -> dict:
    if stage == "Define":
        return {"message": "Scope and problem framing recorded. The problem statement is not treated as proven until supported by baseline data.", "deliverables": ["Define the customer/process boundary", "Record problem, goal, scope, stakeholders, and expected benefit", "Avoid setting a target without an agreed baseline"]}
    if stage == "Measure":
        deliverables = ["Check sample size, missingness, units, and operational definitions", "Review measurement-system reliability", "Establish a baseline and sampling plan"]
        if analysis:
            deliverables.insert(0, f"Review the calculated baseline for {analysis.get('n')} finite observations")
            if analysis.get("missing_or_nonfinite_count", 0):
                deliverables.append(f"Resolve or document {analysis['missing_or_nonfinite_count']} missing/non-finite measurement values")
        else:
            deliverables.insert(0, "No numeric baseline is attached; collect and validate time-ordered process data before drawing numeric conclusions")
        return {"message": "Measured evidence reviewed. No measurement is fabricated when data is absent.", "deliverables": deliverables, "data_available": bool(analysis)}
    if stage == "Analyze":
        deliverables = ["Stratify by time, shift, product, supplier, or relevant process factors", "Use a fishbone/5-Whys as hypothesis generation, not proof", "Validate candidate causes with appropriate evidence or a designed test"]
        messages = ["Deterministic findings are separated from potential causes. Descriptive data alone does not establish root cause."]
        if analysis:
            imr = analysis.get("imr") or {}
            signal_count = len(imr.get("individual_signals", [])) + len(imr.get("moving_range_signals", []))
            if signal_count:
                messages.append(f"The basic I-MR diagnostic flagged {signal_count} limit signal(s); investigate time/order, data quality, and potential special causes before interpreting capability.")
                deliverables.insert(0, "Review each flagged point against the production timeline; signals do not identify a cause by themselves")
            normality = analysis.get("normality") or {}
            if normality.get("status") == "calculated" and normality.get("p_value", 1) < 0.05:
                deliverables.append("Shapiro–Wilk detected evidence against normality at alpha 0.05; assess distribution shape and an appropriate analysis method")
            spec = analysis.get("specification") or {}
            if spec.get("out_of_spec_count", 0) > 0:
                deliverables.append(f"Investigate the {spec['out_of_spec_count']} observed out-of-spec point(s) using stratification and process knowledge")
        return {"message": " ".join(messages), "deliverables": deliverables}
    if stage == "Improve":
        deliverables = ["Prioritize reversible, low-risk changes tied to a validated cause", "Predefine primary outcome, guardrails, duration, and owner", "Use a pilot or controlled comparison where feasible"]
        spec = (analysis or {}).get("specification") or {}
        if spec.get("out_of_spec_count", 0) > 0:
            deliverables.insert(0, "Prioritize a controlled investigation of observed out-of-spec cases; do not assume a cause from the metric alone")
        elif analysis and spec:
            deliverables.insert(0, "No out-of-spec values were observed in this sample; use a planned pilot and sufficient follow-up rather than assuming the process is permanently capable")
        return {"message": "Improvement actions are proposed as experiments, not claimed as successful changes.", "deliverables": deliverables}
    deliverables = ["Set owner, metric, sampling frequency, limits, and escalation rule", "Continue time-ordered SPC monitoring", "Compare pre/post data and document sustained effect before claiming savings"]
    if analysis:
        imr = analysis.get("imr") or {}
        signal_count = len(imr.get("individual_signals", [])) + len(imr.get("moving_range_signals", []))
        if signal_count:
            deliverables.insert(0, "Keep the process under review and investigate flagged points before relying on capability indices")
        else:
            deliverables.insert(0, "No basic I-MR limit signals were flagged in this sample; continue monitoring because absence of signals is not proof of control")
    return {"message": "A control plan is proposed; the process is not considered improved until post-change evidence confirms it.", "deliverables": deliverables}


def create_agent_run(payload: dict) -> dict:
    run_id = str(uuid.uuid4()); now = now_iso()
    with connection() as conn:
        conn.execute("INSERT INTO agent_runs(id,project_id,dataset_id,analysis_run_id,prompt,status,current_stage,completed_stages_json,result_json,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (run_id, payload.get("project_id"), payload.get("dataset_id"), payload.get("analysis_run_id"), payload.get("prompt", ""), "queued", "Define", "[]", "{}", "", now, now))
    audit("agent.run_created", "agent_run", run_id, {"project_id": payload.get("project_id"), "dataset_id": payload.get("dataset_id")})
    return advance_agent_run(run_id)


def advance_agent_run(run_id: str, prompt_override: str | None = None) -> dict:
    with connection() as conn:
        row = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise KeyError("Agent run not found.")
    completed = json.loads(row["completed_stages_json"])
    result = json.loads(row["result_json"] or "{}")
    trace = result.get("trace", [])
    prompt = prompt_override or row["prompt"]
    analysis = None
    dataset_info = None
    try:
        analysis, dataset_info = _get_analysis(row["analysis_run_id"])
        if analysis is None and row["dataset_id"]:
            frame = frame_for_dataset(row["dataset_id"])
            numeric = frame.select_dtypes(include="number").columns.tolist()
            if numeric:
                col = numeric[0]
                analysis = analyze_measurements(frame[col].tolist())
                with connection() as conn:
                    dataset_row = conn.execute("SELECT filename FROM datasets WHERE id=?", (row["dataset_id"],)).fetchone()
                dataset_info = {"id": row["dataset_id"], "filename": dataset_row["filename"] if dataset_row else "Unknown dataset", "config": {"value_column": str(col)}}
            elif not any(item.get("stage") == "Measure" and item.get("status") == "warning" for item in trace):
                trace.append({"stage": "Measure", "status": "warning", "message": "Dataset has no numeric columns; no numeric statistical findings were generated."})
        with connection() as conn:
            conn.execute("UPDATE agent_runs SET status='running',error='',updated_at=? WHERE id=?", (now_iso(), run_id))
        for stage in STAGES:
            if stage in completed:
                continue
            output = _stage_content(stage, analysis, prompt)
            trace.append({"stage": stage, "status": "completed", "message": output["message"], "deliverables": output["deliverables"], "data_available": output.get("data_available")})
            completed.append(stage)
            result.update({"trace": trace, "completed_stages": completed, "verified_findings": _verified_findings(analysis, dataset_info), "analysis_context": {"dataset": dataset_info, "statistics": analysis} if analysis else None})
            with connection() as conn:
                conn.execute("UPDATE agent_runs SET status='running',current_stage=?,completed_stages_json=?,result_json=?,updated_at=? WHERE id=?", (stage, json_dump(completed), json_dump(result), now_iso(), run_id))
        if "AIInterpretation" not in completed:
            context = {"dataset": dataset_info, "calculated_statistics": analysis, "verified_findings": result.get("verified_findings", []), "dmaic_trace": trace}
            interpretation = _llm_interpret(context, prompt)
            result["ai_interpretation"] = interpretation
            completed.append("AIInterpretation")
            trace.append({"stage": "AIInterpretation", "status": "completed", "message": f"Interpretation source: {interpretation.get('provider_status', 'unknown')}. AI content is kept separate from calculated findings."})
            result["trace"] = trace
            result["completed_stages"] = completed
        result["run_id"] = run_id
        result["status"] = "completed"
        result["prompt"] = prompt
        with connection() as conn:
            conn.execute("UPDATE agent_runs SET status='completed',current_stage='Control',completed_stages_json=?,result_json=?,error='',updated_at=? WHERE id=?", (json_dump(completed), json_dump(result), now_iso(), run_id))
        audit("agent.run_completed", "agent_run", run_id, {"stages": completed, "findings": len(result.get("verified_findings", []))})
        return result
    except Exception as exc:
        result.update({"run_id": run_id, "status": "failed", "trace": trace, "completed_stages": completed, "verified_findings": _verified_findings(analysis, dataset_info)})
        with connection() as conn:
            conn.execute("UPDATE agent_runs SET status='failed',completed_stages_json=?,result_json=?,error=?,updated_at=? WHERE id=?", (json_dump(completed), json_dump(result), f"{type(exc).__name__}: {exc}"[:2000], now_iso(), run_id))
        audit("agent.run_failed", "agent_run", run_id, {"error_type": type(exc).__name__})
        raise


def get_agent_run(run_id: str) -> dict | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
    if not row: return None
    result = json.loads(row["result_json"] or "{}")
    result.update({"run_id": row["id"], "status": row["status"], "current_stage": row["current_stage"], "completed_stages": json.loads(row["completed_stages_json"]), "error": row["error"], "project_id": row["project_id"], "dataset_id": row["dataset_id"], "analysis_run_id": row["analysis_run_id"], "created_at": row["created_at"], "updated_at": row["updated_at"]})
    return result


def list_agent_runs() -> list[dict]:
    with connection() as conn:
        rows = conn.execute("SELECT id,project_id,dataset_id,status,current_stage,created_at,updated_at FROM agent_runs ORDER BY created_at DESC LIMIT 50").fetchall()
    return [dict(r) for r in rows]
