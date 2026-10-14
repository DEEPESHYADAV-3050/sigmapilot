from __future__ import annotations
import math
from typing import Any
import numpy as np
from scipy import stats


def _finite(values) -> np.ndarray:
    arr = np.asarray(values, dtype=float).reshape(-1)
    return arr[np.isfinite(arr)]


def _round(value: Any, digits: int = 6):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return round(value, digits) if math.isfinite(value) else None


def mean_confidence_interval(values, confidence: float = 0.95) -> dict:
    x = _finite(values)
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1")
    if len(x) < 2:
        return {"low": None, "high": None, "confidence": confidence, "status": "insufficient_data"}
    mean = float(np.mean(x)); se = float(stats.sem(x)); critical = float(stats.t.ppf((1 + confidence) / 2, len(x) - 1))
    return {"low": _round(mean - critical * se), "high": _round(mean + critical * se), "confidence": confidence, "status": "calculated", "method": "Student t interval for the population mean"}


def normality_diagnostic(values) -> dict:
    x = _finite(values)
    if len(x) < 3:
        return {"status": "insufficient_data", "message": "Shapiro-Wilk requires at least 3 observations."}
    if len(x) > 5000:
        return {"status": "sample_too_large", "message": "Shapiro-Wilk is limited to 5,000 observations in this application; inspect a representative sample or use a planned alternative."}
    if np.all(x == x[0]):
        return {"status": "constant_data", "message": "All values are identical; normality testing is not informative."}
    statistic, p_value = stats.shapiro(x)
    return {"status": "calculated", "test": "Shapiro-Wilk", "statistic": _round(statistic), "p_value": _round(p_value), "interpretation": "Evidence against normality at alpha=0.05; investigate distribution and study design." if p_value < .05 else "This test did not detect evidence against normality at alpha=0.05; it does not prove normality.", "alpha": 0.05}


def imr_chart(values) -> dict:
    x = _finite(values)
    if len(x) < 2:
        return {"status": "insufficient_data", "message": "At least two ordered observations are required."}
    mr = np.abs(np.diff(x)); mrbar = float(np.mean(mr)); center = float(np.mean(x))
    sigma_within = mrbar / 1.128
    ucl = center + 3 * sigma_within; lcl = center - 3 * sigma_within
    mr_ucl = 3.267 * mrbar
    signals = [{"index": int(i), "value": _round(v), "type": "outside_control_limits"} for i, v in enumerate(x) if v > ucl or v < lcl]
    mr_signals = [{"index": int(i + 1), "value": _round(v), "type": "moving_range_above_limit"} for i, v in enumerate(mr) if v > mr_ucl]
    return {"status": "calculated", "chart": "Individuals and Moving Range (I-MR)", "center_line": _round(center), "ucl": _round(ucl), "lcl": _round(lcl), "mr_center_line": _round(mrbar), "mr_ucl": _round(mr_ucl), "sigma_within_estimate": _round(sigma_within), "individual_signals": signals, "moving_range_signals": mr_signals, "assumptions": ["Observations are in meaningful time/order sequence.", "Limits are estimated from the supplied data.", "Only 3-sigma limit signals are checked; supplementary run/trend rules are not implemented.", "Control limits are not specification limits."]}


def analyze_measurements(values, lsl: float | None = None, usl: float | None = None) -> dict:
    raw = np.asarray(values, dtype=float).reshape(-1)
    x = raw[np.isfinite(raw)]
    if len(x) == 0:
        raise ValueError("The selected measurement column contains no finite numeric values.")
    if lsl is not None and usl is not None and lsl >= usl:
        raise ValueError("LSL must be strictly less than USL.")
    if (lsl is not None or usl is not None) and lsl is None:
        raise ValueError("Provide both LSL and USL, or neither.")
    if (lsl is not None or usl is not None) and usl is None:
        raise ValueError("Provide both LSL and USL, or neither.")
    n = len(x); mean = float(np.mean(x)); sd = float(np.std(x, ddof=1)) if n > 1 else None
    q1, median, q3 = np.quantile(x, [.25, .5, .75])
    result = {
        "n": int(n), "missing_or_nonfinite_count": int(len(raw) - n), "mean": _round(mean), "median": _round(median),
        "sample_std_dev": _round(sd), "min": _round(np.min(x)), "max": _round(np.max(x)),
        "q1": _round(q1), "q3": _round(q3), "iqr": _round(q3 - q1),
        "mean_confidence_interval_95": mean_confidence_interval(x),
        "normality": normality_diagnostic(x), "imr": imr_chart(x),
        "specification": None,
        "evidence_notes": ["Metrics are calculated from finite numeric values in the selected column.", "The row order is treated as the process order for the I-MR chart."]
    }
    if lsl is not None and usl is not None:
        outside = (x < lsl) | (x > usl)
        below = int(np.sum(x < lsl)); above = int(np.sum(x > usl))
        imr = result["imr"]
        sigma_w = imr.get("sigma_within_estimate") if imr.get("status") == "calculated" else None
        cp = cpk = cpu = cpl = None
        if sigma_w is not None and sigma_w > 0:
            cp = (usl - lsl) / (6 * sigma_w)
            cpu = (usl - mean) / (3 * sigma_w); cpl = (mean - lsl) / (3 * sigma_w); cpk = min(cpu, cpl)
        result["specification"] = {
            "lsl": float(lsl), "usl": float(usl), "below_lsl_count": below, "above_usl_count": above,
            "out_of_spec_count": int(np.sum(outside)), "out_of_spec_rate": _round(float(np.mean(outside))),
            "cp": _round(cp), "cpk": _round(cpk), "cpu": _round(cpu), "cpl": _round(cpl),
            "capability_status": "calculated_using_moving_range_within_sigma" if cp is not None else "not_calculated_zero_or_unavailable_within_sigma",
            "assumptions": ["LSL/USL are approved specifications in the same units as the measurements.", "Cp/Cpk use within-process sigma estimated from average moving range divided by 1.128.", "Capability interpretation requires a stable process and an appropriate distribution/model; these are not automatically established by this calculation.", "Observed out-of-spec rate is empirical for this sample and does not forecast future performance."]
        }
    return result


