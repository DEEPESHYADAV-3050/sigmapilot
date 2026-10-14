from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from ..db import connection, json_dump, now_iso, audit
from .ingestion import frame_for_dataset
from .agent import _llm_interpret

TEAM_ROLES = [
    {"role": "Executive Sponsor", "focus": "business case, strategic fit, decision rights", "outputs": ["Confirm the business problem and sponsor", "Approve a measurable goal and resource envelope", "Review benefit assumptions before funding decisions"]},
    {"role": "Master/Black Belt Project Lead", "focus": "DMAIC governance, scope, stage gates, facilitation", "outputs": ["Maintain the charter and phase exit criteria", "Coordinate the cross-functional workstream", "Escalate unresolved blockers and decisions"]},
    {"role": "Process Owner / Subject-Matter Expert", "focus": "actual workflow, constraints, standard work", "outputs": ["Map the end-to-end process and handoffs", "Validate operational definitions with operators", "Identify process constraints to investigate"]},
    {"role": "Data Analyst / Statistician", "focus": "data quality, sampling, statistical evidence", "outputs": ["Check completeness, range, and data representativeness", "Separate observed results from inference", "Select appropriate follow-up analysis"]},
    {"role": "Measurement Systems Analyst", "focus": "measurement validity, repeatability, reproducibility", "outputs": ["Define a measurement-system assessment plan", "Verify units, calibration, and operational definitions", "Do not treat capability as decision-ready before measurement review"]},
    {"role": "Root-Cause / Quality Engineer", "focus": "cause hypotheses, stratification, failure modes", "outputs": ["Build a testable cause-and-effect hypothesis list", "Use stratification and process observation to narrow causes", "Validate causes with evidence before permanent countermeasures"]},
    {"role": "Finance / Benefits Analyst", "focus": "cost of poor quality, benefit validation, payback", "outputs": ["Establish a finance-approved baseline cost", "Distinguish hard savings from capacity release and avoidance", "Validate benefit realization after implementation"]},
    {"role": "Change & Training Lead", "focus": "stakeholders, adoption, communication, training", "outputs": ["Identify impacted roles and training needs", "Plan communications and change readiness checks", "Confirm new standard work is understood"]},
    {"role": "Control Plan Owner", "focus": "sustainability, monitoring, reaction plan, handover", "outputs": ["Assign metric owner and review cadence", "Define control limits separately from specification limits", "Document reaction plan, escalation path, and handover"]},
]

PHASES = ["Define", "Measure", "Analyze", "Improve", "Control"]


def _load_project(project_id: str | None) -> dict | None:
    if not project_id:
        return None
    with connection() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            return None
        entries = conn.execute("SELECT phase,entry_type,title,content,evidence_json,created_at FROM dmaic_entries WHERE project_id=? ORDER BY id", (project_id,)).fetchall()
    p = dict(row)
    p["entries"] = [{**dict(r), "evidence_ids": json.loads(r["evidence_json"])} for r in entries]
    return p


def _load_analysis(analysis_run_id: str | None) -> tuple[dict | None, dict | None]:
    if not analysis_run_id:
        return None, None
    with connection() as conn:
        row = conn.execute("SELECT * FROM analysis_runs WHERE id=?", (analysis_run_id,)).fetchone()
        ds = conn.execute("SELECT filename FROM datasets WHERE id=?", (row["dataset_id"],)).fetchone() if row else None
    if not row:
        return None, None
    return json.loads(row["result_json"]), {"id": row["dataset_id"], "filename": ds["filename"] if ds else "Unknown", "config": json.loads(row["config_json"])}


def _profile_dataset(dataset_id: str | None) -> dict | None:
    if not dataset_id:
        return None
    try:
        frame = frame_for_dataset(dataset_id)
    except (KeyError, FileNotFoundError) as exc:
        raise ValueError("Dataset not found or stored file is missing.") from exc
    numeric = frame.select_dtypes(include=[np.number]).columns.tolist()
    missing = {str(c): int(frame[c].isna().sum()) for c in frame.columns if int(frame[c].isna().sum()) > 0}
    profile = {
        "dataset_id": dataset_id,
        "rows": int(len(frame)),
        "columns": [str(c) for c in frame.columns],
        "numeric_columns": [str(c) for c in numeric],
        "missing_by_column": missing,
        "duplicate_rows": int(frame.duplicated().sum()),
        "data_profile_status": "calculated",
    }
    profile["numeric_summary"] = {}
    for col in numeric[:30]:
        s = pd.to_numeric(frame[col], errors="coerce").dropna()
        profile["numeric_summary"][str(col)] = {
            "finite_n": int(np.isfinite(s.to_numpy(dtype=float)).sum()),
            "missing_or_nonfinite": int(len(frame) - np.isfinite(pd.to_numeric(frame[col], errors="coerce").to_numpy(dtype=float)).sum()),
            "min": float(s.min()) if len(s) else None,
            "max": float(s.max()) if len(s) else None,
            "mean": float(s.mean()) if len(s) else None,
        }
    return profile


