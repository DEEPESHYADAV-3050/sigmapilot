from __future__ import annotations

import math
from typing import Any
import numpy as np
import pandas as pd


def audit_raw_dataframe(frame: pd.DataFrame, value_column: str, defect_column: str | None = None,
                        defect_count_column: str | None = None, group_column: str | None = None) -> dict[str, Any]:
    """Profile raw rows without silently altering or deleting the source dataset."""
    row_count = int(len(frame))
    columns: dict[str, Any] = {}
    warnings: list[str] = []
    duplicate_rows = int(frame.duplicated(keep="first").sum()) if row_count else 0
    if duplicate_rows:
        warnings.append(f"{duplicate_rows} exact duplicate row(s) detected; review whether they are valid repeat observations or accidental duplicates. They were not removed.")
    for name in frame.columns:
        series = frame[name]
        missing = int(series.isna().sum())
        columns[str(name)] = {
            "dtype": str(series.dtype),
            "missing_count": missing,
            "missing_rate": round(missing / row_count, 6) if row_count else None,
            "unique_non_missing": int(series.nunique(dropna=True)),
            "constant_non_missing": bool(series.nunique(dropna=True) <= 1) if series.notna().any() else False,
        }
        if missing:
            warnings.append(f"Column '{name}' has {missing} missing value(s) out of {row_count} rows.")

    if value_column not in frame.columns:
        raise ValueError(f"Column '{value_column}' does not exist in the dataset.")
    raw_measure = frame[value_column]
    numeric_measure = pd.to_numeric(raw_measure, errors="coerce")
    # Distinguish source missingness from non-empty values that failed numeric conversion.
    source_missing = int(raw_measure.isna().sum())
    blank_text = raw_measure.map(lambda v: isinstance(v, str) and not v.strip()).sum()
    conversion_failures = int((numeric_measure.isna() & raw_measure.notna() & ~raw_measure.map(lambda v: isinstance(v, str) and not v.strip())).sum())
    finite_mask = np.isfinite(numeric_measure.to_numpy(dtype=float, na_value=np.nan))
    nonfinite_count = int((numeric_measure.notna().to_numpy() & ~finite_mask).sum())
    valid_count = int(finite_mask.sum())
    measurement = {
        "column": value_column,
        "row_count": row_count,
        "valid_finite_numeric_count": valid_count,
        "source_missing_count": source_missing,
        "blank_text_count": int(blank_text),
        "numeric_conversion_failure_count": conversion_failures,
        "nonfinite_numeric_count": nonfinite_count,
        "invalid_or_missing_count": row_count - valid_count,
        "unique_finite_values": int(numeric_measure.to_numpy(dtype=float, na_value=np.nan)[finite_mask].size and np.unique(numeric_measure.to_numpy(dtype=float, na_value=np.nan)[finite_mask]).size) if valid_count else 0,
    }
    if valid_count < row_count:
        warnings.append(f"Measurement column '{value_column}' has {row_count - valid_count} row(s) excluded from numeric calculations; inspect the raw values before relying on the results.")
    if valid_count < 2:
        warnings.append("Fewer than two valid measurements are available; variation and capability estimates may be unavailable.")
    if valid_count:
        x = numeric_measure.to_numpy(dtype=float, na_value=np.nan)[finite_mask]
        q1, q3 = np.quantile(x, [0.25, 0.75])
        iqr = float(q3 - q1)
        low_fence, high_fence = float(q1 - 1.5 * iqr), float(q3 + 1.5 * iqr)
        potential_outliers = int(np.sum((x < low_fence) | (x > high_fence)))
        measurement["iqr_screen"] = {
            "q1": round(float(q1), 6), "q3": round(float(q3), 6), "iqr": round(iqr, 6),
            "lower_fence": round(low_fence, 6), "upper_fence": round(high_fence, 6),
            "potential_outlier_count": potential_outliers,
            "note": "IQR fence is a screening flag, not proof of error; flagged observations were retained."
        }
        if potential_outliers:
            warnings.append(f"The measurement column has {potential_outliers} potential IQR outlier(s); investigate context and data-entry validity. No values were removed.")

    selected_categorical: dict[str, Any] = {}
    for col in [defect_column, group_column]:
        if col and col in frame.columns:
            counts = frame[col].astype("string").fillna("<MISSING>").value_counts(dropna=False).head(25).to_dict()
            selected_categorical[col] = {str(k): int(v) for k, v in counts.items()}
    if group_column and group_column not in frame.columns:
        raise ValueError(f"Group column '{group_column}' does not exist in the dataset.")

    defect_count_audit = None
    if defect_count_column:
        if defect_count_column not in frame.columns:
            raise ValueError(f"Defect-count column '{defect_count_column}' does not exist in the dataset.")
        raw_counts = frame[defect_count_column]
        converted = pd.to_numeric(raw_counts, errors="coerce")
        finite = np.isfinite(converted.to_numpy(dtype=float, na_value=np.nan))
        vals = converted.to_numpy(dtype=float, na_value=np.nan)[finite]
        invalid = row_count - int(finite.sum())
        nonwhole = int(np.sum(vals != np.floor(vals)))
        negative = int(np.sum(vals < 0))
        defect_count_audit = {"column": defect_count_column, "valid_finite_numeric_count": int(finite.sum()),
                              "invalid_or_missing_count": invalid, "negative_count": negative,
                              "non_whole_number_count": nonwhole,
                              "valid_for_count_metrics": invalid == 0 and negative == 0 and nonwhole == 0}
        if invalid or negative or nonwhole:
            warnings.append(f"Defect-count column '{defect_count_column}' contains {invalid} missing/non-numeric, {negative} negative, and {nonwhole} non-whole value(s); DPMO must not be trusted until these are resolved.")

    if group_column:
        counts = frame[group_column].astype("string").fillna("<MISSING>").value_counts(dropna=False)
        if len(counts) > 25:
            warnings.append(f"Grouping column '{group_column}' has {len(counts)} categories; a two-group comparison may be incomplete or poorly targeted if categories were chosen without context.")

    return {
        "status": "warnings" if warnings else "passed_basic_checks",
        "row_count": row_count,
        "column_count": int(frame.shape[1]),
        "exact_duplicate_rows": duplicate_rows,
        "measurement": measurement,
        "defect_count": defect_count_audit,
        "selected_category_counts": selected_categorical,
        "columns": columns,
        "warnings": list(dict.fromkeys(warnings)),
        "scope_note": "Automated screening only. It cannot establish data provenance, correct units, representative sampling, valid operational definitions, or measurement-system adequacy."
    }


