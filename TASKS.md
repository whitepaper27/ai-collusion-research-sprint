# TASKS

Ordered work list for `sol.md` v5.2. Each task advances a GOAL.md definition-of-done line or
milestone. Status: `todo` · `doing` · `done` · `blocked` · `post-sprint` (paper phase, after the fixture-only sprint submission). Owner: `agent` or `human` (Sahil).
Waves run in parallel worktrees (≤3 workers at a time); everything else is sequential.

| ID | Task | Owner | Depends on | Wave | Done when | Status |
|---|---|---|---|---|---|---|
| T0 | Owner decisions: build authorized, paid runs on go, spend caps, Docker use, actor model | human | – | – | Recorded in GOAL.md | done |
| T0b | Record account pricing basis + auditor model ID; update sol.md lines 4 and 11 | human | – | – | GOAL.md box ticked | todo |
| T1 | Reconcile docs with v5.2: GOAL, AGENTS, CLAUDE, ARCHITECTURE, TASKS; archive old world plan; `.gitattributes`; `.env.example` | agent | T0 | – | `verify.sh` green; merged to main by Sahil | done |
| T2 | Specs `docs/specs/*.md` with requirement IDs + `tests/test_traceability.py` | agent | T1 | – | Traceability test green | done |
| T3 | Walking skeleton: draft contract, one mock episode per condition through the real path; packaging + deps; tag `contract-v1` | agent | T2 | – | Skeleton tests green, tag exists | done |
| T4 | W1 world + scoring (spec `world-scoring.md`) | agent | T3 | 1 | Every R-WLD/R-SCR ID tested; verify green | done |
| T5 | W2 services + enforcement (spec `services-enforcement.md`) | agent | T3 | 1 | Every R-SVC/R-ENF ID tested; verify green | done |
| T6 | W3 runtime + models + prompts + spend (spec `runtime.md`) | agent | T3 | 1 | Every R-RUN/R-MOD/R-PRM/R-SPD ID tested | done |
| T7 | W4 recorder + freeze + Docker (spec `recorder-freeze.md`) | agent | T3 | 2 | Every R-REC/R-FRZ/R-DKR ID tested | done |
| T8 | W5 evaluate + analysis (spec `evaluate-analysis.md`) | agent | T3 | 2 | Every R-EVL/R-ANL ID tested | done |
| T9 | W6 audit fixtures, views, auditor runner, labels, metrics (spec `audit.md`) | agent | T3 | 2 | Every R-AUD ID tested | done |
| T10 | Integration: merge waves, CLI (spec `cli-integration.md`), e2e mock suite, Docker clean room, reviewer pass | agent | T4–T9 | – | **Sun 6 AM milestone**: mock episodes + exports + tests green | done |
| T10b | Mock smoke (seeds 100–102 × 2 + controls 150–151 in mock mode) + full e2e suite + freeze create/validate dry run end to end | agent | T10 | – | All green; **gate for any paid-run planning** | done |
| T10c | Apply pre-freeze owner decisions (h11, pair-5, rubric wording, pair-4, first-record rule, private dir); refreeze roster; ledger lock (R-SPD-06) | agent | T10b | – | Roster verifies; gate green | done |
| T14a | **Sprint:** auditor on 6 dev fixtures (≤24 calls) → freeze auditor → Sahil labels 12 held-out → held-out auditor (48 calls) → Figure 1 + fact sheet | human go + agent | T10c, T0b | – | Labels before predictions; ≤$10 | todo |
| T11 | Smoke (6) + instructed controls (4) live; cost projection; choose 20 or 12 main | human go, agent runs | T10b, T0b | – | Within $8; decision recorded before main outcomes | post-sprint |
| T12 | Freeze protocol (`ethwall freeze`) | human | T11 | – | Freeze ID committed; **Sun 9 AM** | post-sprint |
| T13 | Main runs (seeds 200–209 × 2, or 200–205 fallback) | human go, agent runs | T12 | – | Roster complete or stopped at cutoff; **Sun 1 PM** | post-sprint |
| T14 | Human labels → auditor dev → frozen auditor → held-out + main | human + agent | T13, T9 | – | Labels before predictions; within $10 | todo |
| T15 | Figures 1–3, tables, verified fact sheet | agent | T14 | – | Generated from recorded evidence; **Sun 5 PM** | todo |
| T16 | Report in official template (≤8 pages, limitations/dual-use appendix) | human | T15 | – | Checked against evidence; **Sun 10 PM** | todo |
| T17 | Submit; record confirmation | human | T16 | – | **Sun 11:30 PM** | todo |

Owner decisions to settle before the freeze: `docs/decisions/pre-freeze-owner-decisions.md`.

## Fallbacks (decided before seeing main outcomes)

- Smoke cost projection exceeds $25 for 20 main episodes → main seeds 200–205 (12 episodes).
- Still doesn't fit, or live execution isn't viable at Sun 9 AM → fixture-only study (T9 + T14
  on fixtures, T15 figures with live panels marked absent).

## Optional (only after T10 and with time left)

Auditor calls on smoke/control episodes; extra robustness tests. Everything else listed as out
of scope in GOAL.md stays out.
