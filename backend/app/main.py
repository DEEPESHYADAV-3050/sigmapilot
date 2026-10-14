from __future__ import annotations
import json
import sqlite3
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, PlainTextResponse
import pandas as pd

from .config import DB_PATH, LLM_API_KEY, MAX_UPLOAD_MB
from .db import init_db, connection, json_dump, now_iso, audit
from .schemas import ProjectCreate, ProjectUpdate, EntryCreate, AnalysisRequest, AgentRequest, AgentResumeRequest, HealthResponse, ManualCSVRequest, HTTPJSONConnectorRequest, TeamReviewRequest, TeamActionUpdate
from .services.ingestion import upload_file, list_datasets, list_documents, frame_for_dataset
from .services.statistics import analyze_measurements, defect_metrics, welch_two_sample_test
from .services.validation import audit_raw_dataframe, validate_calculated_results
from .services.projects import create_project, list_projects, get_project, update_project, add_entry
from .services.agent import create_agent_run, get_agent_run, list_agent_runs, advance_agent_run
from .services.reports import analysis_markdown, agent_markdown
from .services.pdf_reports import markdown_to_pdf
from .services.connectors import import_http_json
from .services.virtual_team import create_team_review, get_team_review, list_team_reviews, update_team_action
from .services.dmaic_case import analyze_dmaic_case, report_to_markdown
from .services.agent import _llm_interpret

@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield

