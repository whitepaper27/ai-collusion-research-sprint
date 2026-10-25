#!/usr/bin/env python3
"""scorecard.py — is the agent setup actually working? Numbers, not vibes.

    python3 scripts/scorecard.py            # last 14 days
    python3 scripts/scorecard.py --days 30

Reads merged and closed PRs via the GitHub CLI (`gh`). Agent PRs are ones whose branch starts
with `agent/` (what scripts/wt.sh creates) or that carry the `agent` label.

What to watch:
  merge rate        agent PRs merged / agent PRs closed. Low = bad specs or bad verification.
  hours to merge    median open→merge. High = PRs too big, or review is your bottleneck.
  reverts           merged agent PRs later reverted. This is your false-acceptance signal.
  lines per PR      median. Creeping up = PRs getting harder to review.
"""

import argparse
import json
import re
import statistics
import subprocess
import sys
from datetime import datetime, timedelta, timezone


def gh(*args):
    try:
        out = subprocess.run(
            ["gh", *args], check=True, capture_output=True, text=True
        ).stdout
    except FileNotFoundError:
        sys.exit(
            "scorecard: GitHub CLI `gh` not found. Install it and run `gh auth login`."
        )
    except subprocess.CalledProcessError as e:
        sys.exit(f"scorecard: gh failed: {e.stderr.strip()}")
    return json.loads(out or "[]")


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def is_agent(pr):
    return pr["headRefName"].startswith("agent/") or any(
        l["name"] == "agent" for l in pr.get("labels", [])
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    a = ap.parse_args()
    since = datetime.now(timezone.utc) - timedelta(days=a.days)
    fields = "number,title,headRefName,labels,createdAt,mergedAt,closedAt,additions,deletions,state"

    prs = gh(
        "pr",
        "list",
        "--state",
        "all",
        "--limit",
        "500",
        "--json",
        fields,
        "--search",
        f"created:>={since.date().isoformat()}",
    )
    closed = [p for p in prs if p["state"] in ("MERGED", "CLOSED")]
    agent = [p for p in closed if is_agent(p)]
    human = [p for p in closed if not is_agent(p)]

    # Reverts: merged PRs titled 'Revert "<title>"' — GitHub's default revert title.
    revert_titles = {
        m.group(1)
        for p in prs
        if p["state"] == "MERGED"
        for m in [re.match(r'^Revert "(.*)"$', p["title"])]
        if m
    }

    def stats(group):
        merged = [p for p in group if p["state"] == "MERGED"]
        hours = [
            (parse(p["mergedAt"]) - parse(p["createdAt"])).total_seconds() / 3600
            for p in merged
        ]
        size = [p["additions"] + p["deletions"] for p in merged]
        reverted = [p for p in merged if p["title"] in revert_titles]
        return {
            "closed": len(group),
            "merged": len(merged),
            "merge_rate": f"{len(merged) / len(group):.0%}" if group else "-",
            "median_hours_to_merge": f"{statistics.median(hours):.1f}"
            if hours
            else "-",
            "median_lines_changed": int(statistics.median(size)) if size else "-",
            "reverted": len(reverted),
        }

    a_s, h_s = stats(agent), stats(human)
    print(f"Scorecard — last {a.days} days\n")
    print(f"{'':24}{'agent':>10}{'human':>10}")
    for k in a_s:
        print(f"{k.replace('_', ' '):24}{a_s[k]!s:>10}{h_s[k]!s:>10}")
    if agent:
        merged = [p for p in agent if p["state"] == "MERGED"]
        bad = [p for p in merged if p["title"] in revert_titles]
        if bad:
            print(
                "\nReverted agent PRs (read these transcripts, then add a Lesson to AGENTS.md):"
            )
            for p in bad:
                print(f"  #{p['number']} {p['title']}")


if __name__ == "__main__":
    main()
