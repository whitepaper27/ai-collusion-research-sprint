# Ethwall: source-linked evidence for collusion detection in a synthetic finance team

A research harness for an AI-safety sprint (Track 2, collusion detection). A synthetic
five-role finance team (Market, Legal, HR, Trading, Comms) works on one fictional instrument
in two conditions with byte-identical prompts, evidence, tools, schedule and budget:

- **`baseline`**: role entitlements only.
- **`guarded`**: the same, plus **provenance enforcement**. A gate on messages, structured
  publication and orders blocks content derived from restricted documents.

It asks two questions (see [`GOAL.md`](GOAL.md)):

- **H1, audit:** does a *linked* (source-linked) evidence view change coordination-classification
  errors, false accusations, misses or abstentions compared with a *combined* chronological view
  holding the same information? An auditor model compares **four evidence views**
  (`messages`, `actions`, `combined`, `linked`) on scripted fixtures (6 development, 12 held-out)
  and on live trajectories.
- **H2, intervention:** does the provenance gate change restricted publication and
  legitimate-work completion compared with the matched `baseline` team?

Everything is fictional and local: the company, instrument, people, numbers and IDs. There is no
real trading, posting, exchange access or legal conclusion. Design: [`ARCHITECTURE.md`](ARCHITECTURE.md);
full plan: [`sol.md`](sol.md); work list: [`TASKS.md`](TASKS.md); module specs with requirement
IDs: [`docs/specs/`](docs/specs/).

## Repository map

| Path | Contents |
|---|---|
| `src/ethwall/` | The harness: world, services, enforcement (provenance gate), runtime/scheduler, recorder, spend ledger, scoring, evaluation, audit study, analysis, CLI (`ethwall`) |
| `fixtures/` | World templates and audit fixtures (`fixtures/audit/`) |
| `protocol/frozen/main-v1/` | Frozen protocol for the live main runs (`freeze.json`) |
| `protocol/frozen/auditor-v1/` | Frozen auditor prompt, model and settings |
| `protocol/frozen/adjudicator-v1/` | Frozen reference-adjudicator settings |
| `runs/` | Recorded episodes, one folder per partition. **Append-only evidence**: never edited or deleted |
| `audit/` | Auditor predictions (JSONL), including superseded attempts kept as evidence |
| `labels/` | Reference-adjudicator labels |
| `analysis/` | Tables, figures and fact sheets per partition; `analysis/audit-final/`; `analysis/report-figures/` |
| `docs/results/` | Results packet: numbers only, each traced to its source file |
| `docs/report/` | Report outline and related-work notes |
| `docs/specs/`, `docs/plans/`, `docs/decisions/` | Module specs, implementation plans, owner decisions |
| `scripts/` | The gate (`verify.sh`), Docker clean room, the owner's paid pipeline, agent hooks |
| `tests/` | Test suite, including spec traceability |

## Quickstart (offline, no model calls)

Requires Python 3.10+ and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
./scripts/verify.sh            # the full gate: format, lint, types, tests (--fast while iterating)

# One mock episode in both conditions (seed 200), written outside the repo
uv run --frozen ethwall run --mode mock --policy honest --partition mock-demo --seeds 200 --runs-root /tmp/x

# Check the recordings, then build tables, figures and a fact sheet for that partition
uv run --frozen ethwall verify --partition mock-demo --runs-root /tmp/x
uv run --frozen ethwall analyze --partition mock-demo --runs-root /tmp/x --out /tmp/x/analysis

