import pytest
from app import db
from app.services import ingestion
from app.services.agent import advance_agent_run, create_agent_run


def test_failed_agent_run_is_persisted_and_can_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "agent.sqlite3")
    monkeypatch.setattr(ingestion, "UPLOAD_DIR", tmp_path / "uploads")
    ingestion.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db()
    csv_content = b"measurement\n1.0\n1.1\n0.9\n1.2\n"
    dataset = ingestion.upload_file("measurements.csv", csv_content)
    with db.connection() as conn:
        row = conn.execute("SELECT stored_path FROM datasets WHERE id=?", (dataset["id"],)).fetchone()
    stored_path = row["stored_path"]
    import pathlib
    pathlib.Path(stored_path).unlink()

    with pytest.raises(FileNotFoundError):
        create_agent_run({"dataset_id": dataset["id"], "prompt": "Inspect process"})
    with db.connection() as conn:
        run = conn.execute("SELECT id,status FROM agent_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    assert run["status"] == "failed"
    pathlib.Path(stored_path).write_bytes(csv_content)

    result = advance_agent_run(run["id"])
    assert result["status"] == "completed"
    assert "Define" in result["completed_stages"]
    assert "AIInterpretation" in result["completed_stages"]
    assert len([x for x in result["trace"] if x["stage"] == "Define"]) == 1
