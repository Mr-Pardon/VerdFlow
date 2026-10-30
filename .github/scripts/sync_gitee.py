"""One-way, non-destructive publication mirror; no application runtime dependency."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Iterator


SOURCE = "Mr-Pardon/VerdFlow"
TARGET = "https://gitee.com/mr-pardon/VerdFlow.git"
MAIN = "refs/heads/main"
ASKPASS = '''#!/usr/bin/env python3
import os, re, sys
from urllib.parse import urlsplit
prompt = sys.argv[1] if len(sys.argv) > 1 else ""
match = re.search(r"https://[^'\\s]+", prompt)
url = urlsplit(match.group(0)) if match else None
if not url or url.hostname != "gitee.com" or url.port not in (None, 443):
    sys.exit(1)
if prompt.startswith("Username"):
    print("mr-pardon")
elif prompt.startswith("Password") and url.username == "mr-pardon":
    print(os.environ["GITEE_SYNC_TOKEN"])
else:
    sys.exit(1)
'''


class SyncError(RuntimeError):
    pass


def git(root: Path, *args: str, env: dict[str, str] | None = None, allow: tuple[int, ...] = (0,)) -> str:
    result = subprocess.run(
        ["git", "-c", "credential.helper=", "-c", "http.sslVerify=true", "-c", "http.followRedirects=false",
         "-c", "core.hooksPath=/dev/null", *args], cwd=root, env=env,
        capture_output=True, text=True, timeout=180,
    )
    if result.returncode not in allow:
        # Authentication diagnostics can contain credentials: keep raw subprocess output out of logs.
        raise SyncError(f"Git {args[0]} failed ({result.returncode}); check network, credentials and branch protections.")
    return result.stdout.strip()


def refs(output: str) -> dict[str, str]:
    result = {}
    for line in output.splitlines():
        oid, name = line.split()
        if name == MAIN or name.startswith("refs/tags/v"):
            result[name] = oid
    return result


def synchronize(root: Path, target: str, env: dict[str, str] | None = None) -> dict:
    desired = refs(git(root, "for-each-ref", "--format=%(objectname) %(refname)", MAIN, "refs/tags/v*"))
    if MAIN not in desired:
        raise SyncError("Source main branch is missing.")
    current = refs(git(root, "ls-remote", "--refs", target, MAIN, "refs/tags/v*", env=env))
    for name, oid in desired.items():
        if name != MAIN and name in current and current[name] != oid:
            raise SyncError(f"Conflicting version tag {name}; no refs were pushed.")
    if MAIN in current and current[MAIN] != desired[MAIN]:
        git(root, "fetch", "--no-tags", target, MAIN, env=env)
        if git(root, "rev-parse", "FETCH_HEAD") != current[MAIN]:
            raise SyncError("Gitee changed during preflight; retry without overwriting it.")
        ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", current[MAIN], desired[MAIN]], cwd=root,
                                  capture_output=True, timeout=30)
        if ancestor.returncode != 0:
            raise SyncError("Gitee main has diverged; no refs were pushed. Resolve changes manually on GitHub.")
    updates = [f"{oid}:{name}" for name, oid in desired.items() if current.get(name) != oid]
    if updates:
        git(root, "push", "--porcelain", target, *updates, env=env)
    actual = refs(git(root, "ls-remote", "--refs", target, MAIN, "refs/tags/v*", env=env))
    if any(actual.get(name) != oid for name, oid in desired.items()):
        raise SyncError("Remote verification changed or failed; retry the idempotent sync.")
    return {"status": "synced", "updated_refs": len(updates), "main": desired[MAIN],
            "version_tags": len(desired) - 1}


@contextmanager
def credentials(token: str) -> Iterator[dict[str, str]]:
    if not token.strip():
        raise SyncError("Configure the repository secret GITEE_SYNC_TOKEN first. Never paste it into logs or code.")
    with tempfile.TemporaryDirectory(prefix="verdflow-gitee-") as temporary:
        helper = Path(temporary) / "askpass.py"
        helper.write_text(ASKPASS, encoding="utf-8", newline="\n")
        helper.chmod(0o700)
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_TRACE")
               and key not in {"GIT_CURL_VERBOSE", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS"}}
        env.update(GIT_ASKPASS=str(helper), GIT_TERMINAL_PROMPT="0", GITEE_SYNC_TOKEN=token, LC_ALL="C")
        try:
            yield env
        finally:
            env.pop("GITEE_SYNC_TOKEN", None)


def main() -> None:
    if os.environ.get("GITHUB_REPOSITORY") != SOURCE or os.environ.get("GITHUB_ACTIONS") != "true":
        raise SyncError("Credentialed synchronization is restricted to the canonical GitHub Actions repository.")
    root = Path.cwd()
    origin = git(root, "remote", "get-url", "origin").removesuffix(".git").lower()
    if origin != f"https://github.com/{SOURCE}".lower():
        raise SyncError("Unexpected source repository.")
    with credentials(os.environ.get("GITEE_SYNC_TOKEN", "")) as env:
        print(json.dumps(synchronize(root, TARGET, env)))


if __name__ == "__main__":
    try:
        main()
    except (SyncError, subprocess.TimeoutExpired) as error:
        print(str(error) if isinstance(error, SyncError) else "Git operation timed out; no force or delete was requested.")
        raise SystemExit(1)
