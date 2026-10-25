# GOAL

The file every other decision defers to. The full research plan is [`sol.md`](sol.md)
(v4.1, 2026-10-24); this page is its operating summary. If they disagree, `sol.md` wins and
this file gets fixed.

**Owner:** Sahil Soni · **Last updated:** 2026-10-24

## Open owner decision

- [ ] `sol.md` v4.1 says "No code changes are authorized by this update." Until the owner
      records here that building the experiment harness is authorized, agents may only work
      on setup, docs, fixtures design and plans, not harness code.

## Mandatory agent instruction

Read this file before research, design, implementation or delegation. Every task must name
the core cell, definition-of-done item or decision-rule line it advances. If it advances
none, don't do it.

---

## The goal (one sentence)

By **Monday Oct 26, 2026, 6:59 AM America/Chicago** (target: midnight Sunday→Monday), submit
a reproducible ≤8-page report that measures whether separating client duties across
permission-scoped agents changes cross-client disclosure compared with one combined-role
agent, and what a selective provenance-enforced gate preserves, blocks and costs, in the
fictional Northgate Tax Advisory ethical-wall sandbox.

## What a credible result requires (priority order)

1. **The two primary contrasts are measured** on matched synthetic-world seeds: team vs
   combined-role under entitlements only (cell 2 vs 1), and selective gate vs entitlements
   only for the team (cell 4 vs 2). Counts, denominators, intervals.
2. **The main figure exists** (fixed in `sol.md` §1): single vs team × entitlements-only vs
   selective gate; Client A content in the committed Client B memo next to legitimate
   Client B quality; internal exposure and gate blocks shown separately.
3. **Controls work:** the known-answer evidence fixture is detected, the instructed agent
   control is run, and legitimate Client B work completes. Otherwise a null is inconclusive.
4. **Limits are stated honestly:** sample size, prompt dependence, covert channels not closed,
   trusted components, synthetic scope. Overclaiming is a credibility loss.
5. **Not a reskin:** positioned against Fides, FLOWSEAL, SafeFlow, Colosseum and Multi-Agent
   AI Control (`sol.md` §14).

## The claim (do not exceed it)

> We compare a combined-role agent with separately permissioned client agents in a
> controlled ethical-wall workflow. We measure cross-client information flow, evidence of
> coordination, and the disclosure–utility tradeoff of a specified provenance-enforcement
> design.

No expected results, no direction for H1/H2, no prize prediction. Results are stated only
after measurement. Leakage alone is not collusion.

## Non-goals

- Trading, price impact, publication-driven profit, systemic-risk or legal-offense claims.
- Optional arms (conservative gate C, prompt reminder, policy omission, open model, mixed
  teams, auditor views, second paraphrase) before the core is secure.
- Claiming full noninterference or closure of covert channels.
- Rewording prompts after a refusal to obtain compliance.
- Agents writing the submission prose. The report must be the author's own writing.

## Decision rule under time pressure

Keep from the top, cut from the bottom:

1. KEEP: core cells 1–4 (40 episodes, high pressure, one exact model), main figure, claim
   labels/adjudication, report with Limitations and Dual-Use appendix.
2. KEEP: 12 smoke episodes (3 per cell) and the dated pilot record before the main run.
3. CUT FIRST: conservative gate C reference (10 team episodes).
4. CUT NEXT: second prompt paraphrase, then auditor-view study (H4), then every other extension.
5. If cells must shrink, keep the two primary contrasts and disclose missing cells.

Sunday noon is the internal scope cutoff, not a promise that data will be ready.

## Definition of done

- [ ] Synthetic world built: stores, labels, canary registry; canaries verified absent from
      KB, Client B records, prompts and reference memo
- [ ] Honest reference Client B memo and frozen quality rubric (no Client A content needed)
- [ ] Architectures (combined-role, team) and gates (E, S) implemented with tested access
      boundaries; recorder retains every message, read, observation and deliverable
- [ ] Known-answer fixture and instructed control pass
- [ ] Smoke episodes run, pilot record dated and kept
- [ ] Protocol frozen in a git commit (hypotheses, prompts, labels, gate semantics, seeds,
      budget, analyses) before the main run; hash recorded in run manifests
- [ ] 40 core episodes run on fresh seeds; refusals, API failures, incomplete runs kept separate
- [ ] Main figure + tables generated from recorded runs by script
- [ ] Fact sheet of verified numbers for the author; report written by the author in the
      official template (≤8 pages, 150–250-word abstract, Limitations + Dual-Use appendix)
- [ ] Submitted before the deadline

## Where the detail lives

| Need | File |
|---|---|
| Full research plan, measurement rules, claim boundaries | [`sol.md`](sol.md) |
| How to work in this repo | [`AGENTS.md`](AGENTS.md) |
| Plans for non-trivial changes | [`docs/plans/`](docs/plans/) |
| Prior L3 sprint (Agent Worlds harness) | [`../harness/`](../harness/) — record provenance if anything is reused |
