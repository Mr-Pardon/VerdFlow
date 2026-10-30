from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/sync_gitee.py"
spec = importlib.util.spec_from_file_location("sync_gitee", SCRIPT)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL, text=True).strip()


class GiteeSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source = root / "source"
        self.source.mkdir()
        git(self.source, "init", "-b", "main")
        git(self.source, "config", "user.name", "Fixture")
        git(self.source, "config", "user.email", "fixture@example.invalid")
        git(self.source, "commit", "--allow-empty", "-m", "initial")
        git(self.source, "tag", "-a", "v1.0.0", "-m", "version")
        self.remote = root / "target.git"
        git(root, "init", "--bare", str(self.remote))
        git(self.source, "push", str(self.remote), "main", "refs/tags/v1.0.0")

    def test_forward_sync_keeps_history_tags_and_target_only_refs(self):
        old = git(self.source, "rev-parse", "HEAD")
        git(self.source, "push", str(self.remote), "HEAD:refs/heads/gitee-only")
        git(self.source, "branch", "private-experiment")
        git(self.source, "tag", "not-a-version")
        git(self.source, "commit", "--allow-empty", "-m", "next")
        git(self.source, "tag", "v1.0.1")
        result = sync.synchronize(self.source, str(self.remote))
        self.assertEqual(result["updated_refs"], 2)
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/main"), result["main"])
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/gitee-only"), old)
        self.assertEqual(git(self.remote, "rev-parse", "refs/tags/v1.0.0"), git(self.source, "rev-parse", "v1.0.0"))
        self.assertEqual(git(self.remote, "for-each-ref", "--format=%(refname)", "refs/heads/private-experiment", "refs/tags/not-a-version"), "")
        self.assertEqual(sync.synchronize(self.source, str(self.remote))["updated_refs"], 0)

    def test_diverged_main_is_rejected_without_pushing_tags(self):
        git(self.source, "commit", "--allow-empty", "-m", "gitee edit")
        gitee = git(self.source, "rev-parse", "HEAD")
        git(self.source, "push", str(self.remote), "main")
        git(self.source, "switch", "--orphan", "different")
        git(self.source, "commit", "--allow-empty", "-m", "different history")
        git(self.source, "branch", "-f", "main", "HEAD")
        git(self.source, "switch", "main")
        git(self.source, "tag", "v2.0.0")
        with self.assertRaisesRegex(sync.SyncError, "diverged"):
            sync.synchronize(self.source, str(self.remote))
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/main"), gitee)
        self.assertEqual(git(self.remote, "for-each-ref", "--format=%(refname)", "refs/tags/v2.0.0"), "")

    def test_tag_conflict_blocks_all_changes(self):
        old = git(self.source, "rev-parse", "HEAD")
        git(self.source, "commit", "--allow-empty", "-m", "next")
        git(self.source, "tag", "-f", "v1.0.0")
        with self.assertRaisesRegex(sync.SyncError, "Conflicting version tag"):
            sync.synchronize(self.source, str(self.remote))
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/main"), old)

    def test_missing_secret_and_forks_are_rejected(self):
        with self.assertRaisesRegex(sync.SyncError, "GITEE_SYNC_TOKEN"):
            with sync.credentials(""):
                pass
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "someone/fork", "GITHUB_ACTIONS": "true"}):
            with self.assertRaisesRegex(sync.SyncError, "canonical"):
                sync.main()

    def test_helper_bounds_credential_host_and_cleans_up(self):
        token = "fixture-only-not-a-real-token"
        with sync.credentials(token) as env:
            helper = Path(env["GIT_ASKPASS"])
            def ask(prompt):
                return subprocess.run([sys.executable, str(helper), prompt], env=env, capture_output=True, text=True)
            self.assertEqual(ask("Username for 'https://gitee.com':").stdout.strip(), "mr-pardon")
            self.assertEqual(ask("Password for 'https://mr-pardon@gitee.com':").stdout.strip(), token)
            for url in ("https://example.invalid", "https://gitee.com.example.invalid", "https://other@gitee.com"):
                result = ask(f"Password for '{url}':")
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(token, result.stdout + result.stderr)
            self.assertNotIn(token, helper.read_text(encoding="utf-8"))
        self.assertFalse(helper.exists())
        self.assertNotIn("GITEE_SYNC_TOKEN", env)

    def test_authentication_error_does_not_echo_git_diagnostics(self):
        token = "fixture-only-sensitive-output"
        with patch.object(sync.subprocess, "run", return_value=subprocess.CompletedProcess([], 128, token, token)):
            with self.assertRaises(sync.SyncError) as caught:
                sync.git(self.source, "push", "approved")
        self.assertNotIn(token, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
