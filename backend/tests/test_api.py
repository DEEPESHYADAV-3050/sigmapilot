from fastapi.testclient import TestClient

from app import db
from app import main
from app.services import ingestion


def test_full_local_workflow(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.sqlite3")
    monkeypatch.setattr(ingestion, "UPLOAD_DIR", tmp_path / "uploads")
    ingestion.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    with TestClient(main.app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["database"] == "ok"

        project = client.post("/api/projects", json={
            "name": "Reduce packaging variation", "process_name": "Packaging",
            "problem_statement": "Measured package weights vary.", "goal_statement": "Understand and reduce variation.",
            "owner": "QA", "priority": "High"
        })
        assert project.status_code == 201, project.text
        project_id = project.json()["id"]
        entry = client.post(f"/api/projects/{project_id}/entries", json={
            "phase": "Define", "entry_type": "hypothesis", "title": "Check filler settings", "content": "Candidate cause only; not verified."
        })
        assert entry.status_code == 201
        assert len(client.get(f"/api/projects/{project_id}").json()["entries"]) >= 2

        csv_data = b"weight,shift,defects,defective\n10.0,A,0,0\n10.2,A,1,1\n9.8,A,0,0\n10.1,B,1,1\n10.3,B,0,0\n9.9,B,0,0\n"
        upload = client.post("/api/files/upload", files={"file": ("weights.csv", csv_data, "text/csv")})
        assert upload.status_code == 201, upload.text
        dataset = upload.json()
        assert dataset["kind"] == "dataset"
        assert dataset["row_count"] == 6
        assert "weight" in dataset["columns"]

        analysis = client.post(f"/api/datasets/{dataset['id']}/analysis", json={
            "value_column": "weight", "lsl": 9.5, "usl": 10.5,
            "defective_column": "defective", "defect_count_column": "defects", "opportunities_per_unit": 3,
            "group_column": "shift", "group_a": "A", "group_b": "B"
        })
        assert analysis.status_code == 201, analysis.text
        analysis_json = analysis.json()
        assert analysis_json["result"]["n"] == 6
        assert analysis_json["result"]["specification"]["cpk"] is not None
        assert abs(analysis_json["result"]["defects"]["yield"] - (4 / 6)) < 1e-5
        assert analysis_json["result"]["defects"]["dpmo"] is not None
        assert "group_comparison" in analysis_json["result"]

        report = client.get(f"/api/reports/analysis/{analysis_json['id']}")
        assert report.status_code == 200
        assert "Calculated results" in report.text
        pdf = client.get(f"/api/reports/analysis/{analysis_json['id']}?format=pdf")
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")

        agent = client.post("/api/agent/run", json={
            "project_id": project_id, "dataset_id": dataset["id"], "analysis_run_id": analysis_json["id"],
            "prompt": "Review the process evidence and recommend next actions."
        })
        assert agent.status_code == 201, agent.text
        result = agent.json()
        assert result["status"] == "completed"
        assert all(stage in result["completed_stages"] for stage in ["Define", "Measure", "Analyze", "Improve", "Control"])
        assert len(result["verified_findings"]) >= 3
        assert result["ai_interpretation"]["provider_status"] in {"not_configured", "provider_error_fallback", "configured_response"}
        agent_report = client.get(f"/api/reports/agent/{result['run_id']}?format=pdf")
        assert agent_report.status_code == 200 and agent_report.content.startswith(b"%PDF")
        agent_report_md = client.get(f"/api/reports/agent/{result['run_id']}?format=markdown")
        assert agent_report_md.status_code == 200
        assert "Raw-data quality audit" in agent_report_md.text
        assert "Calculation validation" in agent_report_md.text

        doc = client.post("/api/files/upload", files={"file": ("notes.txt", b"Supplier batch B changed on Monday.", "text/plain")})
        assert doc.status_code == 201, doc.text
        search = client.get("/api/documents?q=supplier")
        assert search.status_code == 200 and len(search.json()) == 1
        assert client.get("/api/audit").json()
        manual = client.post("/api/datasets/manual-csv", json={"filename": "manual.csv", "csv_text": "cycle_time,team\n12.1,A\n11.8,B\n"})
        assert manual.status_code == 201, manual.text
        assert manual.json()["row_count"] == 2
        assert client.get("/api/dashboard").json()["datasets"] == 2


def test_raw_data_audit_and_calculation_validation_are_in_report(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "validation.sqlite3")
    monkeypatch.setattr(ingestion, "UPLOAD_DIR", tmp_path / "validation_uploads")
    ingestion.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    csv_data = (
        b"measurement,defects,defective,shift\n"
        b"10,0,0,A\n"
        b"10.1,1,1,A\n"
        b"bad,0,0,B\n"
        b"10.2,-1,0,B\n"
        b"100,2,1,B\n"
    )
    with TestClient(main.app) as client:
        upload = client.post("/api/files/upload", files={"file": ("raw_audit.csv", csv_data, "text/csv")})
        assert upload.status_code == 201, upload.text
        ds = upload.json()
        response = client.post(f"/api/datasets/{ds['id']}/analysis", json={
            "value_column": "measurement", "lsl": 9, "usl": 11,
            "defective_column": "defective", "defect_count_column": "defects",
            "opportunities_per_unit": 3, "group_column": "shift", "group_a": "A", "group_b": "B"
        })
        assert response.status_code == 201, response.text
        result = response.json()["result"]
        assert result["data_quality"]["status"] == "warnings"
        assert result["data_quality"]["measurement"]["numeric_conversion_failure_count"] == 1
        assert result["calculation_validation"]["status"] in {"passed", "partial"}
        assert result["defects"]["dpmo"] is None
        assert result["defects"]["status"] == "withheld_due_to_data_quality"
        report = client.get(f"/api/reports/analysis/{response.json()['id']}")
        assert report.status_code == 200
        assert "Raw-data quality audit" in report.text
        assert "Independent calculation cross-check" in report.text
        assert "DPMO and sigma estimate were withheld" in report.text
