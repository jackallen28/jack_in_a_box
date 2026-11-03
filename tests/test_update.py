"""Tests for the auto-updater (deploy/update.sh) and token storage (deploy/github-auth.sh).

They use a real local git "GitHub" (bare repo), a real clone, and stand-ins for systemctl/sudo and for
the running app, so the real script runs end to end: update, dependency change, rollback, expired token...
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# Ignore the machine's own git settings (files AND GIT_CONFIG_* variables): they may rewrite URLs etc.
HERMETIC = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_CONFIG_COUNT": "0",
            "GIT_TERMINAL_PROMPT": "0"}
pytestmark = pytest.mark.skipif(not (shutil.which("git") and shutil.which("curl") and shutil.which("flock")),
                                reason="needs git, curl and flock")


def sh(cmd, cwd=None, env=None, check=True):
    env = {**os.environ, **HERMETIC, **(env or {})}
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, shell=isinstance(cmd, str))
    if check and r.returncode:
        raise AssertionError(f"{cmd}\n{r.stdout}\n{r.stderr}")
    return r


class Rig:
    """origin.git  ->  'GitHub';  pi/  ->  the clone on the Pi.

    layout "root": the project IS the repo (the real layout of jack_in_a_box).
    layout "nested": the project sits in a sub-folder of a bigger repo (the layout it used to have)."""

    def __init__(self, tmp: Path, layout: str = "root"):
        self.sub = "jack-in-a-box/" if layout == "nested" else ""
        self.tmp = tmp
        self.origin, self.work, self.pi = tmp / "origin.git", tmp / "work", tmp / "pi"
        self.dir = self.pi / self.sub.rstrip("/") if self.sub else self.pi
        self.data = self.dir / "data"
        self.bin = tmp / "bin"
        self.calls = tmp / "calls.log"
        self.port = 18000 + os.getpid() % 1000
        g = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
        self.g = g
        sh(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)])
        sh(["git", "clone", "-q", str(self.origin), str(self.work)])
        (self.work / (self.sub + "deploy")).mkdir(parents=True)
        shutil.copy(ROOT / "deploy/update.sh", self.work / (self.sub + "deploy/update.sh"))
        (self.work / (self.sub + "deploy/update.sh")).chmod(0o755)
        (self.work / (self.sub + "requirements.txt")).write_text("flask\n")
        (self.work / (self.sub + ".gitignore")).write_text("data/\n.venv/\n")
        self.commit("v1", {self.sub + "VERSION": "1"})
        sh(["git", "clone", "-q", "-b", "main", str(self.origin), str(self.pi)])
        # fake venv + stubs
        (self.dir / ".venv/bin").mkdir(parents=True)
        (self.dir / ".venv/bin/python").symlink_to(sys.executable)
        self.script(self.dir / ".venv/bin/pip", f'echo "pip $*" >> {self.calls}')
        self.bin.mkdir()
        (self.tmp / "fakeapp.py").write_text(textwrap.dedent(f"""
            import http.server, socketserver
            class H(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200); self.end_headers(); self.wfile.write(b'{{}}')
                def log_message(self, *a): pass
            socketserver.TCPServer.allow_reuse_address = True
            socketserver.TCPServer(("127.0.0.1", {self.port}), H).serve_forever()
            """))
        self.script(self.bin / "sudo", 'if [ "$1" = "-n" ]; then shift; fi; exec "$@"')
        self.script(self.bin / "systemctl", f"""
            echo "systemctl $*" >> {self.calls}
            [ "$1" = restart ] || exit 0
            [ -e {self.tmp}/NO_SUDO ] && exit 1
            [ -f {self.tmp}/app.pid ] && kill $(cat {self.tmp}/app.pid) 2>/dev/null; sleep 0.3
            if [ ! -e {self.dir}/BROKEN ]; then
              nohup {sys.executable} {self.tmp}/fakeapp.py >/dev/null 2>&1 &
              echo $! > {self.tmp}/app.pid
            fi
            """)
        self.data.mkdir()
        self.set_config(updates={"enabled": True}, web={"port": self.port})
        self.env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "SYSTEMCTL": str(self.bin / "systemctl"),
                    "JACK_IN_A_BOX_DATA": str(self.data), "UPDATE_HEALTH_SECONDS": "4", **HERMETIC}

    def p(self, name: str) -> str:
        return self.sub + name

    @staticmethod
    def script(path: Path, body: str):
        path.write_text("#!/bin/bash\n" + textwrap.dedent(body))
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def set_config(self, **kw):
        (self.data / "config.json").write_text(json.dumps(kw))

    def commit(self, msg, files):
        for name, content in files.items():
            p = self.work / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        sh(self.g + ["add", "-A"], cwd=self.work)
        sh(self.g + ["commit", "-q", "-m", msg], cwd=self.work)
        sh(["git", "push", "-q", "origin", "main"], cwd=self.work)
        return sh(["git", "rev-parse", "HEAD"], cwd=self.work).stdout.strip()

    def run(self, *args):
        r = subprocess.run([str(self.dir / "deploy/update.sh"), *args], env=self.env, capture_output=True, text=True, timeout=60)
        st = json.loads((self.data / "update-status.json").read_text()) if (self.data / "update-status.json").exists() else None
        return r.returncode, st

    def head(self):
        return sh(["git", "rev-parse", "HEAD"], cwd=self.pi).stdout.strip()

    def log(self):
        return self.calls.read_text() if self.calls.exists() else ""

    def stop(self):
        if (self.tmp / "app.pid").exists():
            subprocess.run(["kill", (self.tmp / "app.pid").read_text().strip()], capture_output=True)


@pytest.fixture(params=["root", "nested"])
def rig(tmp_path, request):
    r = Rig(tmp_path, request.param)
    r.script(r.bin / "start", "")  # no-op, keeps bin non-empty
    sh([str(r.bin / "systemctl"), "restart"], check=True)   # "boot" the fake app
    yield r
    r.stop()


def test_up_to_date_does_nothing(rig):
    rc, st = rig.run()
    assert rc == 0 and st["result"] == "up_to_date"
    assert "restart" not in rig.log().replace("systemctl restart", "", 1)       # only the initial boot restart


def test_update_applies_restarts_and_reports(rig):
    new = rig.commit("Add the friends queue", {rig.p("VERSION"): "2"})
    rc, st = rig.run()
    assert rc == 0 and st["result"] == "updated" and "Add the friends queue" in st["message"]
    assert rig.head() == new and (rig.dir / "VERSION").read_text() == "2"
    assert rig.log().count("restart") == 2 and "pip" not in rig.log()          # requirements unchanged
    assert rig.run()[1]["result"] == "up_to_date"                               # and it's idempotent


def test_dependency_change_runs_pip(rig):
    rig.commit("needs a new package", {rig.p("requirements.txt"): "flask\nnewpkg\n"})
    rc, st = rig.run()
    assert st["result"] == "updated" and "pip install --quiet -r" in rig.log()


def test_data_folder_survives_updates(rig):
    (rig.data / "history").mkdir()
    (rig.data / "history/1.png").write_text("keep me")
    rig.commit("v2", {rig.p("VERSION"): "2"})
    rig.run()
    assert (rig.data / "history/1.png").read_text() == "keep me" and (rig.data / "config.json").exists()


def test_broken_update_rolls_back_and_is_not_retried(rig):
    good = rig.head()
    bad = rig.commit("this will not start", {rig.p("BROKEN"): "x"})
    rc, st = rig.run()
    assert rc == 1 and st["result"] == "rolled_back" and bad[:7] in st["message"]
    assert rig.head() == good and not (rig.dir / "BROKEN").exists()             # back on the working version
    assert (rig.data / "update-bad-commit").read_text().strip() == bad
    restarts = rig.log().count("restart")
    assert restarts == 3                                                         # boot, bad version, rolled-back version
    rc, st = rig.run()                                                          # next hourly check
    assert rc == 0 and st["result"] == "error" and "Skipping" in st["message"]
    assert rig.log().count("restart") == restarts                                # NOT retried
    sh(f"git rm -q {rig.p('BROKEN')} && git -c user.name=t -c user.email=t@t commit -q -m unbreak && git push -q origin main", cwd=rig.work)
    rc, st = rig.run()
    assert st["result"] == "updated" and not (rig.data / "update-bad-commit").exists()


def test_switched_off_unless_forced(rig):
    rig.set_config(updates={"enabled": False}, web={"port": rig.port})
    new = rig.commit("v2", {rig.p("VERSION"): "2"})
    old = rig.head()
    rc, st = rig.run()
    assert rig.head() == old and st is None                                      # auto-update off: nothing happens
    (rig.data / "update-now").write_text("1")                                    # what the web page's button does
    rc, st = rig.run()
    assert st["result"] == "updated" and rig.head() == new and not (rig.data / "update-now").exists()


def test_expired_token_or_offline_reports_clearly(rig):
    sh(["git", "remote", "set-url", "origin", str(rig.tmp / "gone.git")], cwd=rig.pi)
    rc, st = rig.run()
    assert rc == 1 and st["result"] == "error" and "Couldn't reach GitHub" in st["message"] and "token" in st["message"]


def test_zip_install_explains_itself(rig, tmp_path):
    plain = tmp_path / "zip" / "app-folder"
    shutil.copytree(rig.dir / "deploy", plain / "deploy")
    r = subprocess.run([str(plain / "deploy/update.sh")], env={**rig.env, "JACK_IN_A_BOX_DATA": str(plain / "data")},
                       capture_output=True, text=True)
    st = json.loads((plain / "data/update-status.json").read_text())
    assert r.returncode == 1 and "ZIP" in st["message"]


def test_cant_restart_is_reported_not_rolled_back(rig):
    (rig.tmp / "NO_SUDO").write_text("1")
    new = rig.commit("v2", {rig.p("VERSION"): "2"})
    rc, st = rig.run()
    assert rc == 1 and st["result"] == "error" and "restart" in st["message"] and rig.head() == new


# ---------------------------------------------------------------- token storage
def auth(repo, origin, extra_env=None, tty=False):
    sh(["git", "init", "-q", str(repo)])
    sh(["git", "remote", "add", "origin", origin], cwd=repo)
    cred = repo.parent / "data" / ".git-credentials"
    # hermetic: ignore the machine's own git config (it may rewrite URLs or set credential helpers)
    env = {**os.environ, **HERMETIC, **(extra_env or {})}
    r = subprocess.run(["bash", "-c", f'source {ROOT}/deploy/github-auth.sh; setup_github_auth "{repo}" "{cred}"'],
                       env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return r, cred


def test_token_in_clone_url_is_moved_to_a_private_file(tmp_path):
    r, cred = auth(tmp_path / "repo", "https://jackallen28:ghp_SECRET123@github.com/jackallen28/jackallen.git", {"JIAB_NO_VERIFY": "1"})
    assert r.returncode == 0, r.stderr
    url = sh(["git", "remote", "get-url", "origin"], cwd=tmp_path / "repo").stdout.strip()
    assert url == "https://github.com/jackallen28/jackallen.git" and "SECRET" not in url      # not left in .git/config
    assert "SECRET" not in (tmp_path / "repo/.git/config").read_text()
    assert "ghp_SECRET123" in cred.read_text() and stat.S_IMODE(cred.stat().st_mode) == 0o600
    got = subprocess.run(["git", "-C", str(tmp_path / "repo"), "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                         capture_output=True, text=True,
                         env={**os.environ, **HERMETIC}).stdout
    assert "password=ghp_SECRET123" in got and "username=jackallen28" in got                    # git really will reuse it


def test_failed_check_without_terminal_skips_cleanly(tmp_path):
    r, cred = auth(tmp_path / "repo", "https://127.0.0.1:1/jackallen28/x.git")                 # refuses connections instantly
    assert r.returncode == 1 and "Skipped" in r.stdout and cred.read_text() == ""


def test_ssh_remote_is_left_alone(tmp_path):
    r, cred = auth(tmp_path / "repo", "git@github.com:jackallen28/jackallen.git")
    assert r.returncode == 0 and not cred.exists()
