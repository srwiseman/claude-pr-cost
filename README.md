# pr-cost

A Claude Code plugin that posts a PR comment with the approximate token usage of the session that opened it:

> ### 🤖 Claude session cost: ~439.9k tokens
>
> | Model | API calls | Input + cache write | Cache read | Output | Total tokens | API-equiv. $ |
> |---|--:|--:|--:|--:|--:|--:|
> | `claude-opus-5` | 6 | 70.4k | 360.7k | 8.8k | 439.9k | $1.10 |

Tokens are the headline because they mean the same thing on an API plan and on a Pro/Max subscription. The dollar figure is the API list-price equivalent. On a subscription, the usage counts against plan limits and isn't charged per token.

## Install

In Claude Code:

```
/plugin marketplace add srwiseman/claude-pr-cost
/plugin install pr-cost@claude-pr-cost
```

Requirements: `python3`, and either `gh` authenticated (`gh auth login`) or `GH_TOKEN`/`GITHUB_TOKEN` set.

## How it works

- **Automatic:** a `PostToolUse` hook fires when the session opens a PR. That covers `gh pr create`, a POST to `/repos/{owner}/{repo}/pulls` via `gh api` or `curl`, and any MCP tool named `*create_pull_request*`. It posts once per PR per session and never blocks the tool call.
- **Manual:** `/pr-cost` in a session posts the current numbers on the branch's PR. You can also pass a PR number or URL, or use `--dry-run` to preview.

The token counts come from the session transcript under `~/.claude/projects/`, plus any subagent transcripts. Each API response is logged several times, so it is counted once by message id. Cache writes (5-minute and 1-hour) and cache reads are priced separately.

## Caveats

- Usage is counted up to the moment the PR is opened.
- Prices are hardcoded list prices (`PRICES` in `skills/pr-cost/pr-session-cost.py`), with no batch or priority discounts. Models the script doesn't recognise still count toward tokens, but not toward the $ total.
- Agent SDK and `claude -p` runs pick the hook up only if they load the plugin. It doesn't run in GitHub Actions unless Claude Code runs there with the plugin installed.

## License

MIT
