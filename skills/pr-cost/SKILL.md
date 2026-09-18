---
name: pr-cost
description: Post this session's approximate token usage (and API-equivalent $) as a comment on a GitHub PR. Use when the user says "pr cost", "add session cost to the PR", or runs /pr-cost. The plugin's hook already does this automatically when a PR is opened; this skill is for refreshing the number later or for PRs opened outside the session.
---

The script `pr-session-cost.py` is in this skill's base directory. Run it with `python3` from the repo's working directory. It resolves the current branch's PR with `gh pr view`:

```bash
python3 <skill-dir>/pr-session-cost.py            # current branch's PR
python3 <skill-dir>/pr-session-cost.py 42         # a PR number or URL
python3 <skill-dir>/pr-session-cost.py --dry-run  # print the comment only
```

It reads the newest transcript for the current directory, including subagent transcripts, and dedupes by API message id. Each run posts a new comment, so an earlier one isn't edited. Report the headline token count and the API-equivalent $ to the user. On a subscription plan the $ figure is notional.
