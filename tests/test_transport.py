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
