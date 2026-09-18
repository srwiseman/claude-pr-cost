# pr-cost

A Claude Code plugin that keeps one PR comment up to date with the approximate token usage of the Claude Code sessions that worked on the PR. It is updated on every push:

> ### 🤖 Claude session cost: ~439.9k tokens
>
> | Session | Model | API calls | Input + cache write | Cache read | Output | Total tokens | API-equiv. $ |
> |---|---|--:|--:|--:|--:|--:|--:|
> | `bbbc5ac5` | `claude-opus-5` | 6 | 70.4k | 360.7k | 8.8k | 439.9k | $1.10 |

Tokens are the headline because they mean the same thing on an API plan and on a Pro/Max subscription. The dollar figure is the API list-price equivalent. On a subscription, the usage counts against plan limits and isn't charged per token.

## Install

In Claude Code:

```
/plugin marketplace add srwiseman/claude-pr-cost
/plugin install pr-cost@claude-pr-cost
```

Requirements: `python3`, and either `gh` authenticated (`gh auth login`) or `GH_TOKEN`/`GITHUB_TOKEN` set.

## How it works

- **Automatic:** a `PostToolUse` hook fires when the session:
  - opens a PR: `gh pr create`, a POST to `/repos/{owner}/{repo}/pulls` via `gh api` or `curl`, or any MCP tool named `*create_pull_request*`
  - pushes a branch that has an open PR (`git push`)
- **One comment per PR, edited in place.** Each session gets its own rows, and the header shows the running total. When you come back to a PR in a new session, that session is added and the earlier ones are kept. Per-session totals are stored in a hidden JSON block in the comment, so nothing is kept locally.
- **Manual:** `/pr-cost` in a session refreshes the comment on the branch's PR. You can also pass a PR number or URL, or use `--dry-run` to preview.
- The hook never blocks the tool call. If posting fails, the session shows a one-line notice.

The token counts come from the session transcript under `~/.claude/projects/`, plus any subagent transcripts. Each API response is logged several times, so it is counted once by message id. Cache writes (5-minute and 1-hour) and cache reads are priced separately.

## Caveats

- Each session is counted up to its most recent PR open or push. Work after the last push shows up on the next push or `/pr-cost`.
- Pushes that don't go to the branch checked out in the session's directory, such as `git push origin other-branch` or `cd elsewhere && git push`, update whichever PR belongs to the checked-out branch.
- Prices are hardcoded list prices (`PRICES` in `skills/pr-cost/pr-session-cost.py`), with no batch or priority discounts. Models the script doesn't recognise still count toward tokens, but not toward the $ total.
- Agent SDK and `claude -p` runs pick the hook up only if they load the plugin. It doesn't run in GitHub Actions unless Claude Code runs there with the plugin installed.

## License

MIT
