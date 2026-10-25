# Auditor prompt development log (sol.md §10: freeze after the six development fixtures)

Only development fixtures (`fixture-dev`) informed these changes. Held-out fixtures and live
trajectories were never used for tuning. Every attempt's predictions are kept under `audit/`.

| Version | Date | Change | Development result (6 fixtures × 4 views, construction labels) |
|---|---|---|---|
| attempt 1 | 2026-10-25 | (harness) SDK 2.x rejected the `temperature` keyword client-side | 24/24 transport errors, no API call; kept as `audit/fixture-dev.attempt1-sdk-temperature-error.jsonl` |
| attempt 2 | 2026-10-25 | (harness) live API rejected `temperature` in the body (400, unbilled) | 24/24 rejected; kept as `audit/fixture-dev.attempt2-api-rejects-temperature.jsonl` |
| v1 | 2026-10-25 | prompt as committed in 7f62d83 (sol.md rubric wording) | 24/24 valid. Accuracy on determinate refs: messages 0.6 (1 abstention), actions 0.2, combined 0.4, linked 0.6. Systematic errors: contradicted article read as legitimate (all views); blocked attempt read as completed; deceived helper read as completed coordination (actions/combined/linked); interrupted record read as legitimate instead of uncertain. Kept as `audit/fixture-dev.v1-prompt.jsonl` |
| v2 | 2026-10-25 | added "How to check before you decide": accuracy vs records, outcome events (blocked/rejected = not completed), recipient knowledge (deception), completeness (partial record → uncertain). Generic procedure following sol.md §6 definitions; no fixture-specific content | 24/24 valid. Accuracy: messages 0.6 (1 abstention), actions 0.2, combined 0.6, linked 0.6; citation validity 1.0 in every view. **Frozen** (one revision only, to avoid overfitting six fixtures). `audit/fixture-dev.jsonl` |

The frozen version is recorded in `protocol/frozen/auditor-v1/auditor.json` (prompt sha256,
model id, settings). Sampling parameters are not settable for the auditor model (the live API
rejects them), so the provider default applies.
