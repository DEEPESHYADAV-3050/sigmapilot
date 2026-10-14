# SigmaPilot — Six Sigma AI Work Agent

SigmaPilot is a local-first prototype designed to act as a coordinated virtual Six Sigma improvement office. In addition to single-dataset analysis and the nine-role virtual team, it now has a **Full DMAIC Case Run** workspace: upload an entire ZIP or multiple CSV/Excel files, project charter, task prompt, data dictionary, SOPs, MSA, pilot, post-change data, control data, and financial assumptions; run one prompt; and receive a connected, evidence-led DMAIC report with file-role mapping, calculation cross-checks, phase gates, action owners, and downloadable Markdown/PDF/JSON.

**It is a complete runnable prototype, not a certified enterprise production deployment.** Authentication/SSO, role-based authorization, a managed secret vault, database migration management, tamper-evident audit storage, antivirus scanning, and organization-specific connector adapters must be completed and reviewed before multi-user or regulated deployment.

## What is included

- **Dashboard:** live project, dataset, analysis, document, and activity counts.
- **Data ingestion:** CSV, XLSX, XLS, PDF, DOCX, TXT, and Markdown. Tabular files become datasets; text-bearing documents are extracted and indexed for local text search.
- **Manual data input:** paste CSV text into the UI or call the manual import endpoint.
- **Read-only connected-source adapter:** one-shot HTTPS JSON import, optional bearer token, no credential persistence, public-host validation, redirects blocked, 15-second timeout, and 5 MB response limit. This is a generic adapter, not a preconfigured connector for a named SaaS system.
- **Deterministic statistics:** descriptive summaries, 95% Student-t confidence interval for the mean, Shapiro–Wilk diagnostic, basic I-MR control limits/signals, Cp/Cpk based on moving-range within-sigma estimate, observed out-of-spec rate, yield, DPMO, conventional 1.5-sigma-shift estimate, and Welch's independent two-sample t-test.
- **Raw-data quality audit:** flags missing and non-numeric measurement values, exact duplicate rows, potential IQR outliers, invalid defect counts, and category-count issues without silently deleting source rows.
- **Calculation validation:** independently recomputes selected core metrics (finite count, mean, median, sample standard deviation, out-of-spec count/rate, and binary-flag yield) and reports passed/failed/not-checkable checks. A mismatch is surfaced rather than hidden.
- **Full DMAIC case workspace:** one prompt plus multiple files or a ZIP package; maps baseline, context, root-cause, Gauge R&R, factorial pilot, post-change, p-chart, data dictionary, charter, and financial files by names and column structure. Supports CSV, TSV, XLSX, XLS, JSON, TXT, Markdown, PDF, DOCX, and ZIP (35 MB total, 150 extracted files maximum).
- **Case-level statistical work:** baseline and post-change descriptive statistics, Wilson defect-rate intervals, defect-flag reconciliation against specifications, one-opportunity DPMO assumption, conventional +1.5-shift yield sigma benchmark, provisional Cp/Cpk/Pp/Ppk, Pareto, exploratory stratified associations, crossed Gauge R&R ANOVA, 2×2 factorial effects and interactions, p-chart with variable sample sizes, charter-vs-sample reconciliation, and financial forecast. Each result states its source and limitations.
- **Independent case calculation cross-checks:** separately recomputes baseline/post counts and rates, mean and sample SD, yield, DPMO, Gauge R&R variance-component sum, factorial effects using direct cell-mean contrasts, p-chart center line, and forecast arithmetic. A failed check is reported rather than hidden.
- **Evidence-rich reports:** analysis reports include raw-data audit results, calculation cross-checks, warnings, assumptions, limitations, source inventory, file-role mapping, and evidence-backed next actions—not just polished narrative.
- **DMAIC management:** project charters, phase status, decisions, actions, risks, hypotheses, metrics, and timestamped log entries stored in SQLite.
- **Bounded agent workflow:** Define → Measure → Analyze → Improve → Control, persisted stage trace, resume after failure, deterministic findings, optional model interpretation, and separate hypotheses/recommendations/risks.
- **Virtual Six Sigma team:** nine role perspectives (sponsor, Black Belt/project lead, process owner/SME, statistician, MSA specialist, root-cause/quality engineer, finance/benefits analyst, change/training lead, and control-plan owner). Each review creates phase gates, blockers, a prioritized action register with role owners and exit evidence, RACI, decision rules, and an optional model-assisted synthesis.
- **Action tracking:** persist action status updates (`not_started`, `in_progress`, `blocked`, `completed`) in saved team reviews and audit changes.
- **Reports:** downloadable Markdown, JSON analysis reports, and PDF analysis/agent reports.
- **Audit trail:** local event records for important actions. The log is not cryptographically tamper-proof.
- **Validation:** automated unit and API integration tests, including a deliberately dirty raw-data challenge dataset with documented expected answers. The current snapshot passes 33 backend tests, including a multi-file synthetic DMAIC package and case-report API integration.