def _phase_gates(project: dict | None, profile: dict | None, analysis: dict | None) -> list[dict]:
    problem = bool(project and project.get("problem_statement", "").strip())
    goal = bool(project and project.get("goal_statement", "").strip())
    owner = bool(project and project.get("owner", "").strip())
    process = bool(project and project.get("process_name", "").strip())
    data_ready = bool(profile and profile.get("rows", 0) > 0)
    analysis_ready = bool(analysis and analysis.get("n", 0) > 0)
    spec = (analysis or {}).get("specification") or {}
    cpk_ready = spec.get("cpk") is not None
    gates = [
        {"phase": "Define", "status": "ready_for_review" if problem and goal and process else "blocked", "criteria": [
            {"item": "Process name / boundary identified", "met": process},
            {"item": "Problem statement recorded", "met": problem},
            {"item": "Measurable goal recorded", "met": goal},
            {"item": "Named project owner", "met": owner},
        ], "decision": "Sponsor and process owner review required; this tool does not grant formal approval."},
        {"phase": "Measure", "status": "ready_for_review" if data_ready and analysis_ready else "blocked", "criteria": [
            {"item": "Dataset imported", "met": data_ready},
            {"item": "Statistical baseline saved", "met": analysis_ready},
            {"item": "Measurement system assessed", "met": False},
            {"item": "Sampling plan and operational definitions documented", "met": False},
        ], "decision": "Do not treat the baseline as validated until measurement and sampling checks are completed."},
        {"phase": "Analyze", "status": "ready_for_review" if analysis_ready else "blocked", "criteria": [
            {"item": "Baseline evidence available", "met": analysis_ready},
            {"item": "Candidate causes stratified and tested", "met": False},
            {"item": "Root cause verified with evidence", "met": False},
        ], "decision": "Root-cause verification requires process knowledge and follow-up evidence; summary statistics alone are insufficient."},
        {"phase": "Improve", "status": "blocked", "criteria": [
            {"item": "Countermeasure options assessed", "met": False},
            {"item": "Pilot plan with primary metric and guardrails", "met": False},
            {"item": "Post-change results demonstrate effect", "met": False},
        ], "decision": "No improvement is marked successful without post-change data."},
        {"phase": "Control", "status": "blocked", "criteria": [
            {"item": "Control plan and reaction plan approved", "met": False},
            {"item": "Control owner and review cadence assigned", "met": owner},
            {"item": "Handover and benefit validation completed", "met": False},
        ], "decision": "Sustainment requires an accountable human owner and an agreed response to signals."},
    ]
    if analysis_ready and not cpk_ready:
        gates[1]["criteria"].append({"item": "Specification limits available for capability analysis", "met": False})
    return gates


