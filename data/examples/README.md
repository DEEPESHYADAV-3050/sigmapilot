# Multi-file DMAIC test package

Upload `six_sigma_agent_test_package.zip` in the application's **Full DMAIC case run** workspace. It intentionally includes multiple interrelated datasets and reference files—not just one CSV:

- Project charter, task prompt, README, manifest, and data dictionary
- Baseline and post-improvement measurement datasets
- Production context and root-cause investigation dataset
- Crossed Gauge R&R measurements
- 2×2 improvement pilot
- Daily subgroup quality data for a p-chart
- Financial forecast assumptions

All records and company details are synthetic. This pack is intended to test file mapping, calculations, calculation cross-checks, limitations, and report generation. It is not a real manufacturing study.

The app should parse nine data tables and at least two reference documents from this ZIP. It should identify the baseline as 100 valid observations, 8 observed defects, an 8% defect rate, 92% yield, and 80,000 DPMO under the explicit one-opportunity-per-unit assumption. It should report the post sample as 1/100 out of spec and the pre/post difference as descriptive only. It should not claim the pilot proves a causal improvement or that forecast savings were realized.