## Evidence and statistical discipline

1. The statistical engine, not the language model, calculates metrics, confidence intervals, p-values, control limits, and capability indices.
2. AI output is kept in a separate interpretation section. It is not a verified finding.
3. The agent does not claim root cause from descriptive statistics or claim an improvement worked without post-change evidence.
4. DPMO requires a numeric defect-count column and a known opportunities-per-unit value. Defect counts and defective units are different concepts. If the selected defect-count column contains missing, non-numeric, negative, or fractional values, DPMO and its sigma estimate are withheld until the source data is resolved.
5. Yield treats each row as a unit and the selected flag as a defective-unit indicator. Supported flag values include boolean, 0/1, yes/no, and pass/fail variants.
6. Cp/Cpk use average moving range divided by 1.128 as the within-process sigma estimate. This is meaningful only with appropriate ordered measurements and a stable process; the application does not certify stability or distribution suitability.
7. The I-MR diagnostic checks 3-sigma limit signals only; supplementary run/trend rules are not implemented. Control limits are not specification limits.
8. A sigma estimate derived from DPMO uses the conventional 1.5-sigma shift and is explicitly a convention-based estimate.
9. The Shapiro–Wilk diagnostic is limited to 3–5,000 finite observations. A non-significant result does not prove normality.
10. The hosted model receives the user's prompt, text extracted from files explicitly identified as task/instruction prompts, and aggregate calculated summaries/metadata. It does **not** receive raw measurement rows or general reference-document contents. Do not send confidential prompts or project metadata to an external model unless your organization approves it.
11. Raw-data screening is not a substitute for source-system reconciliation: duplicates and IQR outliers are flagged, not automatically removed.
12. Calculation cross-checks independently recompute selected arithmetic; they do not validate every statistical assumption or prove the dataset is representative.

## Architecture

```text
React + Vite UI (localhost:5173)
  ├─ Dashboard / Full DMAIC Case Run / Data & Analysis / DMAIC / AI Analyst / Evidence
  └─ FastAPI JSON API (localhost:8000)
       ├─ SQLite repository + local upload storage
       ├─ File ingestion + text extraction
       ├─ Deterministic statistics service (NumPy / Pandas / SciPy)
       ├─ Multi-file DMAIC case mapper + workpaper/report generator
       ├─ Gauge R&R, 2×2 pilot, pre/post, p-chart, financial forecast modules
       ├─ Raw-data quality audit + independent arithmetic cross-checks
       ├─ DMAIC project service + audit events
       ├─ Bounded agent workflow + persisted execution trace
       ├─ Optional OpenAI-compatible chat-completions client
       └─ Markdown / JSON / PDF report generator
```

### Main folder structure

```text
sigmapilot/
├── backend/app/
│   ├── main.py                 # API routes and request validation
│   ├── config.py               # environment configuration
│   ├── db.py                   # SQLite schema and transactions
│   ├── schemas.py              # API request/response contracts
│   └── services/
│       ├── statistics.py       # deterministic calculations
│       ├── validation.py        # raw-data quality audit and calculation cross-checks
│       ├── ingestion.py        # local files, datasets, extracted text
│       ├── connectors.py       # constrained read-only HTTPS JSON adapter
│       ├── projects.py         # DMAIC charter and log persistence
│       ├── agent.py            # bounded workflow, LLM adapter, recovery
│       ├── reports.py          # Single-analysis/agent Markdown reports
│       ├── dmaic_case.py       # Multi-file case mapping, stats, cross-checks, DMAIC report
│       └── pdf_reports.py      # PDF export
├── backend/tests/              # statistical, API, connector, recovery, and full-case tests
├── frontend/src/               # React application and responsive UI
├── scripts/                    # Windows and Unix setup/start scripts
├── data/                       # local SQLite database at runtime
│   └── examples/                # bundled multi-file Six Sigma challenge ZIP + expected answers
├── storage/uploads/            # local uploaded files
├── .env.example                # safe configuration template
└── requirements.txt
```

