import json
import uuid
from ..db import connection, json_dump, now_iso, audit


def create_project(payload: dict) -> dict:
    project_id = str(uuid.uuid4()); now = now_iso()
    with connection() as conn:
        conn.execute("INSERT INTO projects(id,name,process_name,problem_statement,goal_statement,owner,status,priority,target_date,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (project_id, payload["name"], payload.get("process_name", ""), payload.get("problem_statement", ""), payload.get("goal_statement", ""), payload.get("owner", ""), "Define", payload.get("priority", "Medium"), payload.get("target_date", ""), now, now))
        conn.execute("INSERT INTO dmaic_entries(project_id,phase,entry_type,title,content,evidence_json,created_at) VALUES(?,?,?,?,?,?,?)", (project_id, "Define", "note", "Project created", "Project charter started. Add problem statement, goal, scope, baseline, and stakeholders as evidence becomes available.", "[]", now))
    audit("project.created", "project", project_id, {"name": payload["name"]})
    return get_project(project_id)


def list_projects() -> list[dict]:
    with connection() as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_project(project_id: str) -> dict | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row: return None
        entries = conn.execute("SELECT * FROM dmaic_entries WHERE project_id=? ORDER BY id DESC", (project_id,)).fetchall()
    result = dict(row); result["entries"] = []
    for entry in entries:
        item = dict(entry); item["evidence_ids"] = json.loads(item.pop("evidence_json")); result["entries"].append(item)
    return result


def update_project(project_id: str, changes: dict) -> dict | None:
    changes = {k: v for k, v in changes.items() if v is not None}
    if not changes: return get_project(project_id)
    allowed = {"name", "process_name", "problem_statement", "goal_statement", "owner", "status", "priority", "target_date"}
    if set(changes) - allowed: raise ValueError("Unsupported project fields.")
    changes["updated_at"] = now_iso()
    sql = ",".join(f"{key}=?" for key in changes)
    with connection() as conn:
        cur = conn.execute(f"UPDATE projects SET {sql} WHERE id=?", (*changes.values(), project_id))
        if cur.rowcount == 0: return None
    audit("project.updated", "project", project_id, {"fields": list(changes.keys())})
    return get_project(project_id)


def add_entry(project_id: str, payload: dict) -> dict | None:
    now = now_iso()
    with connection() as conn:
        if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone(): return None
        cur = conn.execute("INSERT INTO dmaic_entries(project_id,phase,entry_type,title,content,evidence_json,created_at) VALUES(?,?,?,?,?,?,?)", (project_id, payload["phase"], payload.get("entry_type", "note"), payload["title"], payload["content"], json_dump(payload.get("evidence_ids", [])), now))
        conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))
        entry_id = cur.lastrowid
    audit("dmaic.entry_added", "project", project_id, {"entry_id": entry_id, "phase": payload["phase"], "entry_type": payload.get("entry_type", "note")})
    return {"id": entry_id, "project_id": project_id, "phase": payload["phase"], "entry_type": payload.get("entry_type", "note"), "title": payload["title"], "content": payload["content"], "evidence_ids": payload.get("evidence_ids", []), "created_at": now}
