# Raw-data validation challenge

Upload `raw_data_validation_challenge.csv` and run an analysis with:

- Measurement column: `measurement`
- LSL: `9`
- USL: `11`
- Defective-unit column: `defective`
- Defect-count column: `defects`
- Opportunities per unit: `3`
- Group column: `shift`; compare `A` with `B`

## Expected checks

These expected values are deliberately documented so the agent can be evaluated against ground truth rather than judged by how polished its report looks.

- Raw rows: **7**.
- Exact duplicate rows: **1** (must be flagged, not automatically deleted).
- Valid finite measurements: **5**.
- Measurement values that fail numeric conversion: **1** (`bad-value`).
- Missing measurement values: **1** (blank final measurement).
- For valid measurements `[10.0, 10.1, 10.2, 100.0, 10.0]`, mean is **28.06**.
- With LSL 9 and USL 11, observed out-of-spec count is **1**, rate **0.2**. The 100.0 value is not silently removed as an outlier.
- Defective flags contain 2 defective units out of 7, so observed yield is **5/7 ≈ 0.714286**.
- The defect-count column includes a negative value (`-1`). DPMO and sigma estimate must therefore be **withheld**, with an explicit data-quality explanation, rather than computed after silently dropping or accepting the invalid value.
- The report must explain that the IQR outlier flag is a screening signal, not proof of a data error; that row order is assumed for I-MR; and that a group comparison does not establish causation.

This challenge is intentionally dirty. It tests raw-data validation, arithmetic, evidence discipline, limitation reporting, and whether the application avoids turning a questionable dataset into a confident but misleading conclusion.
