import subprocess
from types import SimpleNamespace

from raspi.core.models import Host
from raspi.ssh import transport
from raspi.ssh.transport import SshTransport


def test_ssh_is_started_without_a_console_window(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout=b"ok", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = SshTransport("ssh").run_script(Host("pi", "10.0.0.1", "u"), "true\n")
    assert result.ok and result.stdout == "ok"
    assert seen["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    assert seen["input"] == b"true\n"  # bytes: no CRLF translation on Windows
    assert transport._NO_WINDOW == seen["creationflags"]


def test_script_is_read_whole_before_it_runs(monkeypatch):
    """A command that reads stdin must not swallow the rest of the script."""
    import shutil

    import pytest

    real_run = subprocess.run
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    SshTransport("ssh").run_script(Host("pi", "10.0.0.1", "u"), "true\n")
    assert seen["cmd"][-1] == transport.REMOTE_COMMAND
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("no local sh to run the script semantics")
    script = b"cat >/dev/null\necho after\n"
    old = real_run([sh, "-s"], input=script, capture_output=True, check=False)
    new = real_run(
        [sh, "-c", 'eval "$(cat)"'], input=script, capture_output=True, check=False
    )
    assert b"after" not in old.stdout  # the bug this guards against
    assert new.stdout.strip() == b"after"
