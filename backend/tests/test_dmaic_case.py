from __future__ import annotations
import json
from pathlib import Path
import zipfile
import io
import pandas as pd

from app.services.dmaic_case import analyze_dmaic_case, report_to_markdown

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHALLENGE_ZIP = PROJECT_ROOT / "data" / "examples" / "six_sigma_agent_test_package.zip"


def run_challenge():
    return analyze_dmaic_case([(CHALLENGE_ZIP.name, CHALLENGE_ZIP.read_bytes())], "Run all requested DMAIC tasks from the uploaded prompt and charter. Validate calculations and separate assumptions from findings.")


def test_full_multifile_case_pack_maps_all_datasets_and_documents():
    report = run_challenge()
    assert report["data_provenance"]["parsed_data_tables"] == 9
    assert report["data_provenance"]["documents_indexed"] >= 2
    assert report["data_provenance"]["synthetic_data_detected"] is True
    assert report["baseline_analysis"]["source"].endswith("baseline_diameter.csv")
    assert report["measurement_system_analysis"]["status"] == "calculated"
    assert report["improvement_pilot"]["status"] == "calculated"
    assert report["post_improvement_analysis"]["status"] == "calculated"
    assert report["process_control"]["status"] == "calculated"
    assert report["financial_forecast"]["status"] == "forecast_only"
    assert report["embedded_task_prompt_sources"]
    assert report["calculation_validation"]["status"] == "passed"
    assert report["calculation_validation"]["check_count"] >= 12
    assert report["calculation_validation"]["failed_check_count"] == 0
    assert report["charter_assumption_validation"]["baseline_assumption_consistent_with_sample"] is True
    assert report["charter_assumption_validation"]["post_sample_at_or_below_target"] is True
    assert report["data_dictionary"]["fields"]
    assert all("dmaic_role" in item for item in report["data_provenance"]["file_inventory"])


def test_baseline_calculations_and_defect_flag_validation():
    report = run_challenge()
    base = report["baseline_analysis"]
    assert base["n_valid"] == 100
    assert base["mean"] == 20.0033
    assert base["specification"]["out_of_spec_count"] == 8
    assert base["specification"]["defect_rate"] == 0.08
    assert base["specification"]["yield"] == 0.92
    assert base["dpmo_one_opportunity_per_unit"] == 80000
    assert base["defect_flag_validation"]["status"] == "passed"
    assert base["capability"]["status"] == "provisional_only"
    assert base["sigma_benchmark_status"] == "calculated_with_1_5_shift_convention"


def test_measurement_pilot_control_financial_and_prepost_are_reported_with_caveats():
    report = run_challenge()
    gauge = report["measurement_system_analysis"]
    assert gauge["parts"] == 10 and gauge["appraisers"] == 3 and gauge["trials_per_cell"] == 2
    assert gauge["percent_study_variation"]["total_gauge_rr"] is not None
    assert gauge["percent_tolerance"] is not None
    pilot = report["improvement_pilot"]
    assert set(pilot["effects"]) == {"factor_a_main_effect", "factor_b_main_effect", "interaction_effect"}
    assert all("ci_95" in value and "p_value" in value for value in pilot["effects"].values())
    assert report["process_control"]["subgroups"] == 30
    assert report["process_control"]["supplied_rate_validation"]["status"] == "passed"
    comparison = report["pre_post_comparison"]
    assert comparison["baseline_defect_rate"] == 0.08
    assert comparison["post_defect_rate"] == 0.01
    assert comparison["test_name"].startswith("Fisher exact")
    assert comparison["absolute_reduction_ci_95"]
    assert report["financial_forecast"]["gross_annual_savings_forecast_inr"] == 1800000
    assert "not realized savings" in " ".join(report["financial_forecast"]["caveats"])


def test_report_contains_evidence_limitations_and_actionable_dmaic_plan():
    report = run_challenge()
    markdown = report_to_markdown(report)
    for section in ("Measure", "Analyze", "Improve", "Control", "Gauge R&R", "Pareto", "Limitations", "Prioritized action plan"):
        assert section.lower() in markdown.lower()
    assert "not causal proof" in markdown.lower() or "causation" in markdown.lower()
    assert report["recommended_actions"]
    assert report["dmaic_phase_review"] and len(report["dmaic_phase_review"]) == 5
    assert report["data_provenance"]["shared_unit_id_audit"]