def defect_metrics(defective_values=None, defect_counts=None, opportunities_per_unit: int | None = None) -> dict:
    out: dict[str, Any] = {"status": "calculated", "yield": None, "defective_units": None, "defect_count": None, "dpmo": None, "sigma_estimate": None, "assumptions": []}
    if defective_values is not None:
        arr = np.asarray(defective_values)
        normalized = []
        for value in arr:
            if isinstance(value, (bool, np.bool_)):
                normalized.append(bool(value))
            elif str(value).strip().lower() in {"1", "true", "yes", "y", "defective", "fail"}:
                normalized.append(True)
            elif str(value).strip().lower() in {"0", "false", "no", "n", "good", "pass", ""}:
                normalized.append(False)
            else:
                raise ValueError("Defective column must contain booleans or explicit 0/1-like values (0/1, true/false, yes/no, pass/fail).")
        if normalized:
            count = sum(normalized)
            out["defective_units"] = int(count); out["yield"] = _round(1 - count / len(normalized))
            out["unit_count"] = len(normalized)
            out["assumptions"].append("Yield treats each row as one unit and the selected column as a defective-unit flag.")
    if defect_counts is not None:
        vals = _finite(defect_counts)
        if len(vals) == 0 or np.any(vals < 0) or np.any(vals != np.floor(vals)):
            raise ValueError("Defect count column must contain non-negative whole-number counts.")
        out["defect_count"] = int(np.sum(vals)); out["unit_count"] = int(len(vals))
        if opportunities_per_unit is not None:
            total_opportunities = int(len(vals)) * opportunities_per_unit
            defect_rate = float(np.sum(vals)) / total_opportunities
            out["dpmo"] = _round(defect_rate * 1_000_000)
            # Conventional 1.5-sigma-shift conversion is only meaningful for a rate in [0, 1].
            if 0 <= defect_rate < 1:
                p = max(defect_rate, 1e-12)
                out["sigma_estimate"] = _round(float(stats.norm.ppf(1 - p) + 1.5))
                out["assumptions"].append("Sigma estimate uses the conventional 1.5-sigma shift; it is a convention-based estimate, not a directly observed process property.")
            else:
                out["assumptions"].append("Sigma estimate was not calculated because defects divided by opportunities is at least 1; verify the defect/opportunity definitions.")
            out["assumptions"].append("DPMO uses summed defects divided by units × opportunities per unit; defect counts and defective units are distinct.")
    elif opportunities_per_unit is not None:
        raise ValueError("Select a numeric defect-count column before calculating DPMO.")
    return out


def welch_two_sample_test(group_a, group_b, confidence: float = .95) -> dict:
    a = _finite(group_a); b = _finite(group_b)
    if len(a) < 2 or len(b) < 2:
        raise ValueError("Each group needs at least two finite numeric observations.")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1.")
    va, vb = np.var(a, ddof=1), np.var(b, ddof=1); na, nb = len(a), len(b)
    se = math.sqrt(va / na + vb / nb)
    if not math.isfinite(se) or se <= 0:
        raise ValueError("Welch's test is undefined when both groups have zero within-group variance.")
    test = stats.ttest_ind(a, b, equal_var=False)
    denominator = (va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1)
    if denominator <= 0 or not math.isfinite(denominator):
        raise ValueError("Welch's degrees of freedom could not be calculated for these groups.")
    df = (va / na + vb / nb) ** 2 / denominator
    diff = float(np.mean(a) - np.mean(b)); margin = float(stats.t.ppf((1 + confidence) / 2, df) * se)
    return {"status": "calculated", "test": "Welch independent two-sample t-test", "n_a": na, "n_b": nb, "mean_a": _round(np.mean(a)), "mean_b": _round(np.mean(b)), "difference_a_minus_b": _round(diff), "confidence_interval": {"low": _round(diff - margin), "high": _round(diff + margin), "confidence": confidence}, "t_statistic": _round(test.statistic), "p_value": _round(test.pvalue), "interpretation": "Statistically detectable difference at alpha=0.05; evaluate practical importance and design assumptions." if test.pvalue < .05 else "This test did not detect a difference at alpha=0.05; this is not proof the groups are equivalent.", "assumptions": ["Groups are independent.", "Observations within groups are independent and the mean comparison is meaningful.", "Welch's test does not assume equal group variances.", "This test does not establish causation."]}