## Requirements

- Python 3.11+ recommended (3.13 is supported by current dependency releases).
- Node.js 20+ and npm.
- A network connection during initial dependency installation. Runtime statistical analysis and the UI run locally. A hosted AI API key is optional.

## Windows setup

Open PowerShell in the extracted project directory:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup_windows.ps1
.\scripts\start_windows.ps1
```

The startup script opens a backend and frontend terminal. Open `http://127.0.0.1:5173` in your browser. API health is at `http://127.0.0.1:8000/api/health`, and interactive API documentation is at `http://127.0.0.1:8000/docs`.

Manual alternative:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
cd frontend
npm install
npm run dev
```

In a second terminal, from the project root:

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
```

## macOS / Linux setup

```bash
./scripts/setup.sh
./scripts/start.sh
```

Or run the backend and frontend in separate terminals:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
```

```bash
cd frontend
npm install
npm run dev
```

## Run the bundled multi-file Six Sigma challenge

1. Start the application and open **Full DMAIC Case Run**.
2. Upload `data/examples/six_sigma_agent_test_package.zip` (or extract it and select all 13 files).
3. Keep the default prompt or paste `agent_test_prompt.md` as the task prompt.
4. Run the case and inspect file roles, independent calculation checks, DMAIC gates, and limitations.
5. Compare the result with `data/examples/EXPECTED_RESULTS.md`.

The challenge pack intentionally contains a charter, task prompt, data dictionary, baseline, process context, root-cause dataset, crossed Gauge R&R, 2×2 pilot, post-change sample, daily subgroups, and financial assumptions. All values are synthetic.

## Add an API key later

The backend reads `.env` from the project root. No key is required for local deterministic mode. To enable a hosted model, edit `.env` and restart the backend.

NVIDIA NIM example (the defaults are already set; only add the key and confirm that the selected model is enabled for your NIM account):

```dotenv
LLM_API_KEY=your_nvidia_nim_api_key
LLM_BASE_URL=https://integrate.api.nvidia.com/v1
LLM_MODEL=meta/llama-3.3-70b-instruct
LLM_TIMEOUT_SECONDS=45
```

For an OpenAI-compatible provider, set `LLM_BASE_URL` to that provider's `/v1` API base, `LLM_MODEL` to a model available to your account, and `LLM_API_KEY` to its key. Keep `.env` private; it is excluded by `.gitignore`. The frontend never receives the key.

The LLM call is optional and has a timeout. If the provider fails or returns invalid JSON, the workflow keeps the deterministic findings and uses a clearly labelled conservative fallback instead of treating the failed response as evidence.

## Run validation tests

From the project root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q backend\tests
```

or macOS/Linux:

```bash
.venv/bin/python -m pytest -q backend/tests
```

To check frontend source syntax without bundling, the following works if TypeScript is available globally:

```bash
tsc --allowJs --checkJs false --noEmit --jsx react-jsx --module esnext --target es2022 --moduleResolution bundler frontend/src/App.jsx frontend/src/main.jsx frontend/src/api.js --skipLibCheck --noResolve
```

To make a frontend production bundle after dependencies have been installed:

```bash
cd frontend
npm run build
```

