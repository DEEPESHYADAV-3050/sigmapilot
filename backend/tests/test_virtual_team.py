from fastapi.testclient import TestClient

from app import db, main
from app.services import ingestion


def test_virtual_team_review_produces_roles_gates_actions_and_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "team.sqlite3")
    monkeypatch.setattr(ingestion, "UPLOAD_DIR", tmp_path / "uploads")
    ingestion.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    with TestClient(main.app) as client:
        project_response = client.post("/api/projects", json={
            "name": "Reduce cycle time", "process_name": "Order fulfillment",
            "problem_statement": "Cycle time is variable across shifts.",
            "goal_statement": "Reduce median cycle time by an agreed target.",
            "owner": "Operations", "priority": "High"
        })
        assert project_response.status_code == 201, project_response.text
        project_id = project_response.json()["id"]

        upload = client.post("/api/files/upload", files={
            "file": ("cycle.csv", b"cycle_time,shift\n12.1,A\n11.8,B\n12.4,A\n10.9,B\n11.7,A\n", "text/csv")
        })
        assert upload.status_code == 201, upload.text
        dataset_id = upload.json()["id"]

        review_response = client.post("/api/team/review", json={
            "project_id": project_id,
            "dataset_id": dataset_id,
            "question": "Review the project as a full Six Sigma team."
        })
        assert review_response.status_code == 201, review_response.text
        review = review_response.json()
        assert len(review["team_members"]) == 9
        assert review["ai_team_synthesis"]["provider_status"] in {"not_configured", "provider_error_fallback", "configured_response"}
        assert [g["phase"] for g in review["dmaic_gates"]] == ["Define", "Measure", "Analyze", "Improve", "Control"]
        assert review["action_register"]
        assert review["raci"]
        assert "not nine actual professionals" in " ".join(review["limitations"])

        readback = client.get(f"/api/team/reviews/{review['review_id']}")
        assert readback.status_code == 200
        assert readback.json()["review_id"] == review["review_id"]
        action_id = review["action_register"][0]["id"]
        update = client.patch(f"/api/team/reviews/{review['review_id']}/actions/{action_id}", json={"status": "in_progress", "note": "Owner has started the charter work."})
        assert update.status_code == 200, update.text
        assert next(a for a in update.json()["action_register"] if a["id"] == action_id)["status"] == "in_progress"
        report = client.get(f"/api/reports/team/{review['review_id']}")
        assert report.status_code == 200
        assert "Prioritized action register" in report.text
        pdf = client.get(f"/api/reports/team/{review['review_id']}?format=pdf")
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
        history = client.get("/api/team/reviews")
        assert history.status_code == 200 and len(history.json()) == 1


def test_virtual_team_rejects_unknown_project(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "team.sqlite3")
    with TestClient(main.app) as client:
        response = client.post("/api/team/review", json={"project_id": "missing-project"})
        assert response.status_code == 400
        assert "Project not found" in response.text