def test_mismatched_defect_flags_are_detected_not_silently_corrected():
    csv_data = b"part_id,diameter_mm,defect\nA,9.8,0\nB,10.0,0\nC,10.2,0\n"
    charter = json.dumps({"project_title":"Flag audit","customer_ctq":{"lsl":9.9,"target":10.0,"usl":10.1,"characteristic":"diameter","unit":"mm"}}).encode()
    report = analyze_dmaic_case([("baseline_diameter.csv", csv_data), ("project_charter.json", charter)], "Audit defect flags against specs.")
    check = report["baseline_analysis"]["defect_flag_validation"]
    assert check["status"] == "mismatch_found"
    assert check["mismatch_count"] == 2


def test_zero_observed_defects_does_not_emit_infinite_json_value():
    csv_data = b"part_id,diameter_mm,defect\nA,10.0,0\nB,10.01,0\nC,9.99,0\n"
    charter = json.dumps({"project_title":"Zero defects","customer_ctq":{"lsl":9.9,"target":10.0,"usl":10.1,"characteristic":"diameter","unit":"mm"}}).encode()
    report = analyze_dmaic_case([("baseline.csv", csv_data), ("project_charter.json", charter)], "Calculate the baseline.")
    assert report["baseline_analysis"]["sigma_benchmark_with_1_5_shift"] is None
    assert "zero_observed_defects" in report["baseline_analysis"]["sigma_benchmark_status"]
    json.dumps(report, allow_nan=False)


def test_bad_zip_fails_with_clear_validation_message():
    try:
        analyze_dmaic_case([("not-a-real.zip", b"not a zip")], "Analyze this package")
    except ValueError as exc:
        assert "valid ZIP" in str(exc)
    else:
        raise AssertionError("Invalid ZIP should be rejected")


def test_case_workspace_api_returns_persistent_report_and_downloads(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import db, main
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "case.sqlite3")
    monkeypatch.setattr(main, "CASE_RUN_DIR", tmp_path / "case_runs")
    main.CASE_RUN_DIR.mkdir(parents=True, exist_ok=True)
    with TestClient(main.app) as client:
        response = client.post(
            "/api/case-workspace/run",
            data={"prompt": "Use the attached charter and run the full DMAIC case. Validate all results."},
            files={"files": (CHALLENGE_ZIP.name, CHALLENGE_ZIP.read_bytes(), "application/zip")},
        )
        assert response.status_code == 201, response.text
        payload = response.json()
        report = payload["report"]
        assert report["run_id"]
        assert report["ai_team_synthesis"]["provider_status"] in {"not_configured", "provider_error_fallback", "configured_response"}
        assert "Measure" in payload["report_markdown"]
        saved = client.get(f"/api/case-workspace/{report['run_id']}")
        assert saved.status_code == 200
        json_export = client.get(f"/api/reports/case/{report['run_id']}?format=json")
        assert json_export.status_code == 200 and json_export.json()["run_id"] == report["run_id"]
        md_export = client.get(f"/api/reports/case/{report['run_id']}?format=markdown")
        assert md_export.status_code == 200 and "Limitations" in md_export.text
        pdf_export = client.get(f"/api/reports/case/{report['run_id']}?format=pdf")
        assert pdf_export.status_code == 200 and pdf_export.content.startswith(b"%PDF")


def test_supplied_daily_defect_rates_are_reconciled_against_counts():
    source = zipfile.ZipFile(CHALLENGE_ZIP)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as altered:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename.endswith("daily_quality.csv"):
                text = content.decode("utf-8")
                lines = text.splitlines()
                cols = lines[0].split(",")
                rate_idx = cols.index("defect_rate")
                first = lines[1].split(",")
                first[rate_idx] = "0.99"  # Deliberately corrupt one supplied summary rate.
                lines[1] = ",".join(first)
                content = ("\n".join(lines) + "\n").encode("utf-8")
            altered.writestr(item.filename, content)
    source.close()
    report = analyze_dmaic_case([("altered_case.zip", buffer.getvalue())], "Reconcile supplied subgroup rates against raw defect counts.")
    validation = report["process_control"]["supplied_rate_validation"]
    assert validation["status"] == "mismatch_found"
    assert validation["mismatch_count"] == 1