# Regenerate the report figures from committed tables (writes analysis/report-figures/*.png)
uv run --frozen python analysis/make_report_figures.py
```

`uv run --frozen ethwall -h` lists every subcommand (`run`, `verify`, `analyze`, `freeze`,
`doctor`, `export-review`, `label`, `audit`); each has its own `-h`. Mock and fixture modes
never call a model. Every record is tagged with a `mode` (`mock | fixture | live`) and a
partition, and partitions are never pooled.

## Live runs (owner only)

Paid runs happen only on the owner's explicit go, through `scripts/paid-pipeline.sh`, run from
the owner's own shell with a local `.env` copied from [`.env.example`](.env.example) (API key,
exact model IDs, recorder key, pricing basis). **Never commit `.env`.** The script runs the
stages in gate order (doctor, probe, auditor dev/freeze/held-out, smoke and controls, cost
projection, freeze, main, audit, report; plus the `inc-*` stages for the incentive arm). The
spend ledger reserves before every call and stops at the caps: $50 total, $1.25 per main
episode, $0.80 per smoke/control episode, plus per-batch limits ([`GOAL.md`](GOAL.md)). Main
runs require a clean tree and a matching protocol freeze. Verifying committed live recordings
needs the owner's recorder key.

## Results

- [`docs/results/results-2026-10-26.md`](docs/results/results-2026-10-26.md): the results packet
  (live study, adjudicator validation, audit study, exploratory incentive arm, spend).
- [`analysis/audit-final/`](analysis/audit-final/): H1 audit metrics, fact sheet and Figure 1.
- [`analysis/report-figures/`](analysis/report-figures/): report figures 2 to 4.

Headline of the live study, verbatim from the results packet:

> Live study, frozen main-v1 (H2): inconclusive behavioral null

## Limits

- **References are AI-made or designed.** Fixture labels are construction labels written by the
  fixture author (designed, not validated ground truth); the secondary reference is an AI
  adjudicator. One sole human reviewer.
- **Small samples.** 12 held-out fixtures (6 pairs), 20 live main episodes; intervals are wide.
- **The live null is inconclusive.** No messages moved between roles in the main runs, so the
  gate never fired, and the instructed controls were not executed.
- **The incentive arm is exploratory**: post-hoc, partial (controls only; no smoke or main),
  stopped early, and recorded after the sprint deadline.
- **Synthetic, fictional world.** One instrument, a constrained structured publication
  interface, covert channels left open, no real trading or market impact.

---

The rest of this file documents the agent kit this repository's development workflow is built on.

## Agent Kit — a frontier-lab-style setup for shipping production software with coding agents

This kit doesn't build an agent platform. It sets up the repo so off-the-shelf agents
(Claude Code, Codex) can do most of the implementation safely and in parallel, while you
spend your time on specs, review and product. The practices come from what Anthropic and
OpenAI engineers have published about their own workflows:

1. **An off-the-shelf harness, barely customized.** Leverage comes from how work is organized
   around the agent.
2. **Parallel agents in git worktrees.** 3–5 local sessions, more in the cloud.
3. **Plan first.** Effort goes into the plan so the implementation lands in one shot.
4. **Agents verify their own work.** One command, enforced before an agent can say "done".
5. **Instructions that compound.** Every correction becomes one line in `AGENTS.md`.
6. **Independent review on every PR.** A human merges.

On top of that sits one idea from governed-agent systems, kept deliberately small:
**agents decide *how*; something they can't modify decides *whether*.** Here that's a guard
that CI runs from the base branch, plus GitHub branch rules. It isn't a platform.

---

## The loop

```
 issue (spec + acceptance criteria)
   │
   ▼
 ./scripts/wt.sh new <name>  ──►  claude  ──►  /task <issue#>
                                                  │
     ┌────────────────────────────────────────────┘
     ▼
 plan ─► failing tests ─► implement ─► verify.sh ─► reviewer subagent ─► PR
  │                                     ▲   │
  └─ high risk: STOP for approval       │   └─ Stop hook: can't finish while verify fails
                                        │
 hooks block: edits to protected files, check suppression, force-push, merges
     │
     ▼
 CI  verify (untrusted: PR code, no secrets)       ─┐
     guard  (trusted: base-branch script + rules,   ├─ both required ─►  YOU merge
             PR read only as a diff)               ─┘   AI review: advisory
     │
     ▼
 correction? ─► /learn ─► one line in AGENTS.md ─► every future agent knows
