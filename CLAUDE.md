@AGENTS.md
@GOAL.md

The full research plan is `sol.md` (~29 KB, not auto-loaded). Read the sections relevant to
your task before designing or changing anything in the experiment: §2 world, §3 architectures,
§5–6 bus and gates, §7 outcomes, §8 sample plan, §11–12 freeze and evidence integrity.

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
- **Deadline mode.** The sprint ends Mon Oct 26, 6:59 AM CT. Before starting anything, check
  it against GOAL.md's decision rule. If a task belongs to a cut tier, say so and stop.
- **Never spend model credits without being asked.** Live-model episodes (`mode: live`) cost
  money and become evidence. Build and test with `mock`/`fixture` mode; the human starts live runs.

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