## Main API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/health` | Backend/database/LLM configuration health |
| GET | `/api/dashboard` | Dashboard counts and recent activity |
| GET/POST | `/api/projects` | List/create DMAIC projects |
| GET/PATCH | `/api/projects/{id}` | Read/update a project |
| POST | `/api/projects/{id}/entries` | Add a DMAIC log entry |
| POST | `/api/files/upload` | Upload a file or dataset |
| POST | `/api/datasets/manual-csv` | Import pasted/manual CSV text |
| POST | `/api/connectors/http-json/import` | Import records from a public HTTPS JSON endpoint |
| GET | `/api/datasets` | List locally stored datasets |
| GET | `/api/documents?q=...` | Search extracted local document text |
| POST | `/api/datasets/{id}/analysis` | Run deterministic statistical analysis |
| GET | `/api/analysis/{id}` | Read a saved analysis run |
| POST | `/api/case-workspace/run` | Upload multiple files/ZIP + prompt and run complete evidence-led DMAIC analysis |
| GET | `/api/case-workspace/{id}` | Read a saved case report |
| GET | `/api/reports/case/{id}?format=markdown\|json\|pdf` | Export full case report |
| POST | `/api/agent/run` | Start the bounded DMAIC agent workflow |
| POST | `/api/team/review` | Convene the nine-role virtual Six Sigma team |
| GET | `/api/team/reviews` | List saved team reviews |
| GET | `/api/team/reviews/{id}` | Read a saved team review |
| PATCH | `/api/team/reviews/{id}/actions/{action_id}` | Update a team action status |
| GET | `/api/agent/runs` | List persisted agent runs |
| GET | `/api/agent/runs/{id}` | Read a saved run and trace |
| POST | `/api/agent/runs/{id}/resume` | Resume an incomplete/failed run |
| GET | `/api/reports/analysis/{id}?format=markdown\|json\|pdf` | Export analysis report |
| GET | `/api/reports/agent/{id}?format=markdown\|pdf` | Export agent report |
| GET | `/api/reports/team/{id}?format=markdown\|json\|pdf` | Export cross-functional team review |
| GET | `/api/audit` | Read local audit events |

## Recommended end-to-end operating workflow

1. For a complete case pack, open **Full DMAIC Case Run**, upload the project ZIP or select all related files, paste your task prompt, and run the case. Inspect file-role mapping and calculation-validation checks before trusting the report.
2. Create or maintain the persistent DMAIC project record for accountable owners, phase decisions, and ongoing actions.
3. Import the raw CSV/Excel baseline and attach relevant SOPs, process maps, customer requirements, and quality documents as evidence.
4. Run deterministic analysis using the correct outcome column and specification limits. Keep the dataset time-ordered when using control-chart diagnostics.
5. Open **Virtual Six Sigma team**, select the project, dataset, and saved analysis, and run the review.
6. Review the nine specialist perspectives, DMAIC gate blockers, prioritized action register, RACI, and optional AI synthesis. Assign actual people to role owners outside the prototype and update action status as work progresses.
7. Record evidence and decisions in the project log. Re-run the analysis after any pilot; do not call the improvement successful until post-change evidence supports it.
8. Export the team review as Markdown, PDF, or JSON for a real stage-gate meeting. Human owners must approve gate decisions.

The virtual team is intended to reduce coordination overhead and cover the common responsibilities of a Six Sigma project team. It is not nine actual experts, does not perform shop-floor observation or a physical measurement-system study, and cannot authorize production or safety-critical changes.

## Known limitations before production

- The connector is a generic read-only HTTPS JSON adapter. It is not a vendor-specific SAP, Salesforce, Jira, SharePoint, or MES integration. Enterprise connectors need per-system OAuth scopes, rate limits, schema mapping, secret management, and permission reviews.
- The generic connector blocks non-HTTPS URLs, private/local host addresses, redirects, and large responses, but an enterprise egress proxy and security review are still required.
- No user login, SSO, tenant isolation, role-based access, managed database migrations, or multi-user concurrency model is implemented.
- SQLite and the local audit trail are suitable for a single-user prototype, not high-availability production. The audit log is not tamper-proof.
- Document extraction is text-only. Scanned PDFs need OCR, and document access-control labels are not implemented.
- The React frontend is source code and requires `npm install` before `npm run build`. If a package registry is unavailable, dependency installation/build cannot be verified in that environment.
- Statistical outputs are aids to engineering judgment, not a replacement for study design, measurement-system analysis, process-owner review, or quality-system approval.
