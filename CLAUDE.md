@AGENTS.md
@GOAL.md

The full research plan is `sol.md` v5.2 (not auto-loaded). Read the sections relevant to your
task before designing or changing anything: §2 roles and entitlements, §3 sources and release
records, §4 conditions and partitions, §5 schedule and exposure, §6 evidence and scoring, §7
provenance enforcement, §8 trading environment, §9 caps, spend and terminal states, §10 auditor
study and denominators, §11 acceptance checks and milestones. System design is in
`ARCHITECTURE.md`, the work list in `TASKS.md`, module requirements in `docs/specs/`.

## Claude Code specifics

- **Plan mode first** for anything non-trivial. A good plan is what makes the implementation
  work in one shot; spend the effort there.
- **Use subagents to keep context clean.** Send broad codebase searches to an Explore subagent
  and keep only its conclusion. Before shipping, run the `reviewer` subagent on your diff: it
  has fresh context and hasn't seen your reasoning, which is the point.
- **Hooks are enforcing the rules.** If a hook blocks you, read the message and change approach.
  Don't retry the same action or route around it through the shell; CI checks the same rules.
- **The Stop hook runs `verify.sh --fast`** when you finish. If it fails, you'll be asked to
  keep going. If you're genuinely stuck after a few attempts, say so plainly.
- **One task per session.** When a task is done, the human will `/clear` or start a new
  worktree. Don't carry unrelated work into the same branch.
- **Deadline mode.** Milestones are in GOAL.md (first: Sun 6 AM working mock episode). Before
  starting anything, check it against GOAL.md's decision rule. If it's cut-tier, say so and stop.
- **Never spend model credits without the owner's explicit go.** Live episodes and auditor calls
  cost money against a $50 ceiling and become evidence. Build and test in `mock`/`fixture` mode.
- **Spec-driven loop.** Read the module spec, write failing tests citing its `R-XXX-NN` IDs,
  implement, run `./scripts/verify.sh`, fix the cause on red (never weaken a test). After 6 red
  iterations stop with a BLOCKED report.

## Skills in this repo

| Skill | Use |
|---|---|
| `/task <issue# or description>` | Full loop: plan → failing tests → implement → verify → review → PR |
| `/plan <issue# or description>` | Write `docs/plans/<slug>.md` and stop for approval |
| `/ship` | Verify, self-review with the reviewer subagent, open the PR |
| `/fix-ci [pr#]` | Reproduce failing CI checks locally and fix them |
| `/learn` | Turn the correction you just got into one line in AGENTS.md → Lessons |

Issue numbers and PRs need a GitHub remote and `gh auth login`. Until then, pass a short
description instead of an issue number.