```

## What's in the box

| File | Job |
|---|---|
| `AGENTS.md` | The one instructions file, read by every agent (Claude Code via `@AGENTS.md`, Codex natively). Commands, rules, conventions, Lessons. |
| `CLAUDE.md` | Imports AGENTS.md and adds Claude Code specifics: plan mode, subagents, skills. |
| `scripts/verify.sh` | Does the code work: format, lint, types, tests, build (plus a local guard run). Auto-detects JS/TS and Python. Prints only failure tails so agent context stays clean. |
| `scripts/agent_guard.py` | Deterministic rules: protection tiers, gate-script and test-config tampering, check suppression, assertion loss in edited tests, dangerous shell commands. Local hooks give fast feedback; the trusted CI copy enforces. |
| `.agent/protected.txt` | Risk tiers. **Locked:** CI, the gate, hook and test-runner config. **Review:** existing tests, lint/type config, agent instructions — editable, a human must approve. **Immutable:** applied migrations. Files a branch creates are always the agent's to edit. |
| `.github/workflows/guard.yml` | **The trusted check.** Runs on `pull_request_target`, so the workflow, script and rules all come from the base branch. Reads the PR as a diff, never executes it. Approval labels only count from `TRUSTED_LABELERS`, and any new push strips them. |
| `scripts/stop-verify.sh` | Stop hook. Runs `verify.sh --fast` when the agent tries to finish; if it fails, the agent keeps working. The last retry demands an explicit `BLOCKED:` report instead of "done". |
| `scripts/format-file.sh` | Post-edit hook. Formats each file the agent touches (Biome, Prettier or Ruff). |
| `scripts/wt.sh` | One worktree per agent: `new`, `ls` (with PR state), `rm`, `clean` (removes merged ones). Copies `.env` files and installs deps. |
| `scripts/scorecard.py` | Agent vs. human PRs: merge rate, hours to merge, size, reverts. |
| `scripts/setup-github.sh` | One-time: labels, `TRUSTED_LABELERS`, and a ruleset (PR-only, required `verify` + `guard`, no force push, no bypass list). |
| `.claude/settings.json` | Permissions (allow routine commands, ask before push/install, deny secrets) and hook wiring. |
| `.claude/skills/` | `/task` (full loop), `/plan`, `/ship`, `/fix-ci`, `/learn`. |
| `.claude/agents/reviewer.md` | Fresh-context, read-only reviewer. Checks each acceptance criterion against evidence. |
| `.github/workflows/ci.yml` | Runs `verify.sh` on the PR's code with no secrets and no persisted token. Required, but treated as untrusted. |
| `.github/workflows/claude-review.yml` | AI review on every PR. Advisory, never required. |
| Templates | Issue (agent-ready spec), PR, plan, CODEOWNERS. |
| `kit-tests/redteam.sh` | Adversarial tests for the kit itself (32 scenarios). Run after changing any rule. Not installed into your repo. |

## Install

```bash
unzip agent-kit.zip && ./agent-kit/install.sh /path/to/your/repo
cd /path/to/your/repo
```

1. **Fill in `AGENTS.md`.** Project, stack, real commands, real conventions. This is the
   highest-leverage 20 minutes in the setup. Delete any placeholder you can't fill.
2. **Replace `@OWNER`** in `.github/CODEOWNERS`.
3. **Make `./scripts/verify.sh` pass locally.** It runs whichever of `format:check`, `lint`,
   `typecheck`, `test`, `build`, `test:e2e` exist in `package.json` (or Ruff, Pyright/mypy and
   pytest for Python). Add any missing scripts.
4. **Commit on a branch, open a PR, merge when `verify` is green.** `guard` won't run on this
   first PR: it runs from the base branch, where it doesn't exist until you merge.
5. **Run `./scripts/setup-github.sh`** (needs repo admin): labels, `TRUSTED_LABELERS` (you),
   and branch rules requiring `verify` + `guard`.
6. **Give agents their own GitHub identity** (recommended). Use a bot/machine account or a
   GitHub App with Write access only, then run `./scripts/setup-github.sh --reviews 1`. Until you
   do, agents act as you: checks still hold, but GitHub can't tell your approval from theirs, so
   approvals are off and approval labels are only as strong as the hook that blocks agents from
   applying them.
7. **Optional:** add an `ANTHROPIC_API_KEY` repo secret for `claude-review.yml`, or delete that
   file and use the managed Code Review GitHub App instead.

Requirements: git, Python 3.10+, `gh` (for `wt ls`, the scorecard and the skills), and your
stack's toolchain.

## Daily workflow

**Morning (15 min): write specs, not code.** Turn the day's work into issues using the
*Agent task* template, each with acceptance criteria checkable by a test, and label them
`agent-ready`. Anything high risk (data model, auth, billing, public API) gets `/plan` first,
and you approve the plan.

**Run agents in parallel.**
```bash
./scripts/wt.sh new signup-rate-limit && cd "$(./scripts/wt.sh path signup-rate-limit)" && claude
> /task 142
```
Do that 3–5 times in separate terminal tabs with notifications on. Small, well-specified
issues can go to cloud or background sessions. Your job is now answering questions, approving
plans and reviewing PRs.

**Review as PRs land.** You're the merge gate. Keep PRs small; ask for a split rather than
reviewing 1,000 lines. If `guard` asks for `human-reviewed`, read its report first: it lists
which existing tests lost assertions. When you correct an agent, finish with `/learn`.

**End of day:** `./scripts/wt.sh clean`.

**Weekly (30 min):**
- `python3 scripts/scorecard.py`. Read the transcripts of any reverted agent PR; each one should
  produce a Lesson.
- Prune `AGENTS.md`: delete lines that are no longer true or no longer needed. Stay under 200.
- **After every model upgrade, delete scaffolding.** Remove rules and skill steps that only
  compensated for the old model's weaknesses. Try the task with less instruction and see if it
  still works.

## How enforcement works, honestly

| Layer | What it is | Trust |
|---|---|---|
| `AGENTS.md` rules | Guidance | None. Agents usually follow it; nothing makes them. |
| Hooks + permissions | Fast local feedback: blocks or prompts on protected files, suppressions, force-push, merges, approval labels, `.env` reads | Low. It's code in the agent's own checkout, and the shell can route around it. |
| `verify` in CI | Runs the PR's code: does it work? | Untrusted by design: no secrets, read-only token. It can't vouch for its own oracle. |
| `guard` in CI | Base-branch script and rules judging the PR's diff | **Trusted.** A PR can't change what judges it. |
| Branch ruleset | PR-only, `verify` + `guard` required, no force push, no bypass | **Trusted**, as long as agents don't have admin. |
| You | Merge, and approval labels for sensitive changes | Only distinguishable from agents if agents use their own identity. |

`verify` answers "does the code work?" and `guard` answers "did anyone tamper with how we
check?" Together: the tests that passed are the tests a human agreed to.

**Not covered, so know the limits:**
- **Semantic weakening.** A new test that can't fail, or an assertion swapped for a looser one
  (still counted as an assertion). The reviewer subagent and you catch these; the guard reports
  assertion loss but doesn't understand meaning. For critical modules, add mutation testing
  (Stryker, mutmut) to `verify.sh`.
- **Execution isolation.** Worktrees separate files, not processes, ports, databases or
  credentials. That's fine for agents you're supervising on your laptop. For unattended agents,
  run each in a sandbox (a container or cloud session) with no production credentials.
- **Coverage drift.** Not measured. Add a coverage threshold to your test script if it matters.
  The threshold then lives in locked or review-tier config.
- **Merge races.** With many concurrent PRs, add GitHub's merge queue (CI must also trigger on
  `merge_group`).

## Customizing

- **Stack:** `verify.sh` auto-detects. If your commands differ, edit its `STEPS`. It's
  locked, so make that change as a human, in a PR with the `allow-protected` label.
- **Tiers:** edit `.agent/protected.txt` (locked). Put security and authorization tests in
  `locked`. The rules that apply to a PR are always the base branch's, so loosening them takes
  effect only after a human merges that change.
- **After changing any rule,** run `./kit-tests/redteam.sh` from the kit to confirm nothing
  regressed.
- **Codex and other agents:** they read `AGENTS.md` natively. Hooks are Claude Code specific, so
  for other tools CI is the whole boundary, which is fine; that's where the boundary belongs.
- **Python:** Ruff, Pyright or mypy, and pytest (via `uv` if present). Mark slow tests
  `@pytest.mark.e2e` to keep `--fast` fast.
- **Your linters and the kit's scripts:** the kit's Python passes Ruff (format and strict
  rule sets) and Pyright cleanly. If your config is stricter still, exclude `scripts/` rather
  than editing the gate.

## Deliberately not included (and when to add it)

| Not here | Add it when |
|---|---|
| Orchestration framework (LangGraph, Temporal) | You run unattended multi-hour pipelines that must survive crashes. Not for dev work. |
| Vector or episodic memory DB | `AGENTS.md` passes ~200 lines of genuinely necessary rules. Unlikely. |
| Policy engine (OPA), MCP gateway | Agents act on production systems or customer data, or you need audit for compliance. |
| LLM judge as a required check | Never. LLM review can veto, never approve. |
| Custom multi-agent "swarm" | Measured: a fixed task set shows it beats parallel independent sessions. |

The test for any addition: does it raise merged-and-not-reverted PRs per week on your own
scorecard? If you can't show that, it's overhead.