def _actions(project: dict | None, profile: dict | None, analysis: dict | None) -> list[dict]:
    actions = []
    def add(priority: str, phase: str, owner_role: str, task: str, rationale: str, exit_evidence: str):
        actions.append({"id": f"ACT-{len(actions)+1:03d}", "priority": priority, "phase": phase, "owner_role": owner_role, "task": task, "rationale": rationale, "exit_evidence": exit_evidence, "status": "not_started"})
    if not project or not project.get("problem_statement"):
        add("Critical", "Define", "Executive Sponsor + Project Lead", "Write a quantified problem statement with where/when/how much and customer impact.", "The team cannot agree on success or scope without a problem definition.", "Approved problem statement and project boundary")
    if not project or not project.get("goal_statement"):
        add("Critical", "Define", "Executive Sponsor", "Set a SMART goal with metric, target, due date, and guardrail.", "A vague goal prevents a defensible stage-gate decision.", "Signed goal statement with baseline and target")
    if not project or not project.get("owner"):
        add("High", "Define", "Project Lead", "Assign a named accountable process owner.", "Actions and sustainment need clear accountability.", "Named owner recorded in project charter")
    if not profile:
        add("Critical", "Measure", "Data Analyst", "Import a representative dataset and document source, time window, units, and sampling method.", "No dataset is attached to this team review.", "Dataset with documented provenance and data dictionary")
    else:
        if profile.get("rows", 0) < 30:
            add("High", "Measure", "Data Analyst / Statistician", "Assess whether the sample is adequate for the intended inference; collect more representative observations if needed.", f"Only {profile.get('rows')} rows are present; adequacy depends on process variation and decision risk.", "Approved sampling rationale")
        if profile.get("missing_by_column") or profile.get("duplicate_rows", 0):
            add("High", "Measure", "Data Analyst", "Resolve or disposition missing values and duplicate rows; preserve the original file and record exclusion rules.", "Data quality issues can bias estimates and conclusions.", "Data-quality log and reproducible cleaning rules")
        if not profile.get("numeric_columns"):
            add("Critical", "Measure", "Data Analyst", "Identify a valid numeric outcome or defect measure and define the unit of analysis.", "No numeric columns were detected for quantitative analysis.", "Validated metric definition and usable measurement column")
    if not analysis:
        add("High", "Measure", "Data Analyst / Statistician", "Run a baseline analysis with the correct outcome column and justified specification limits.", "The team review has no saved analysis run to cite.", "Saved baseline analysis with assumptions")
    else:
        result = analysis
        imr = result.get("imr") or {}
        signals = len(imr.get("individual_signals", [])) + len(imr.get("moving_range_signals", []))
        if imr.get("status") == "calculated" and signals:
            add("High", "Analyze", "Process Owner + Statistician", "Investigate the flagged time-ordered I-MR observations against process events before capability interpretation.", f"Basic I-MR diagnostics flagged {signals} limit signals; this is a signal to investigate, not proof of a cause.", "Documented special-cause investigation and disposition")
        spec = result.get("specification") or {}
        if spec.get("cpk") is not None:
            add("High", "Measure", "Measurement Systems Analyst", "Validate measurement-system adequacy and process stability before using the Cpk estimate for decisions.", "Capability interpretation depends on measurement quality, stability, distribution, and assumptions.", "MSA result and stability review signed off")
    add("High", "Analyze", "Root-Cause / Quality Engineer", "Create a fishbone and 5-Why hypotheses, then rank and test them with stratified data and direct process observation.", "Potential causes must remain hypotheses until independently tested.", "Evidence-backed cause verification record")
    add("Medium", "Improve", "Process Owner + Change Lead", "Design a low-risk pilot with one primary outcome, guardrail metrics, sample plan, owner, and rollback criteria.", "A controlled pilot reduces risk and makes the effect measurable.", "Approved pilot protocol and pre-change baseline")
    add("Medium", "Control", "Control Plan Owner", "Define the monitoring chart, frequency, reaction plan, escalation owner, and handover checklist.", "Improvements decay without a response plan and ownership.", "Approved control plan and training / handover evidence")
    add("Medium", "Improve", "Finance / Benefits Analyst", "Baseline the cost of poor quality and agree how realized savings, cost avoidance, and capacity release will be validated.", "Financial impact must not be invented or double-counted.", "Finance-approved benefit calculation and realization method")
    return actions


