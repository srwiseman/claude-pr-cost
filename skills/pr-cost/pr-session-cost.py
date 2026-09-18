#!/usr/bin/env python3
"""Post this Claude Code session's approximate token usage as a comment on a PR.

Hook mode (PostToolUse, JSON on stdin): fires after a tool call that opened a PR
  - Bash: `gh pr create`, or a POST to .../pulls via `gh api` / `curl`
  - MCP:  any tool whose name contains create_pull_request
Manual mode: pr-session-cost.py [PR_URL_OR_NUMBER] [--dry-run]
  Uses the newest transcript for the current directory and the current branch's PR.

Tokens are the headline because they mean the same thing on an API plan and on a
Pro/Max subscription. The dollar figure is labelled as API-equivalent list price.
"""
import glob
import json
import os
import re
import subprocess
import sys

MARKER = "<!-- claude-session-cost -->"
STATE_DIR = os.path.expanduser("~/.claude/state/pr-session-cost")
PR_URL_RE = re.compile(r"https://github\.com/([^/\s\"']+)/([^/\s\"']+)/pull/(\d+)")

# $ per million tokens: (input, output). Cache pricing is derived from input:
# 5m write 1.25x, 1h write 2x, read 0.1x - except where CACHE_READ overrides.
PRICES = [
    ("claude-fable-5", (10.0, 50.0)),
    ("claude-mythos-5", (10.0, 50.0)),
    ("claude-opus-5", (5.0, 25.0)),
    ("claude-opus-4", (5.0, 25.0)),
    ("claude-sonnet-5", (2.0, 10.0)),
    ("claude-sonnet-4", (3.0, 15.0)),
    ("claude-haiku-4", (1.0, 5.0)),
]
CACHE_READ = {"claude-fable-5-1": 0.25}


def price_for(model):
    for prefix, p in PRICES:
        if model.startswith(prefix):
            return p
    return None


# ---------------------------------------------------------------- detection

def pr_opened(event):
    """Return the PR URL if this tool call opened a PR, else None."""
    tool = event.get("tool_name", "")
    resp = json.dumps(event.get("tool_response", ""))
    m = PR_URL_RE.search(resp)
    if not m:
        return None
    if tool == "Bash":
        cmd = event.get("tool_input", {}).get("command", "")
        if re.search(r"\bgh\s+pr\s+create\b", cmd):
            return m.group(0)
        posts_to_pulls = re.search(r"/pulls\b(?!/)", cmd) and re.search(
            r"(-X\s*POST|--method\s+POST|--request\s+POST|\s-[fFd]\s|--field|--raw-field|--input|--data)",
            cmd,
        )
        return m.group(0) if posts_to_pulls else None
    if tool.startswith("mcp__") and "create_pull_request" in tool:
        return m.group(0)
    return None


# ---------------------------------------------------------------- usage

def transcripts_for(main):
    files = [main]
    files += glob.glob(os.path.join(main[: -len(".jsonl")], "subagents", "*.jsonl"))
    return files


def tally(files):
    seen = set()
    by_model = {}
    for path in files:
        try:
            fh = open(path, encoding="utf-8")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"usage"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                msg = rec.get("message") or {}
                usage = msg.get("usage")
                if rec.get("type") != "assistant" or not usage:
                    continue
                # One API response is logged once per content block, each with the same usage.
                key = msg.get("id") or rec.get("requestId") or rec.get("uuid")
                if key in seen:
                    continue
                seen.add(key)
                model = msg.get("model") or "unknown"
                if model == "<synthetic>":
                    continue
                t = by_model.setdefault(model, dict(inp=0, out=0, w5=0, w1h=0, read=0, calls=0))
                cc = usage.get("cache_creation") or {}
                total_write = usage.get("cache_creation_input_tokens", 0) or 0
                w1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
                t["inp"] += usage.get("input_tokens", 0) or 0
                t["out"] += usage.get("output_tokens", 0) or 0
                t["w1h"] += w1h
                t["w5"] += max(total_write - w1h, 0)
                t["read"] += usage.get("cache_read_input_tokens", 0) or 0
                t["calls"] += 1
    return by_model


def cost(model, t):
    p = price_for(model)
    if not p:
        return None
    inp, out = p
    read = next((v for k, v in CACHE_READ.items() if model.startswith(k)), inp * 0.1)
    return (t["inp"] * inp + t["w5"] * inp * 1.25 + t["w1h"] * inp * 2
            + t["read"] * read + t["out"] * out) / 1e6


def fmt(n):
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if n >= div:
            return f"{n / div:.1f}{suf}"
    return str(n)