def validate_calculated_results(frame: pd.DataFrame, config: dict, result: dict) -> dict[str, Any]:
    """Recompute a small set of key metrics independently and compare with reported values."""
    checks: list[dict[str, Any]] = []

    def add(name: str, reported: Any, recomputed: Any, tolerance: float = 1e-5):
        if reported is None or recomputed is None:
            checks.append({"metric": name, "status": "not_checkable", "reported": reported, "independently_recomputed": recomputed})
            return
        try:
            ok = math.isclose(float(reported), float(recomputed), rel_tol=tolerance, abs_tol=tolerance)
        except (TypeError, ValueError):
            ok = reported == recomputed
        checks.append({"metric": name, "status": "passed" if ok else "failed", "reported": reported, "independently_recomputed": recomputed})

    col = config.get("value_column")
    if col in frame.columns:
        raw = pd.to_numeric(frame[col], errors="coerce").to_numpy(dtype=float, na_value=np.nan)
        x = raw[np.isfinite(raw)]
        add("finite_measurement_count", result.get("n"), len(x), tolerance=0)
        if len(x):
            add("mean", result.get("mean"), float(np.mean(x)))
            add("median", result.get("median"), float(np.median(x)))
            if len(x) > 1:
                add("sample_std_dev", result.get("sample_std_dev"), float(np.std(x, ddof=1)))
            if config.get("lsl") is not None and config.get("usl") is not None:
                outside = int(np.sum((x < float(config["lsl"])) | (x > float(config["usl"]))))
                spec = result.get("specification") or {}
                add("out_of_spec_count", spec.get("out_of_spec_count"), outside, tolerance=0)
                add("out_of_spec_rate", spec.get("out_of_spec_rate"), outside / len(x))
    defect_count_col = config.get("defect_count_column")
    defects = result.get("defects") or {}
    opportunities = config.get("opportunities_per_unit")
    if defect_count_col and defect_count_col in frame.columns and opportunities:
        raw_counts = pd.to_numeric(frame[defect_count_col], errors="coerce").to_numpy(dtype=float, na_value=np.nan)
        valid_counts = raw_counts[np.isfinite(raw_counts)]
        if len(valid_counts) == len(frame) and np.all(valid_counts >= 0) and np.all(valid_counts == np.floor(valid_counts)):
            add("total_defect_count", defects.get("defect_count"), int(np.sum(valid_counts)), tolerance=0)
            expected_dpmo = float(np.sum(valid_counts)) / (len(valid_counts) * int(opportunities)) * 1_000_000
            add("dpmo", defects.get("dpmo"), expected_dpmo)
        else:
            checks.append({"metric": "dpmo_input_validity", "status": "passed" if defects.get("dpmo") is None else "failed",
                           "reason": "DPMO must be withheld for incomplete or invalid defect-count data."})
    defect_col = config.get("defective_column")
    if defect_col and defect_col in frame.columns:
        vals = frame[defect_col].astype("string").str.strip().str.lower()
        truthy = {"1", "true", "yes", "y", "defective", "fail"}
        falsy = {"0", "false", "no", "n", "good", "pass", ""}
        normalized = vals.map(lambda v: True if v in truthy else False if pd.isna(v) or v in falsy else None)
        if normalized.notna().all() and len(normalized):
            add("yield", defects.get("yield"), 1 - float(normalized.sum()) / len(normalized))
            add("defective_units", defects.get("defective_units"), int(normalized.sum()), tolerance=0)
        else:
            checks.append({"metric": "defective_column_encoding", "status": "not_checkable", "reason": "Unrecognized values prevent independent binary-flag validation."})
    statuses = {item["status"] for item in checks}
    failed = sum(item["status"] == "failed" for item in checks)
    return {"status": "failed" if failed else "passed" if checks and statuses <= {"passed"} else "partial", "checks": checks,
            "failed_check_count": failed,
            "note": "Independent arithmetic cross-checks validate selected formulas against the uploaded rows; they do not validate sampling, units, assumptions, or the correctness of source data."}
