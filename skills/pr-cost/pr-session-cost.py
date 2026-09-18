#!/usr/bin/env python3
"""Keep a PR comment showing the approximate token usage of the Claude Code sessions behind it.

Hook mode (PostToolUse, JSON on stdin) fires when a tool call:
  - opens a PR: `gh pr create`, a POST to .../pulls via `gh api` / `curl`,
    or an MCP tool whose name contains create_pull_request
  - pushes a branch that has an open PR: `git push`
Manual mode: pr-session-cost.py [PR_URL_OR_NUMBER] [--dry-run]
  Uses the newest transcript for the current directory and the current branch's PR.

There is one comment per PR, edited in place. It keeps one row per session, so work from
several sessions adds up. Per-session totals are stored in a hidden JSON block in the comment.

Tokens are the headline because they mean the same thing on an API plan and on a
Pro/Max subscription. The dollar figure is labelled as API-equivalent list price.
"""
import datetime
import glob
import json
import os
import re
import subprocess
import sys

MARKER = "<!-- claude-session-cost -->"
DATA_RE = re.compile(r"<!-- claude-session-cost-data (.*?) -->", re.S)
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


def pushed(event):
    """True if this tool call was a successful `git push`."""
    if event.get("tool_name") != "Bash":
        return False
    cmd = event.get("tool_input", {}).get("command", "")
    if not re.search(r"\bgit\s+(-C\s+\S+\s+)?push\b", cmd) or re.search(r"--dry-run|\s-n\b", cmd):
        return False
    resp = json.dumps(event.get("tool_response", ""))
    return not re.search(r"\[rejected\]|fatal:|error: failed to push", resp)


