import importlib.util
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "prc", os.path.join(HERE, "..", "skills", "pr-cost", "pr-session-cost.py"))
prc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prc)

URL = "https://github.com/o/r/pull/7"


def event(tool, cmd=None, resp=URL):
    return {"tool_name": tool, "tool_input": {"command": cmd} if cmd else {}, "tool_response": resp}


class Detection(unittest.TestCase):
    def test_pr_opened(self):
        cases = [
            (event("Bash", "gh pr create --title x --body y"), True),
            (event("Bash", "gh api repos/o/r/pulls -f title=x -f head=b"), True),
            (event("Bash", "curl -X POST https://api.github.com/repos/o/r/pulls -d @b.json"), True),
            (event("mcp__github__create_pull_request", resp={"html_url": URL}), True),
            (event("Bash", "gh pr view 7"), False),
            (event("Bash", "gh api repos/o/r/pulls"), False),
            (event("Bash", "gh api repos/o/r/pulls/7/comments -f body=hi"), False),
            (event("mcp__github__get_pull_request", resp={"html_url": URL}), False),
        ]
        for ev, want in cases:
            with self.subTest(ev=ev):
                self.assertEqual(bool(prc.pr_opened(ev)), want)

    def test_pushed(self):
        ok = {"stdout": "", "stderr": "To github.com:o/r.git\n   abc..def  main -> main"}
        cases = [
            (event("Bash", "git push", ok), True),
            (event("Bash", "git push -u origin feature", ok), True),
            (event("Bash", "git -C /repo push", ok), True),
            (event("Bash", "git push --dry-run", ok), False),
            (event("Bash", "git push", {"stderr": " ! [rejected]  main -> main (fetch first)"}), False),
            (event("Bash", "git pull", ok), False),
            (event("Read", resp=ok), False),
        ]
        for ev, want in cases:
            with self.subTest(ev=ev):
                self.assertEqual(prc.pushed(ev), want)


class Tally(unittest.TestCase):
    def test_dedupes_repeated_message_ids(self):
        usage = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100,
                 "cache_creation_input_tokens": 40, "cache_creation": {"ephemeral_1h_input_tokens": 30}}
        rec = {"type": "assistant", "message": {"id": "m1", "model": "claude-opus-5", "usage": usage}}
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(json.dumps(rec) for _ in range(3)) + "\n")
        t = prc.tally([f.name])["claude-opus-5"]
        os.unlink(f.name)
        self.assertEqual((t["calls"], t["inp"], t["w5"], t["w1h"], t["read"], t["out"]), (1, 10, 10, 30, 100, 5))


class Upsert(unittest.TestCase):
    def setUp(self):
        self.comments = []
        self.calls = []

        def fake_api(method, path, body=None):
            self.calls.append(method)
            if method == "GET":
                return list(self.comments)
            if method == "POST":
                c = {"id": 1, "html_url": URL + "#c1", "body": body["body"]}
                self.comments.append(c)
                return c
            if method == "PATCH":
                self.comments[0]["body"] = body["body"]
                return self.comments[0]
        self._real_api, prc.api = prc.api, fake_api

    def tearDown(self):
        prc.api = self._real_api

    def models(self, out):
        return {"claude-opus-5": dict(inp=0, w5=0, w1h=0, read=0, out=out, calls=1)}

    def test_posts_then_edits_and_accumulates_sessions(self):
        prc.upsert(URL, "session-a", self.models(1000))
        prc.upsert(URL, "session-a", self.models(3000))  # same session, later push: replaced, not added
        prc.upsert(URL, "session-b", self.models(2000))  # new session: added
        self.assertEqual(self.calls.count("POST"), 1)
        self.assertEqual(len(self.comments), 1)
        body = self.comments[0]["body"]
        self.assertIn("~5.0k tokens across 2 sessions", body)
        data = json.loads(prc.DATA_RE.search(body).group(1))
        self.assertEqual(sorted(data), ["session-a", "session-b"])
        self.assertEqual(data["session-a"]["models"]["claude-opus-5"]["out"], 3000)


if __name__ == "__main__":
    unittest.main()