def _role_assessments(project: dict | None, profile: dict | None, analysis: dict | None, gates: list[dict], actions: list[dict]) -> list[dict]:
    open_actions = [a for a in actions if a["status"] != "completed"]
    gate_by_phase = {g["phase"]: g for g in gates}
    result = []
    for role in TEAM_ROLES:
        role_name = role["role"]
        assigned = [a for a in open_actions if role_name.split(" /")[0] in a["owner_role"] or any(part.strip() in a["owner_role"] for part in role_name.split("+"))]
        if role_name == "Executive Sponsor":
            summary = "Business case and target need explicit agreement." if not (project and project.get("goal_statement")) else "Review target, resources, and benefit assumptions before authorizing investment."
            watchouts = ["No financial benefit is considered realized until Finance validates it.", "Formal sponsorship approval must be recorded by a human decision-maker."]
        elif role_name == "Master/Black Belt Project Lead":
            summary = f"Current DMAIC review: {sum(g['status'] == 'ready_for_review' for g in gates)} of 5 phases have enough inputs to be reviewed; none is auto-approved."
            watchouts = ["Keep stage-gate decisions, owners, and evidence traceable.", "Do not advance a phase solely because an AI review completed."]
        elif role_name == "Process Owner / Subject-Matter Expert":
            summary = "Validate the actual workflow, handoffs, and operator context; the system cannot infer undocumented process reality."
            watchouts = ["Verify the data's time order and whether each row represents the intended unit."]
        elif role_name == "Data Analyst / Statistician":
            summary = f"Dataset profile: {profile['rows']} rows and {len(profile['columns'])} columns." if profile else "No dataset attached; quantitative conclusions are blocked."
            watchouts = ["Missingness, duplicates, and sampling bias can distort results.", "Statistical significance does not prove causation or practical importance."]
        elif role_name == "Measurement Systems Analyst":
            summary = "Measurement-system analysis has not been supplied in this review; verify calibration, repeatability, reproducibility, and operational definitions."
            watchouts = ["Capability metrics are provisional until MSA and stability are acceptable."]
        elif role_name == "Root-Cause / Quality Engineer":
            summary = "Use process mapping, stratification, fishbone/5-Why, and designed tests; no cause is verified by this team review alone."
            watchouts = ["Treat candidate causes as hypotheses until evidence supports them."]
        elif role_name == "Finance / Benefits Analyst":
            summary = "No monetary benefit is calculated unless approved cost inputs and a validated benefit model are provided."
            watchouts = ["Separate hard savings, cost avoidance, and capacity release; prevent double counting."]
        elif role_name == "Change & Training Lead":
            summary = "Prepare impacted-stakeholder, communications, training, and adoption checks before standard work changes."
            watchouts = ["A technically effective change can fail if adoption and workload effects are ignored."]
        else:
            summary = "Control plan is not yet approved; define the control metric, owner, cadence, signal response, and handover."
            watchouts = ["Control limits are not specification limits.", "Escalation and reaction plans need accountable human approval."]
        result.append({"role": role_name, "focus": role["focus"], "status": "needs_attention" if assigned or any(not c["met"] for c in gate_by_phase["Measure"]["criteria"]) else "review", "assessment": summary, "responsibilities": role["outputs"], "watchouts": watchouts, "assigned_actions": [a["id"] for a in assigned]})
    return result


