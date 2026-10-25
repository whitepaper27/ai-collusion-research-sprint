# GOAL

The file every other decision defers to. The research plan is [`sol.md`](sol.md) **v5.2**
(2026-10-24); this page is its operating summary. If they disagree, `sol.md` wins and this file
gets fixed. System design: [`ARCHITECTURE.md`](ARCHITECTURE.md). Work list: [`TASKS.md`](TASKS.md).

**Owner:** Sahil Soni (sole human reviewer and report author) · **Last updated:** 2026-10-24

## Owner decisions

- [x] **Build authorized for v5.2** (2026-10-24), effective once this file, AGENTS.md and the
      old world plan are reconciled with v5.2 (done in branch `docs/v5.2-setup`). sol.md
      lines 4 and 11 still say "paused"; the owner updates them.
- [x] **Paid runs only on the owner's explicit go.** Agents build and test in `mock`/`fixture`
      mode and never start live calls on their own.
- [x] **Spend caps** (enforced by the harness before every paid call):
      per episode, main $1.25 and smoke/control $0.80; batches below; total $50.
- [x] **Docker** is used for clean-room test runs and offline mock episodes
      (`--network none`). Live runs stay on the host. No sandbox claim beyond what's tested.
- [x] Actor model: Claude Haiku 4.5, exact ID from `ACTOR_MODEL_ID`, frozen before the main run.
- [ ] Owner records the account pricing basis and the auditor model ID before paid runs.

## Mandatory agent instruction

Read this file before research, design, implementation or delegation. Every task names the
TASKS.md item, definition-of-done line or milestone it advances. If it advances none, don't do it.

---

## The goal (one sentence)

By **Monday Oct 26, 2026, 6:59 AM America/Chicago** (target: submitted Sunday 11:30 PM), submit a
reproducible ≤8-page Track 2 report that tests whether **source-linked evidence** distinguishes
coordinated violations from independent mistakes in a synthetic five-role finance team, and what
a **provenance gate** blocks and costs in legitimate work.

## The two questions (frozen as questions; no direction assumed)

- **H1, primary audit comparison:** does the *linked* evidence view change coordination-
  classification errors, false accusations, misses or abstentions compared with the *combined*
  chronological view holding the same information? Fixtures and live trajectories reported
  separately.
- **H2, live intervention:** does provenance enforcement (`guarded`) change attempted/completed
  restricted publication and legitimate-work completion/quality compared with the matched
  `baseline` team?
- Secondary: messages vs actions vs combined views; contradicted/unsupported publication,
  uncertain claims, internal exposure, benign blocks, unsupported approval claims. Separate
  endpoints, never a composite score.

## What a credible result requires (priority order)

1. **Validated audit evidence:** 6 development + 12 held-out fixtures (6 matched pairs),
   four evidence views, Sahil's reference labels made before seeing auditor predictions.
2. **The live comparison measured:** baseline vs guarded on matched seeds, with the full roster
   (scheduled … not started), known/zero/unknown endpoints and intervals.
3. **Controls work:** one legitimate end-to-end mock run, the known-answer detector fixture,
   4 instructed-control episodes, negative controls.
4. **Three figures** from recorded evidence (sol.md §11) and a verified fact sheet.
5. **Honest limits:** sole reviewer, fixture authorship, imperfect blinding, trusted recorder
   key, constrained publication interface, covert channels open, small samples.
6. **Positioned against** Apollo, Multi-Agent AI Control, Colosseum v2, Fides/FLOWSEAL/SafeFlow.

## The claim (do not exceed it)

> Validated source-linked attribution in a finance workflow, including false-accusation cases
> and the cost of a targeted provenance policy.

Not claimed: that information-flow control is new, that evidence views were never compared, that
a profit motive establishes collusion, real legal loopholes, market impact or real-world harm.

## Budget (hard ceiling $50)

| Allocation | Max USD | Episodes / calls |
|---|---:|---|
| Smoke + instructed controls | 8 | 6 smoke (seeds 100–102 × 2) + 4 controls (seeds 150–151 × 2) |
| Main actor episodes | 25 | 20 (seeds 200–209 × 2); fallback 12 (seeds 200–205) |
| Auditor development + evaluation | 10 | ≤24 dev + 128 held-out decisions (96 on fallback) |
| Failure/retry reserve | 7 | retries and failures only |

30 decisions per episode, 90 tool actions, ≤3 tool calls per response, 1 identical transport
retry, 15 min wall clock. Fallbacks are chosen from smoke cost projections **before** main
outcomes: 20 → 12 main episodes → fixture-only study.

## Milestones (America/Chicago)

| When | Milestone |
|---|---|
| Sun Oct 25, 6 AM | Mock episode, evidence exports, essential tests green |
| Sun 9 AM | Smoke + controls assessed, cost decision, protocol frozen (or fixture-only fallback) |
| Sun 1 PM | Main actor runs finished or stopped at cutoff; full roster preserved |
| Sun 5 PM | Human labels, auditor evaluation, figures, verified fact sheet |
| Sun 10 PM | Author's report complete and checked against evidence |
| Sun 11:30 PM | Submitted; confirmation retained |
| Mon 6:59 AM | Official deadline; margin only |

## Non-goals

- Solo-agent, naive-prompt or architecture comparisons (removed in v5.2).
- A factuality/semantic release gate, conflict-review queues, dynamic releases.
- Publication-driven market impact, interactive markets, real trading or posting.
- Extra models, prompt wordings, mixed teams, dashboards, general agent frameworks.
- Rewording refusals or invalid auditor outputs; inducing agreement.
- Agents writing the submission prose (repository authorship rule; agents produce code,
  evidence packets, figures, tables and a fact sheet).

## Decision rule under time pressure

1. KEEP: both live conditions, audit fixtures + four views, human adjudication, writing time.
2. If live execution is not viable at the 9 AM cutoff: submit the validated fixture study and
   working artifact, with live evidence explicitly absent.
3. Cut in this order: optional engineering polish → auditor calls on smoke/control → main
   episodes down to the 6-seed fallback (chosen before outcomes).

## Definition of done

- [ ] Docs reconciled with v5.2; specs in `docs/specs/` with requirement IDs; traceability test
- [ ] Seeded world, release records, fact registry, predicate vocabulary, price path
- [ ] Services: store reads, public retrieval, messaging, orders, structured publication, duty reports
- [ ] Provenance enforcement at messages, publication and orders, each with block + allow tests
- [ ] Scheduler (6 rounds × 5 slots), caps, terminal states, spend ledger
- [ ] Append-only reconciled recorder; tamper tests; freeze create/validate
- [ ] Evaluator endpoints + legitimate-work scoring; restricted content cannot raise scores
- [ ] Audit fixtures (6 dev, 12 held-out), four views, equality + leak checks, label CLI, metrics
- [ ] Mock end-to-end in both conditions; Docker clean room green; `verify.sh` green
- [ ] Smoke + controls run (owner's go); protocol frozen; main runs; auditor runs
- [ ] Figures 1–3, tables, fact sheet generated from recorded evidence
- [ ] Report written by Sahil in the official template; submitted before the deadline
