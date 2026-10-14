import pytest
from app.services.statistics import analyze_measurements, defect_metrics, mean_confidence_interval, normality_diagnostic, welch_two_sample_test


def test_descriptive_statistics_and_confidence_interval():
    result = analyze_measurements([1, 2, 3, 4, 5])
    assert result["n"] == 5
    assert result["mean"] == 3.0
    assert result["median"] == 3.0
    assert result["min"] == 1.0
    assert result["max"] == 5.0
    assert result["mean_confidence_interval_95"]["low"] < 3
    assert result["mean_confidence_interval_95"]["high"] > 3


def test_capability_requires_valid_limits_and_uses_mr_sigma():
    result = analyze_measurements([10, 11, 10, 12, 11, 9, 10, 11], lsl=8, usl=14)
    assert result["specification"]["cp"] is not None
    assert result["specification"]["cpk"] is not None
    assert result["specification"]["out_of_spec_count"] == 0
    with pytest.raises(ValueError, match="LSL must"):
        analyze_measurements([1, 2, 3], lsl=5, usl=4)


def test_nonfinite_values_are_excluded_and_reported():
    result = analyze_measurements([1, 2, float("nan"), float("inf")])
    assert result["n"] == 2
    assert result["missing_or_nonfinite_count"] == 2


def test_normality_diagnostic_small_sample():
    assert normality_diagnostic([1, 2])["status"] == "insufficient_data"


def test_defect_metrics_keep_yield_and_compute_dpmo():
    yield_result = defect_metrics(defective_values=[0, 1, 0, 0])
    dpmo_result = defect_metrics(defect_counts=[0, 1, 0, 2], opportunities_per_unit=5)
    assert yield_result["yield"] == 0.75
    assert dpmo_result["defect_count"] == 3
    assert dpmo_result["dpmo"] == 150000.0
    assert dpmo_result["sigma_estimate"] is not None
    with pytest.raises(ValueError, match="whole-number"):
        defect_metrics(defect_counts=[0, -1, 1])


def test_welch_test_returns_ci_and_p_value():
    result = welch_two_sample_test([1, 2, 2, 3, 2], [8, 9, 10, 9, 11])
    assert result["test"].startswith("Welch")
    assert result["p_value"] < 0.05
    assert result["confidence_interval"]["low"] < result["difference_a_minus_b"] < result["confidence_interval"]["high"]


def test_welch_rejects_degenerate_zero_variance_groups():
    with pytest.raises(ValueError, match="zero within-group variance"):
        welch_two_sample_test([5, 5, 5], [5, 5, 5])


def test_sigma_estimate_is_withheld_for_invalid_defect_rate():
    result = defect_metrics(defect_counts=[2, 2], opportunities_per_unit=1)
    assert result["dpmo"] == 2_000_000.0
    assert result["sigma_estimate"] is None
    assert any("verify the defect/opportunity definitions" in note for note in result["assumptions"])