def create_team_review(payload: dict) -> dict:
    project = _load_project(payload.get("project_id"))
    if payload.get("project_id") and not project:
        raise ValueError("Project not found.")
    analysis, analysis_info = _load_analysis(payload.get("analysis_run_id"))
    if payload.get("analysis_run_id") and not analysis:
        raise ValueError("Analysis run not found.")
    dataset_id = payload.get("dataset_id") or (analysis_info or {}).get("id")
    profile = _profile_dataset(dataset_id) if dataset_id else None
    if dataset_id and not profile:
        raise ValueError("Dataset not found or could not be read.")
    if analysis_info and dataset_id and analysis_info["id"] != dataset_id:
        raise ValueError("The selected analysis does not belong to the selected dataset.")
    gates = _phase_gates(project, profile, analysis)
    actions = _actions(project, profile, analysis)
    roles = _role_assessments(project, profile, analysis, gates, actions)
    blockers = [{"phase": g["phase"], "criteria": [c["item"] for c in g["criteria"] if not c["met"]]} for g in gates if any(not c["met"] for c in g["criteria"])]
    review_id = str(uuid.uuid4())
    report = {
        "review_id": review_id,
        "created_at": now_iso(),
        "status": "completed",
        "project": {"id": project["id"], "name": project["name"], "process_name": project["process_name"], "owner": project["owner"], "current_phase": project["status"], "problem_statement": project["problem_statement"], "goal_statement": project["goal_statement"]} if project else None,
        "dataset": profile,
        "analysis": {"id": payload.get("analysis_run_id"), "filename": analysis_info.get("filename") if analysis_info else None, "config": analysis_info.get("config") if analysis_info else None, "result": analysis} if analysis else None,
        "request": payload.get("question", "Review this process as a cross-functional Six Sigma team."),
        "executive_summary": f"Virtual team review completed. {sum(g['status'] == 'ready_for_review' for g in gates)} of 5 DMAIC phases have enough recorded inputs to be reviewed; no phase is automatically approved. {len(actions)} prioritized actions were generated.",
        "team_model": "Role-based virtual team simulation; outputs are decision support, not evidence that real people reviewed or approved the project.",
        "team_members": roles,
        "dmaic_gates": gates,
        "action_register": actions,
        "blockers": blockers,
        "raci": [
            {"activity": "Charter and target approval", "responsible": "Project Lead", "accountable": "Executive Sponsor", "consulted": "Process Owner, Finance", "informed": "Impacted stakeholders"},
            {"activity": "Data quality and baseline", "responsible": "Data Analyst / Statistician", "accountable": "Project Lead", "consulted": "Measurement Systems Analyst, Process Owner", "informed": "Sponsor"},
            {"activity": "Root-cause verification", "responsible": "Root-Cause / Quality Engineer", "accountable": "Process Owner", "consulted": "Data Analyst, operators", "informed": "Sponsor"},
            {"activity": "Pilot approval and execution", "responsible": "Process Owner + Change Lead", "accountable": "Executive Sponsor", "consulted": "Quality, Finance, impacted operators", "informed": "Project team"},
            {"activity": "Control and handover", "responsible": "Control Plan Owner", "accountable": "Process Owner", "consulted": "Data Analyst, Change Lead", "informed": "Sponsor and operators"},
        ],
        "decision_log": [{"decision": "No DMAIC phase is auto-approved", "status": "governance_rule", "reason": "Stage gates require human review and documented evidence."}, {"decision": "No root cause or improvement success declared", "status": "evidence_rule", "reason": "No causal claim is accepted without a verified study and post-change data."}],
        "limitations": ["This is a role-based AI/software simulation, not nine actual professionals or formal certification.", "No external business system is queried unless a connector import is explicitly run.", "Financial savings, customer impact, and operational constraints are not inferred without evidence.", "Human owners must approve stage gates, safety-critical changes, and production deployment."],
    }
    # Optional model synthesis uses only structured summaries and calculated outputs, never raw rows or document text.
    analysis_context = None
    if analysis:
        analysis_context = {
            "n": analysis.get("n"), "mean": analysis.get("mean"), "median": analysis.get("median"),
            "sample_std_dev": analysis.get("sample_std_dev"), "specification": analysis.get("specification"),
            "defects": analysis.get("defects"), "imr": analysis.get("imr"), "group_comparison": analysis.get("group_comparison"),
            "assumptions": analysis.get("assumptions", []),
        }
    report["ai_team_synthesis"] = _llm_interpret({
        "project_summary": report["project"],
        "dataset_profile": profile,
        "calculated_analysis_summary": analysis_context,
        "dmaic_gates": gates,
        "prioritized_action_register": actions,
        "virtual_team_roles": [{"role": m["role"], "assessment": m["assessment"], "watchouts": m["watchouts"]} for m in roles],
    }, report["request"])
    with connection() as conn:
        conn.execute("INSERT INTO team_reviews(id,project_id,dataset_id,analysis_run_id,question,result_json,created_at) VALUES(?,?,?,?,?,?,?)", (review_id, payload.get("project_id"), dataset_id, payload.get("analysis_run_id"), report["request"], json_dump(report), report["created_at"]))
    audit("virtual_team.review_completed", "team_review", review_id, {"project_id": payload.get("project_id"), "dataset_id": dataset_id, "actions": len(actions), "blockers": len(blockers)})
    return report


def get_team_review(review_id: str) -> dict | None:
    with connection() as conn:
        row = conn.execute("SELECT result_json FROM team_reviews WHERE id=?", (review_id,)).fetchone()
    return json.loads(row["result_json"]) if row else None


def list_team_reviews() -> list[dict]:
    with connection() as conn:
        rows = conn.execute("SELECT id,project_id,dataset_id,analysis_run_id,question,created_at FROM team_reviews ORDER BY created_at DESC LIMIT 50").fetchall()
    return [dict(r) for r in rows]


def update_team_action(review_id: str, action_id: str, status: str, note: str = "") -> dict | None:
    allowed = {"not_started", "in_progress", "blocked", "completed"}
    if status not in allowed:
        raise ValueError("Unsupported action status.")
    with connection() as conn:
        row = conn.execute("SELECT result_json FROM team_reviews WHERE id=?", (review_id,)).fetchone()
        if not row:
            return None
        report = json.loads(row["result_json"])
        action = next((a for a in report.get("action_register", []) if a.get("id") == action_id), None)
        if action is None:
            raise ValueError("Action not found in this review.")
        action["status"] = status
        action["last_updated_at"] = now_iso()
        if note.strip():
            action.setdefault("updates", []).append({"note": note.strip(), "created_at": now_iso()})
        conn.execute("UPDATE team_reviews SET result_json=? WHERE id=?", (json_dump(report), review_id))
    audit("virtual_team.action_updated", "team_review", review_id, {"action_id": action_id, "status": status, "has_note": bool(note.strip())})
    return report
