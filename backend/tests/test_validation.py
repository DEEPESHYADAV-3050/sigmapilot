import pandas as pd
from app.services.validation import audit_raw_dataframe, validate_calculated_results
from app.services.statistics import analyze_measurements


def test_raw_data_audit_flags_missing_conversion_duplicates_and_outliers():
    frame = pd.DataFrame({
        "measurement": [10.0, 10.1, "bad-value", None, 10.2, 100.0],
        "defects": [0, 1, 0, 0, -1, 2],
        "shift": ["A", "A", "A", "B", "B", "B"],
    })
    # Add an exact duplicate row to verify that duplicates are flagged, never deleted.
    frame = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    result = audit_raw_dataframe(frame, "measurement", defect_count_column="defects", group_column="shift")
    assert result["row_count"] == 7
    assert result["exact_duplicate_rows"] == 1
    assert result["measurement"]["valid_finite_numeric_count"] == 5
    assert result["measurement"]["numeric_conversion_failure_count"] == 1
    assert result["measurement"]["invalid_or_missing_count"] == 2
    assert result["defect_count"]["negative_count"] == 1
    assert result["defect_count"]["valid_for_count_metrics"] is False
    assert result["warnings"]
    assert result["status"] == "warnings"


def test_calculation_crosscheck_passes_for_known_dataset():
    frame = pd.DataFrame({"measurement": [1, 2, 3, 4, 5]})
    result = analyze_measurements(frame["measurement"], lsl=0, usl=6)
    config = {"value_column": "measurement", "lsl": 0, "usl": 6}
    checked = validate_calculated_results(frame, config, result)
    assert checked["status"] == "passed"
    assert checked["failed_check_count"] == 0


def test_calculation_crosscheck_catches_mismatched_reported_value():
    frame = pd.DataFrame({"measurement": [1, 2, 3, 4, 5]})
    result = analyze_measurements(frame["measurement"])
    result["mean"] = 999
    checked = validate_calculated_results(frame, {"value_column": "measurement"}, result)
    assert checked["status"] == "failed"
    assert any(c["metric"] == "mean" and c["status"] == "failed" for c in checked["checks"])


def test_calculation_crosscheck_recomputes_dpmo():
    frame = pd.DataFrame({"measurement": [1, 2, 3], "defects": [0, 1, 2]})
    result = analyze_measurements(frame["measurement"])
    from app.services.statistics import defect_metrics
    result["defects"] = defect_metrics(defect_counts=frame["defects"], opportunities_per_unit=2)
    config = {"value_column": "measurement", "defect_count_column": "defects", "opportunities_per_unit": 2}
    checked = validate_calculated_results(frame, config, result)
    assert checked["status"] == "passed"
    result["defects"]["dpmo"] = 1
    checked = validate_calculated_results(frame, config, result)
    assert checked["status"] == "failed"
    assert any(c["metric"] == "dpmo" and c["status"] == "failed" for c in checked["checks"])


def test_documented_raw_data_challenge_known_answers():
    from pathlib import Path
    from app.services.statistics import defect_metrics
    challenge = Path(__file__).resolve().parents[2] / "data" / "raw_data_validation_challenge.csv"
    frame = pd.read_csv(challenge)
    quality = audit_raw_dataframe(frame, "measurement", "defective", "defects", "shift")
    analysis = analyze_measurements(pd.to_numeric(frame["measurement"], errors="coerce"), lsl=9, usl=11)
    flags = defect_metrics(defective_values=frame["defective"].tolist())
    assert len(frame) == 7
    assert quality["exact_duplicate_rows"] == 1
    assert quality["measurement"]["valid_finite_numeric_count"] == 5
    assert quality["measurement"]["numeric_conversion_failure_count"] == 1
    assert quality["measurement"]["source_missing_count"] == 1
    assert analysis["mean"] == 28.06
    assert analysis["specification"]["out_of_spec_count"] == 1
    assert analysis["specification"]["out_of_spec_rate"] == 0.2
    assert flags["yield"] == round(5 / 7, 6)
    assert quality["defect_count"]["valid_for_count_metrics"] is False