app = FastAPI(title="SigmaPilot API", version="1.1.0", description="Local-first, evidence-disciplined Six Sigma project and analysis agent", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4173", "http://127.0.0.1:4173"], allow_credentials=False, allow_methods=["GET", "POST", "PATCH", "OPTIONS"], allow_headers=["*"])


@app.get("/api/health", response_model=HealthResponse)
def health():
    try:
        with connection() as conn: conn.execute("SELECT 1").fetchone()
        db = "ok"
    except sqlite3.Error:
        db = "error"
    return {"status": "ok" if db == "ok" else "degraded", "version": app.version, "llm_configured": bool(LLM_API_KEY), "database": db}

@app.get("/api/dashboard")
def dashboard():
    with connection() as conn:
        projects = conn.execute("SELECT status, COUNT(*) AS n FROM projects GROUP BY status").fetchall()
        datasets = conn.execute("SELECT COUNT(*) AS n FROM datasets").fetchone()["n"]
        docs = conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
        runs = conn.execute("SELECT status, COUNT(*) AS n FROM agent_runs GROUP BY status").fetchall()
        analyses = conn.execute("SELECT COUNT(*) AS n FROM analysis_runs").fetchone()["n"]
        latest_audit = conn.execute("SELECT action,entity_type,entity_id,created_at FROM audit_log ORDER BY id DESC LIMIT 8").fetchall()
    return {"project_status_counts": {r["status"]: r["n"] for r in projects}, "datasets": datasets, "documents": docs, "analysis_runs": analyses, "agent_run_counts": {r["status"]: r["n"] for r in runs}, "recent_activity": [dict(r) for r in latest_audit], "llm_configured": bool(LLM_API_KEY)}

@app.get("/api/projects")
def projects_list():
    summaries = list_projects()
    return [get_project(p["id"]) for p in summaries]

@app.post("/api/projects", status_code=201)
def projects_create(payload: ProjectCreate):
    try: return create_project(payload.model_dump())
    except sqlite3.Error as exc: raise HTTPException(500, f"Could not create project: {exc}")

@app.get("/api/projects/{project_id}")
def project_get(project_id: str):
    result = get_project(project_id)
    if result is None: raise HTTPException(404, "Project not found")
    return result

@app.patch("/api/projects/{project_id}")
def project_patch(project_id: str, payload: ProjectUpdate):
    result = update_project(project_id, payload.model_dump(exclude_unset=True))
    if result is None: raise HTTPException(404, "Project not found")
    return result

@app.post("/api/projects/{project_id}/entries", status_code=201)
def project_entry(project_id: str, payload: EntryCreate):
    result = add_entry(project_id, payload.model_dump())
    if result is None: raise HTTPException(404, "Project not found")
    return result

@app.post("/api/files/upload", status_code=201)
async def file_upload(file: UploadFile = File(...)):
    content = await file.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
    try:
        result = upload_file(file.filename or "upload", content)
        audit("file.uploaded", result["kind"], result["id"], {"filename": result["filename"], "file_type": result["file_type"]})
        return result
    except (ValueError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(400, f"Could not process file: {type(exc).__name__}: {exc}")

@app.post("/api/datasets/manual-csv", status_code=201)
def manual_dataset(payload: ManualCSVRequest):
    try:
        result = upload_file(payload.filename, payload.csv_text.encode("utf-8"))
        if result["kind"] != "dataset": raise ValueError("Manual data input must be CSV.")
        audit("dataset.manual_imported", "dataset", result["id"], {"filename": result["filename"], "row_count": result["row_count"]})
        return result
    except (ValueError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise HTTPException(400, str(exc))

@app.post("/api/connectors/http-json/import", status_code=201)
def connector_import(payload: HTTPJSONConnectorRequest):
    try:
        result = import_http_json(payload.url, payload.bearer_token, payload.filename)
        audit("connector.imported", "dataset", result["id"], {"connector_type": "read_only_https_json", "host": result["connector"]["host"], "row_count": result["row_count"]})
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc))

@app.get("/api/datasets")
def datasets_list(): return list_datasets()

@app.get("/api/documents")
def documents_list(q: str = Query(default="", max_length=300)): return list_documents(q)

@app.post("/api/datasets/{dataset_id}/analysis", status_code=201)
def dataset_analysis(dataset_id: str, payload: AnalysisRequest):
    try:
        frame = frame_for_dataset(dataset_id)
        if payload.value_column not in frame.columns: raise ValueError(f"Column '{payload.value_column}' does not exist in this dataset.")
        values = pd.to_numeric(frame[payload.value_column], errors="coerce")
        data_quality = audit_raw_dataframe(
            frame, payload.value_column, payload.defective_column,
            payload.defect_count_column, payload.group_column
        )
        result = analyze_measurements(values.to_numpy(), payload.lsl, payload.usl)
        result["data_quality"] = data_quality
        defects = None
        if payload.defective_column:
            if payload.defective_column not in frame.columns: raise ValueError("Defective-unit column does not exist.")
            defects = defect_metrics(defective_values=frame[payload.defective_column].tolist())
        if payload.defect_count_column:
            if payload.defect_count_column not in frame.columns: raise ValueError("Defect-count column does not exist.")
            count_audit = data_quality.get("defect_count") or {}
            if not count_audit.get("valid_for_count_metrics", False):
                withheld = {"status": "withheld_due_to_data_quality", "dpmo": None, "sigma_estimate": None,
                            "assumptions": ["DPMO and sigma estimate were withheld because the selected defect-count column contains missing, non-numeric, negative, or fractional values. Resolve the source data first."]}
                if defects:
                    defects.update(withheld)
                else:
                    defects = withheld
            else:
                defect_counts = pd.to_numeric(frame[payload.defect_count_column], errors="raise").to_numpy()
                dpmo = defect_metrics(defect_counts=defect_counts, opportunities_per_unit=payload.opportunities_per_unit)
                if defects:
                    for key in ("defect_count", "dpmo", "sigma_estimate", "assumptions", "status"):
                        if key in dpmo and dpmo[key] is not None:
                            if key == "assumptions": defects[key] = list(dict.fromkeys((defects.get(key) or []) + (dpmo.get(key) or [])))
                            else: defects[key] = dpmo[key]
                else: defects = dpmo
        elif payload.opportunities_per_unit:
            raise ValueError("Choose a defect-count column before specifying opportunities per unit.")
        result["defects"] = defects
        if payload.group_column:
            if payload.group_column not in frame.columns: raise ValueError("Group column does not exist.")
            if payload.group_a is None or payload.group_b is None or payload.group_a == payload.group_b: raise ValueError("Choose two different group values.")
            group = frame[payload.group_column].astype(str)
            a = pd.to_numeric(frame.loc[group == payload.group_a, payload.value_column], errors="coerce").dropna()
            b = pd.to_numeric(frame.loc[group == payload.group_b, payload.value_column], errors="coerce").dropna()
            result["group_comparison"] = welch_two_sample_test(a, b)
        result["calculation_validation"] = validate_calculated_results(frame, payload.model_dump(), result)
        run_id = str(uuid.uuid4()); created = now_iso()
        config = payload.model_dump()
        with connection() as conn:
            conn.execute("INSERT INTO analysis_runs(id,dataset_id,config_json,result_json,created_at) VALUES(?,?,?,?,?)", (run_id, dataset_id, json_dump(config), json_dump(result), created))
        audit("analysis.completed", "analysis_run", run_id, {"dataset_id": dataset_id, "value_column": payload.value_column})
        return {"id": run_id, "dataset_id": dataset_id, "config": config, "result": result, "created_at": created}
    except KeyError as exc: raise HTTPException(404, str(exc))
    except (ValueError, TypeError) as exc: raise HTTPException(400, str(exc))

@app.get("/api/analyses")
def analyses_list():
    with connection() as conn:
        rows = conn.execute("SELECT a.id,a.dataset_id,a.config_json,a.result_json,a.created_at,d.filename FROM analysis_runs a LEFT JOIN datasets d ON d.id=a.dataset_id ORDER BY a.created_at DESC LIMIT 50").fetchall()
    return [{"id": r["id"], "dataset_id": r["dataset_id"], "filename": r["filename"], "config": json.loads(r["config_json"]), "result": json.loads(r["result_json"]), "created_at": r["created_at"]} for r in rows]

@app.get("/api/analysis/{analysis_run_id}")
def get_analysis(analysis_run_id: str):
    with connection() as conn:
        row = conn.execute("SELECT * FROM analysis_runs WHERE id=?", (analysis_run_id,)).fetchone()
        ds = conn.execute("SELECT filename FROM datasets WHERE id=?", (row["dataset_id"],)).fetchone() if row else None
    if not row: raise HTTPException(404, "Analysis run not found")
    return {"id": row["id"], "dataset_id": row["dataset_id"], "filename": ds["filename"] if ds else None, "config": json.loads(row["config_json"]), "result": json.loads(row["result_json"]), "created_at": row["created_at"]}

@app.post("/api/agent/run", status_code=201)
def agent_run(payload: AgentRequest):
    if not (payload.project_id or payload.dataset_id or payload.analysis_run_id): raise HTTPException(400, "Provide a project, dataset, or analysis run to ground the agent run.")
    if payload.project_id and not get_project(payload.project_id): raise HTTPException(404, "Project not found")
    if payload.dataset_id:
        try: frame_for_dataset(payload.dataset_id)
        except KeyError as exc: raise HTTPException(404, str(exc))
    if payload.analysis_run_id:
        with connection() as conn: exists = conn.execute("SELECT 1 FROM analysis_runs WHERE id=?", (payload.analysis_run_id,)).fetchone()
        if not exists: raise HTTPException(404, "Analysis run not found")
    try: return create_agent_run(payload.model_dump())
    except Exception as exc: raise HTTPException(500, f"Agent run failed: {type(exc).__name__}: {exc}")

@app.get("/api/agent/runs")
def agent_runs_list(): return list_agent_runs()

@app.get("/api/agent/runs/{run_id}")
def agent_run_get(run_id: str):
    result = get_agent_run(run_id)
    if result is None: raise HTTPException(404, "Agent run not found")
    return result

@app.post("/api/agent/runs/{run_id}/resume")
def agent_run_resume(run_id: str, payload: AgentResumeRequest):
    result = get_agent_run(run_id)
    if result is None: raise HTTPException(404, "Agent run not found")
    if result["status"] == "completed": return result
    try: return advance_agent_run(run_id, payload.prompt)
    except Exception as exc: raise HTTPException(500, f"Resume failed: {type(exc).__name__}: {exc}")



CASE_RUN_DIR = Path(__file__).resolve().parents[2] / "storage" / "case_runs"
CASE_RUN_DIR.mkdir(parents=True, exist_ok=True)

@app.post("/api/case-workspace/run", status_code=201)
async def case_workspace_run(files: list[UploadFile] = File(...), prompt: str = Form(default="Run a complete evidence-led DMAIC review of all supplied files. Calculate metrics from source data, validate them independently where possible, identify limitations, and produce an actionable project plan.")):
    if not files:
        raise HTTPException(400, "Upload at least one dataset, charter, reference document, or ZIP evidence package.")
    if len(files) > 150:
        raise HTTPException(400, "Upload no more than 150 files in one run.")
    payloads = []
    total_bytes = 0
    for file in files:
        content = await file.read(35 * 1024 * 1024 + 1)
        total_bytes += len(content)
        if len(content) > 35 * 1024 * 1024 or total_bytes > 35 * 1024 * 1024:
            raise HTTPException(413, "Total uploaded content is limited to 35 MB per case review.")
        payloads.append((file.filename or "uploaded-file", content))
    try:
        result = analyze_dmaic_case(payloads, prompt[:12000])
        # Optional model synthesis sees aggregate evidence plus the user/task prompt; raw measurement rows and general reference-document contents are not sent.
        synthesis_context = {
            "data_provenance": {"synthetic_data_detected": result["data_provenance"]["synthetic_data_detected"], "file_inventory": [{"filename": x["filename"], "kind": x["kind"], "status": x["status"], "rows": x.get("rows")} for x in result["data_provenance"]["file_inventory"]]},
            "project_charter": {"title": (result.get("project_charter") or {}).get("title"), "problem_statement": (result.get("project_charter") or {}).get("problem_statement"), "goal_statement": (result.get("project_charter") or {}).get("goal_statement"), "ctq": (result.get("project_charter") or {}).get("ctq"), "scope_in": (result.get("project_charter") or {}).get("scope_in"), "scope_out": (result.get("project_charter") or {}).get("scope_out")},
            "data_dictionary": result.get("data_dictionary"),
            "baseline_analysis": result.get("baseline_analysis"),
            "pre_post_comparison": result.get("pre_post_comparison"),
            "measurement_system_status": result.get("measurement_system_analysis"),
            "pilot_effects": (result.get("improvement_pilot") or {}).get("effects"),
            "control_signals": (result.get("process_control") or {}).get("signals_outside_limits"),
            "association_screens": result.get("production_context_associations", [])[:10],
            "known_limitations": result.get("limitations"),
            "recommended_actions": result.get("recommended_actions"),
            "dmaic_phase_review": result.get("dmaic_phase_review")
        }
        attached_instructions = "\n\n".join(item["text"] for item in result.get("uploaded_task_instructions", []))
        synthesis_prompt = (prompt + ("\n\nUploaded task/reference instructions:\n" + attached_instructions if attached_instructions else ""))[:16000]
        result["ai_team_synthesis"] = _llm_interpret(synthesis_context, synthesis_prompt)
        result["run_id"] = str(uuid.uuid4())
        result["created_at"] = now_iso()
        markdown = report_to_markdown(result)
        (CASE_RUN_DIR / f"{result['run_id']}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        (CASE_RUN_DIR / f"{result['run_id']}.md").write_text(markdown, encoding="utf-8")
        audit("dmaic_case.completed", "dmaic_case", result["run_id"], {"file_count": len(payloads), "datasets": result["data_provenance"]["parsed_data_tables"], "limitations": len(result["limitations"])})
        return {"report": result, "report_markdown": markdown}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        audit("dmaic_case.failed", "dmaic_case", str(uuid.uuid4()), {"error_type": type(exc).__name__})
        raise HTTPException(500, f"DMAIC case analysis failed safely: {type(exc).__name__}: {str(exc)[:500]}")

def _case_json_path(run_id: str) -> Path:
    try:
        normalized = str(uuid.UUID(run_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(404, "DMAIC case report not found.")
    if normalized != run_id.lower():
        raise HTTPException(404, "DMAIC case report not found.")
    return CASE_RUN_DIR / f"{normalized}.json"

@app.get("/api/case-workspace/{run_id}")
def case_workspace_get(run_id: str):
    path = _case_json_path(run_id)
    if not path.is_file():
        raise HTTPException(404, "DMAIC case report not found.")
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): raise HTTPException(500, "Saved case report could not be read.")

@app.get("/api/reports/case/{run_id}")
def case_workspace_report(run_id: str, format: str = Query(default="markdown", pattern="^(markdown|json|pdf)$")):
    path = _case_json_path(run_id)
    if not path.is_file():
        raise HTTPException(404, "DMAIC case report not found.")
    try: result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): raise HTTPException(500, "Saved case report could not be read.")
    if format == "json":
        return Response(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="sigmapilot-case-{run_id}.json"'})
    markdown_path = CASE_RUN_DIR / f"{run_id}.md"
    content = markdown_path.read_text(encoding="utf-8") if markdown_path.exists() else report_to_markdown(result)
    if format == "pdf":
        return Response(markdown_to_pdf("SigmaPilot Complete DMAIC Case Report", content), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="sigmapilot-case-{run_id}.pdf"'})
    return PlainTextResponse(content, media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="sigmapilot-case-{run_id}.md"'})


@app.get("/api/reports/analysis/{analysis_run_id}")
def analysis_report(analysis_run_id: str, format: str = Query(default="markdown", pattern="^(markdown|json|pdf)$")):
    with connection() as conn:
        row = conn.execute("SELECT * FROM analysis_runs WHERE id=?", (analysis_run_id,)).fetchone()
        ds = conn.execute("SELECT filename FROM datasets WHERE id=?", (row["dataset_id"],)).fetchone() if row else None
    if not row: raise HTTPException(404, "Analysis run not found")
    obj = {"id": row["id"], "dataset_id": row["dataset_id"], "filename": ds["filename"] if ds else None, "config": json.loads(row["config_json"]), "result": json.loads(row["result_json"]), "created_at": row["created_at"]}
    if format == "json": return Response(json.dumps(obj, indent=2, ensure_ascii=False), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="analysis-{analysis_run_id}.json"'})
    content = analysis_markdown(obj["filename"] or "dataset", obj["config"], obj["result"], obj["created_at"])
    if format == "pdf":
        return Response(markdown_to_pdf("SigmaPilot Analysis Report", content), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="analysis-{analysis_run_id}.pdf"'})
    return PlainTextResponse(content, media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="analysis-{analysis_run_id}.md"'})

@app.get("/api/reports/agent/{run_id}")
def agent_report(run_id: str, format: str = Query(default="markdown", pattern="^(markdown|pdf)$")):
    result = get_agent_run(run_id)
    if result is None: raise HTTPException(404, "Agent run not found")
    project = get_project(result["project_id"]) if result.get("project_id") else None
    content = agent_markdown(project, result)
    if format == "pdf":
        return Response(markdown_to_pdf("SigmaPilot Agent Report", content), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="sigmapilot-agent-{run_id}.pdf"'})
    return PlainTextResponse(content, media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="sigmapilot-agent-{run_id}.md"'})

@app.post("/api/team/review", status_code=201)
def team_review_create(payload: TeamReviewRequest):
    try:
        return create_team_review(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(500, f"Could not complete virtual team review: {type(exc).__name__}")

@app.get("/api/team/reviews")
def team_reviews_list():
    return list_team_reviews()

@app.patch("/api/team/reviews/{review_id}/actions/{action_id}")
def team_action_patch(review_id: str, action_id: str, payload: TeamActionUpdate):
    try:
        result = update_team_action(review_id, action_id, payload.status, payload.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if result is None:
        raise HTTPException(404, "Virtual team review not found")
    return result

@app.get("/api/team/reviews/{review_id}")
def team_review_get(review_id: str):
    result = get_team_review(review_id)
    if result is None:
        raise HTTPException(404, "Virtual team review not found")
    return result

@app.get("/api/reports/team/{review_id}")
def team_review_report(review_id: str, format: str = Query(default="markdown", pattern="^(markdown|json|pdf)$")):
    result = get_team_review(review_id)
    if result is None:
        raise HTTPException(404, "Virtual team review not found")
    if format == "json":
        return Response(json.dumps(result, indent=2, ensure_ascii=False), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="sigmapilot-team-{review_id}.json"'})
    lines = ["# SigmaPilot Virtual Six Sigma Team Review", "", f"Review ID: {result['review_id']}", f"Created: {result['created_at']}", "", "## Executive summary", result["executive_summary"], "", "## Virtual team assessments"]
    for member in result.get("team_members", []):
        lines += [f"### {member['role']}", f"Focus: {member['focus']}", member["assessment"], "Responsibilities:"]
        lines += [f"- {x}" for x in member.get("responsibilities", [])]
        lines += ["Watchouts:"] + [f"- {x}" for x in member.get("watchouts", [])] + [""]
    synthesis = result.get("ai_team_synthesis") or {}
    lines += ["## AI-assisted team synthesis", f"Provider status: {synthesis.get('provider_status', 'unknown')}", synthesis.get("summary", "No synthesis summary available."), "", "### Hypotheses"]
    lines += [f"- {x}" for x in synthesis.get("hypotheses", [])]
    lines += ["", "### Recommendations"] + [f"- {x}" for x in synthesis.get("recommendations", [])]
    lines += ["", "### Risks"] + [f"- {x}" for x in synthesis.get("risks", [])]
    lines += ["", "## DMAIC stage gates"]
    for gate in result.get("dmaic_gates", []):
        lines += [f"### {gate['phase']} — {gate['status']}"]
        lines += [f"- [{'x' if c['met'] else ' '}] {c['item']}" for c in gate.get("criteria", [])]
        lines += [gate["decision"], ""]
    lines += ["## Prioritized action register"]
    for action in result.get("action_register", []):
        lines += [f"### {action['id']} · {action['priority']} · {action['phase']}", f"**Owner:** {action['owner_role']}", action["task"], f"Rationale: {action['rationale']}", f"Exit evidence: {action['exit_evidence']}", ""]
    lines += ["## RACI"]
    for item in result.get("raci", []):
        lines += [f"- **{item['activity']}** — R: {item['responsible']}; A: {item['accountable']}; C: {item['consulted']}; I: {item['informed']}"]
    lines += ["", "## Limitations"] + [f"- {x}" for x in result.get("limitations", [])]
    content = "\n".join(lines)
    if format == "json":
        return Response(json.dumps(result, indent=2, ensure_ascii=False), media_type="application/json")
    if format == "pdf":
        return Response(markdown_to_pdf("SigmaPilot Virtual Six Sigma Team Review", content), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="sigmapilot-team-{review_id}.pdf"'})
    return PlainTextResponse(content, media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="sigmapilot-team-{review_id}.md"'})

@app.get("/api/audit")
def audit_list(limit: int = Query(default=50, ge=1, le=200)):
    with connection() as conn: rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{"id": r["id"], "actor": r["actor"], "action": r["action"], "entity_type": r["entity_type"], "entity_id": r["entity_id"], "details": json.loads(r["details_json"]), "created_at": r["created_at"]} for r in rows]
