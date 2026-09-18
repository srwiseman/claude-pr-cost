---
name: pr-cost
description: Update the PR comment that shows this session's approximate token usage (and API-equivalent $). Use when the user says "pr cost", "add session cost to the PR", "update the cost comment", or runs /pr-cost. The plugin's hook already does this automatically when a PR is opened and on every `git push` to a branch with an open PR; this skill is for refreshing it by hand or for PRs opened outside the session.
---

The script `pr-session-cost.py` is in this skill's base directory. Run it with `python3` from the repo's working directory. It resolves the current branch's PR with `gh pr view`:

```bash
python3 <skill-dir>/pr-session-cost.py            # current branch's PR
python3 <skill-dir>/pr-session-cost.py 42         # a PR number or URL
python3 <skill-dir>/pr-session-cost.py --dry-run  # print the comment without posting
```

Each PR has one cost comment, which the script edits in place and keeps one row per session in. It reads the newest transcript for the current directory, including subagent transcripts. Report the headline token count and the API-equivalent $ to the user. On a subscription plan the $ figure is notional.
