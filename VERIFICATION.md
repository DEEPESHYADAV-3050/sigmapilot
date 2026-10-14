# SigmaPilot Verification Record — Full DMAIC Case Workspace

Verification performed on the upgraded snapshot that adds multi-file/ZIP case ingestion, task-prompt and charter parsing, DMAIC file-role mapping, Gauge R&R, factorial pilot, pre/post comparison, p-chart, financial forecast, and independent arithmetic cross-checks.

## Checks that passed

- Python compilation: `python -m compileall -q backend/app` — passed.
- Automated backend tests: `PYTHONPATH=backend pytest -q backend/tests` — **33 passed**.
- Frontend source syntax: TypeScript compiler parse check for `App.jsx`, `main.jsx`, and `api.js` — passed.
- Full case-pack test: uploaded the bundled ZIP with 13 files; parser mapped 9 data tables and indexed the charter/task/reference documents.
- Baseline calculations: n=100, mean 20.0033 mm, sample SD approximately 0.041537 mm, 8/100 out of spec, 8% defect rate, 92% yield, 80,000 DPMO assuming one CTQ opportunity per unit.
- Defect-flag reconciliation: all 100 baseline and post-change flags matched strict `< LSL` / `> USL` checks for the supplied synthetic case.
- Independent calculation checks: **15 checks passed, 0 failed** for baseline/post arithmetic, Gauge R&R variance-component sum, factorial effects recomputed from direct cell means, p-chart center line, and financial forecast arithmetic.
- Gauge R&R: crossed 10-part × 3-appraiser × 2-trial design parsed and decomposed with classical crossed ANOVA variance components.
- 2×2 pilot: main effects and interaction estimates, standard errors, 95% confidence intervals, and p-values generated.
- Pre/post analysis: 8% to 1% observed defect rate; Fisher exact test selected for sparse counts; Newcombe-style confidence interval for the absolute difference included.
- Control data: 30 subgroups / 3,000 units / 73 defects; supplied daily defect rates reconciled; no p-chart points outside the implemented 3-sigma limits.
- Financial calculation: ₹1,800,000 gross annual savings forecast and ₹1,740,000 first-year net forecast from the supplied assumptions; explicitly labeled forecast, not realized savings.
- API integration: case upload, report persistence, JSON/Markdown/PDF export endpoints tested.
- A deliberately corrupted daily defect-rate value was detected by reconciling supplied rates against raw defect counts. PDF exports verified by `%PDF` signature.
- Invalid defect-flag mismatch, zero observed defects, invalid ZIP, and report JSON serialization edge cases tested.
- ZIP archive integrity: verified after packaging.

## Frontend build caveat

- A Vite production build and browser-level visual interaction test remain **not verified**. `npm install` timed out in this environment and dependencies were not installed, so this is not claimed as a successful frontend build.
- No real NVIDIA NIM or other hosted-model request was made. The optional AI synthesis path is tested with the provider-unconfigured fallback; add a key and validate your chosen model separately.

## Important interpretation caveats

- The bundled case pack explicitly contains synthetic demonstration data. Its results validate application behavior only, not real manufacturing performance.
- The 2×2 pilot estimates do not prove the intervention caused a change. The post sample is nonrandomized and small; the sample-level target is not proof of sustained process improvement.
- Gauge R&R calculations assume a complete balanced crossed design. Acceptance depends on intended use, customer requirements, part selection, and measurement context.
- The p-chart implements points beyond 3-sigma limits only; run/trend rules are not implemented. Control limits are not specification limits.
- Capability indices remain provisional until measurement-system adequacy, process stability, and distribution/sampling assumptions are acceptable.
- Financial values are forecasts, not realized savings; Finance must validate inputs and actual outcomes.
- The calculation cross-checks use separate arithmetic formulas over the same source rows. They do not prove that source data is true, representative, or collected correctly.
- Production deployment still requires SSO/RBAC, tenant isolation, managed secrets, tamper-evident audit storage, backup/recovery, malware scanning, and security review.

## Repeat locally

```bash
python -m compileall -q backend/app
PYTHONPATH=backend python -m pytest -q backend/tests
```

After dependencies are installed:

```bash
cd frontend
npm install
npm run build
```