def render(by_model, session_id):
    rows, total_tokens, total_cost, unpriced = [], 0, 0.0, False
    for model, t in sorted(by_model.items(), key=lambda kv: -sum(v for k, v in kv[1].items() if k != "calls")):
        tokens = t["inp"] + t["w5"] + t["w1h"] + t["read"] + t["out"]
        c = cost(model, t)
        total_tokens += tokens
        if c is None:
            unpriced = True
        else:
            total_cost += c
        rows.append(
            f"| `{model}` | {t['calls']} | {fmt(t['inp'] + t['w5'] + t['w1h'])} | {fmt(t['read'])} "
            f"| {fmt(t['out'])} | {fmt(tokens)} | {'—' if c is None else f'${c:,.2f}'} |"
        )
    note = " (excludes models with unknown pricing)" if unpriced else ""
    return "\n".join([
        MARKER,
        f"### 🤖 Claude session cost: ~{fmt(total_tokens)} tokens",
        "",
        "| Model | API calls | Input + cache write | Cache read | Output | Total tokens | API-equiv. $ |",
        "|---|--:|--:|--:|--:|--:|--:|",
        *rows,
        "",
        f"**API-equivalent cost:** ~${total_cost:,.2f}{note}. On a Pro/Max subscription this is "
        "usage against plan limits, not a charge. Counted up to when the PR was opened; "
        "includes subagents; list prices, so no batch/priority discounts.",
        "",
        f"<sub>session `{session_id}`</sub>",
    ])


# ---------------------------------------------------------------- posting

def post_comment(pr_url, body):
    try:
        r = subprocess.run(["gh", "pr", "comment", pr_url, "--body-file", "-"],
                           input=body, text=True, capture_output=True, timeout=30)
        if r.returncode == 0:
            return True, r.stdout.strip()
        err = r.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        err = str(e)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        return False, err
    owner, repo, num = PR_URL_RE.search(pr_url).groups()
    r = subprocess.run(
        ["curl", "-sS", "-f", "-X", "POST",
         "-H", f"Authorization: Bearer {token}", "-H", "Accept: application/vnd.github+json",
         f"https://api.github.com/repos/{owner}/{repo}/issues/{num}/comments",
         "--data-binary", "@-"],
        input=json.dumps({"body": body}), text=True, capture_output=True, timeout=30)
    return r.returncode == 0, (r.stderr.strip() or "posted via REST API")


# ---------------------------------------------------------------- entry points

def newest_transcript(cwd):
    proj = os.path.expanduser("~/.claude/projects/") + re.sub(r"[^A-Za-z0-9]", "-", cwd)
    files = glob.glob(os.path.join(proj, "*.jsonl"))
    return max(files, key=os.path.getmtime) if files else None


def main():
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry = "--dry-run" in sys.argv

    if sys.stdin.isatty() or args or dry:
        # Manual mode
        transcript = newest_transcript(os.getcwd())
        if not transcript:
            sys.exit("No transcript found for this directory.")
        target = args[0] if args else ""
        r = subprocess.run(["gh", "pr", "view", *([target] if target else []), "--json", "url", "-q", ".url"],
                           capture_output=True, text=True)
        pr_url = r.stdout.strip()
        if not pr_url and not dry:
            sys.exit(f"Could not resolve PR: {r.stderr.strip()}")
        body = render(tally(transcripts_for(transcript)), os.path.basename(transcript)[:-6])
        if dry:
            print(body)
            return
        ok, msg = post_comment(pr_url, body)
        print(msg if ok else f"Failed: {msg}")
        sys.exit(0 if ok else 1)

    # Hook mode: never fail the tool call
    try:
        event = json.load(sys.stdin)
        pr_url = pr_opened(event)
        transcript = event.get("transcript_path")
        if not pr_url or not transcript:
            return
        session = event.get("session_id", "unknown")
        os.makedirs(STATE_DIR, exist_ok=True)
        stamp = os.path.join(STATE_DIR, re.sub(r"\W", "_", f"{session}_{pr_url}"))
        if os.path.exists(stamp):
            return
        body = render(tally(transcripts_for(transcript)), session)
        ok, msg = post_comment(pr_url, body)
        if ok:
            open(stamp, "w").close()
        print(json.dumps({"systemMessage": f"PR session cost {'posted to' if ok else 'failed for'} {pr_url}"
                          + ("" if ok else f": {msg}")}))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"systemMessage": f"pr-session-cost hook error: {e}"}))


if __name__ == "__main__":
    main()
