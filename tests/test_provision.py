import pytest
from typer.testing import CliRunner

from raspi.core.models import Host
from raspi.core.provision import (
    build_check_script,
    build_provision_script,
    missing_packages,
    parse_checks,
    provision_steps,
)
from raspi.services.provision import doctor, provision
from raspi.ssh.transport import RunResult

FIT = """os=Debian GNU/Linux 13 (trixie)
machine=aarch64
sudo=yes
git=git version 2.47.3
curl=curl 8.14.1
uv=
node=v22.11.0
cloudflared=
apps_dir=yes
disk_mb=20000
mem_mb=3800
"""
BARE = (
    FIT.replace("git=git version 2.47.3", "git=")
    .replace("curl=curl 8.14.1", "curl=")
    .replace("apps_dir=yes", "apps_dir=no")
)
HOST = Host("pi", "10.0.0.1", "pi")


class Pi:
    def __init__(self, output: str = BARE, ok: bool = True):
        self.output, self.ok, self.scripts = output, ok, []

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        if "uname -m" in script:
            return RunResult(self.ok, self.output, "" if self.ok else "timeout")
        return RunResult(True, "provisioned\n")


def test_check_script_is_read_only():
    script = build_check_script()
    for forbidden in ("apt", "install", "mkdir", "rm ", ">>", "tee"):
        assert forbidden not in script
    assert "sudo -n true" in script


def test_fit_pi_has_nothing_to_install():
    checks = parse_checks(FIT)
    assert all(c.ok for c in checks if c.required)
    assert provision_steps(checks) == []


def test_missing_items_are_reported_with_a_fix():
    checks = {c.key: c for c in parse_checks(BARE)}
    assert not checks["git"].ok and checks["git"].installable
    assert not checks["apps_dir"].ok
    assert not checks["uv"].ok and not checks["uv"].required
    assert missing_packages(list(checks.values())) == ["git", "curl"]
    assert provision_steps(list(checks.values())) == [
        "Install with apt: git, curl",
        "Create the folder ~/apps",
    ]


def test_low_disk_and_no_sudo_make_the_pi_unfit():
    out = FIT.replace("disk_mb=20000", "disk_mb=300").replace("sudo=yes", "sudo=no")
    bad = {c.key for c in parse_checks(out) if c.required and not c.ok}
    assert bad == {"sudo", "disk"}


def test_provision_script_only_accepts_whitelisted_packages():
    script = build_provision_script(["git"], True)
    assert "apt-get install -y git" in script and 'mkdir -p "$HOME/apps"' in script
    with pytest.raises(ValueError):
        build_provision_script(["git; rm -rf /"], False)


def test_doctor_reports_an_unreachable_pi():
    report = doctor(HOST, Pi(ok=False))
    assert not report.ok and "timeout" in report.message


def test_provision_dry_run_changes_nothing():
    pi = Pi()
    result = provision(HOST, pi, dry_run=True)
    assert result.ok and "apt-get install" in result.script
    assert len(pi.scripts) == 1  # only the check ran


def test_provision_installs_then_reports():
    pi = Pi()
    result = provision(HOST, pi)
    assert result.ok and len(pi.scripts) == 2 and "apt-get" in pi.scripts[1]


def test_provision_does_nothing_on_a_fit_pi():
    pi = Pi(FIT)
    assert provision(HOST, pi).message == "Nothing to install."
    assert len(pi.scripts) == 1


def test_provision_refuses_without_sudo_when_packages_are_missing():
    pi = Pi(BARE.replace("sudo=yes", "sudo=no"))
    result = provision(HOST, pi)
    assert not result.ok and "sudo" in result.message
    assert len(pi.scripts) == 1


def test_cli_doctor_and_provision_dry_run(monkeypatch):
    from raspi.cli import main as cli

    monkeypatch.setattr(cli, "_live_host", lambda name: (HOST, HOST))
    monkeypatch.setattr(cli, "SshTransport", lambda: Pi())
    runner = CliRunner()
    doctor_out = runner.invoke(cli.app, ["host", "doctor", "pi"])
    assert doctor_out.exit_code == 1 and "✕ git" in doctor_out.output
    dry = runner.invoke(cli.app, ["host", "provision", "pi", "--dry-run"])
    assert dry.exit_code == 0 and "apt-get install" in dry.output
