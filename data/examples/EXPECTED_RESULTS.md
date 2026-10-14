# Expected results for the bundled multi-file challenge

These are reference checks for `six_sigma_agent_test_package.zip`. All data are synthetic.

- Files in ZIP: 13.
- Parsed data tables: 9.
- Reference/task documents indexed: at least 2.
- Baseline: n=100; mean diameter 20.0033 mm; sample SD approximately 0.041537 mm; 8 out-of-spec units; 8% defect rate; 92% observed yield; 80,000 DPMO assuming one CTQ opportunity per unit.
- Supplied baseline defect flags: 0 mismatches against strict `diameter < 19.90` or `diameter > 20.10` logic.
- Baseline capability: Cp/Cpk and Pp/Ppk may be shown as provisional estimates only. The sample fails the Shapiro–Wilk normality diagnostic at alpha 0.05 and capability still requires stable-process and adequate-MSA checks.
- Pareto: 4 undersize and 4 oversize defects in the baseline sample.
- Gauge R&R: 10 parts × 3 appraisers × 2 trials; crossed ANOVA decomposition should complete; interpretation is limited to the study design and selected parts.
- 2×2 pilot: three effect estimates (tool replacement, offset verification, interaction) with standard errors, 95% CIs, and p-values. The reference estimates have CIs that include zero; do not declare a confirmed improvement from this pilot alone.
- Post-improvement sample: 1/100 out of spec (1%); all supplied post flags match the specifications.
- Pre/post: 8% to 1%, a 7-percentage-point observed reduction; two-sided Fisher exact p-value is approximately 0.0349; Newcombe-style 95% CI for the difference is approximately 1.09 to 14.05 percentage points. The comparison remains nonrandomized and synthetic, so it is not causal proof.
- Daily control data: 30 subgroups, 3,000 units, 73 defects, p-chart center line approximately 0.024333; supplied daily defect rates reconcile; no point is outside 3-sigma limits under the implemented rule. Other run/trend rules are not implemented.
- Financial model: annual gross savings forecast ₹1,800,000 and first-year net forecast ₹1,740,000 under supplied assumptions. These are forecasts, not realized savings.
- Independent arithmetic checks: all should pass for the bundled test package.

A correct report must explicitly identify the synthetic data, the shared unit IDs across baseline/context files, assumptions, limitations, and the distinction between observed results, hypotheses, and forecasts.