def open_pr_for_branch(cwd):
    r = subprocess.run(["gh", "pr", "view", "--json", "url,state"], cwd=cwd,
                       capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        return None
    info = json.loads(r.stdout)
    return info["url"] if info.get("state") == "OPEN" else None


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


def total_tokens(t):
    return t["inp"] + t["w5"] + t["w1h"] + t["read"] + t["out"]


def fmt(n):
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if n >= div:
            return f"{n / div:.1f}{suf}"
    return str(n)


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def render(sessions):
    """sessions: {session_id: {"started": str, "updated": str, "models": {model: tally}}}"""
    rows, grand_tokens, grand_cost, unpriced = [], 0, 0.0, False
    ordered = sorted(sessions.items(), key=lambda kv: kv[1].get("started", ""))
    for sid, s in ordered:
        for model, t in sorted(s["models"].items(), key=lambda kv: -total_tokens(kv[1])):
            tokens = total_tokens(t)
            c = cost(model, t)
            grand_tokens += tokens
            if c is None:
                unpriced = True
            else:
                grand_cost += c
            rows.append(
                f"| `{sid[:8]}` | `{model}` | {t['calls']} | {fmt(t['inp'] + t['w5'] + t['w1h'])} "
                f"| {fmt(t['read'])} | {fmt(t['out'])} | {fmt(tokens)} | {'—' if c is None else f'${c:,.2f}'} |"
            )
    n = len(sessions)
    note = " (excludes models with unknown pricing)" if unpriced else ""
    data = json.dumps(sessions, separators=(",", ":"))
    return "\n".join([
        MARKER,
        f"### 🤖 Claude session cost: ~{fmt(grand_tokens)} tokens"
        + (f" across {n} sessions" if n > 1 else ""),
        "",
        "| Session | Model | API calls | Input + cache write | Cache read | Output | Total tokens | API-equiv. $ |",
        "|---|---|--:|--:|--:|--:|--:|--:|",
        *rows,
        "",
        f"**API-equivalent cost:** ~${grand_cost:,.2f}{note}. On a Pro/Max subscription this is "
        "usage against plan limits, not a charge. Each session is counted up to its latest "
        "PR open or push; includes subagents; list prices, so no batch/priority discounts.",
        "",
        f"<sub>Updated {now_utc()}</sub>",
        f"<!-- claude-session-cost-data {data} -->",
    ])


# ---------------------------------------------------------------- GitHub

def api(method, path, body=None):
    """Call the GitHub REST API via gh, falling back to curl + GH_TOKEN/GITHUB_TOKEN."""
    payload = json.dumps(body) if body is not None else None
    try:
        args = ["gh", "api", "-X", method, path]
        if method == "GET":
            args.append("--paginate")
        if payload is not None:
            args += ["--input", "-"]
        r = subprocess.run(args, input=payload, text=True, capture_output=True, timeout=30)
        if r.returncode == 0:
            return _parse_json_pages(r.stdout)
        err = r.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        err = str(e)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError(err)
    args = ["curl", "-sS", "-f", "-X", method,
            "-H", f"Authorization: Bearer {token}", "-H", "Accept: application/vnd.github+json",
            f"https://api.github.com/{path}"]
    if payload is not None:
        args += ["--data-binary", "@-"]
    r = subprocess.run(args, input=payload, text=True, capture_output=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"curl exited {r.returncode}")
    return json.loads(r.stdout) if r.stdout.strip() else None


def _parse_json_pages(text):
    # `gh api --paginate` concatenates one JSON array per page.
    text, out, dec, i = text.strip(), [], json.JSONDecoder(), 0
    if not text:
        return None
    while i < len(text):
        obj, i = dec.raw_decode(text, i)
        if not isinstance(obj, list):
            return obj
        out.extend(obj)
        while i < len(text) and text[i].isspace():
            i += 1
    return out


def upsert(pr_url, session_id, models, dry=False):
    """Merge this session into the PR's cost comment, creating the comment if needed."""
    owner, repo, num = PR_URL_RE.search(pr_url).groups()
    existing, sessions = None, {}
    for c in api("GET", f"repos/{owner}/{repo}/issues/{num}/comments?per_page=100") or []:
        if MARKER in (c.get("body") or ""):
            existing = c
    if existing:
        m = DATA_RE.search(existing["body"])
        if m:
            try:
                sessions = json.loads(m.group(1))
            except ValueError:
                sessions = {}
    stamp = now_utc()
    prev = sessions.get(session_id, {})
    sessions[session_id] = {"started": prev.get("started", stamp), "updated": stamp, "models": models}
    body = render(sessions)
    if dry:
        return body
    if existing:
        api("PATCH", f"repos/{owner}/{repo}/issues/comments/{existing['id']}", {"body": body})
        return f"updated {existing['html_url']}"
    c = api("POST", f"repos/{owner}/{repo}/issues/{num}/comments", {"body": body})
    return f"posted {c.get('html_url', pr_url)}"


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
        r = subprocess.run(["gh", "pr", "view", *args[:1], "--json", "url", "-q", ".url"],
                           capture_output=True, text=True)
        pr_url = r.stdout.strip()
        session = os.path.basename(transcript)[: -len(".jsonl")]
        models = tally(transcripts_for(transcript))
        if dry and not pr_url:
            print(render({session: {"started": now_utc(), "updated": now_utc(), "models": models}}))
            return
        if not pr_url:
            sys.exit(f"Could not resolve PR: {r.stderr.strip()}")
        try:
            print(upsert(pr_url, session, models, dry=dry))
        except RuntimeError as e:
            sys.exit(f"Failed: {e}")
        return

    # Hook mode: never fail the tool call
    try:
        event = json.load(sys.stdin)
        transcript = event.get("transcript_path")
        if not transcript:
            return
        pr_url = pr_opened(event)
        if not pr_url and pushed(event):
            pr_url = open_pr_for_branch(event.get("cwd") or os.getcwd())
        if not pr_url:
            return
        result = upsert(pr_url, event.get("session_id", "unknown"), tally(transcripts_for(transcript)))
        print(json.dumps({"systemMessage": f"PR session cost {result}"}))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"systemMessage": f"pr-session-cost hook error: {e}"}))


if __name__ == "__main__":
    main()
