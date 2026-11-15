import pytest
from typer.testing import CliRunner

from raspi.core.deploykey import (
    DeployKeyError,
    build_key_script,
    build_verify_script,
    check_repo,
    clone_url,
)
from raspi.core.models import Host
from raspi.services.deploykey import ensure_key, verify_repo
from raspi.ssh.transport import RunResult

KEYS = ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMq", "ecdsa-sha2-nistp256 AAAAE2Vj/+="]
HOST = Host("pi", "10.0.0.1", "pi")
PUB = "ssh-ed25519 AAAAC3Nza raspi-game@pi"


class Pi:
    def __init__(self, verify_error: str = ""):
        self.verify_error, self.scripts = verify_error, []

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        if "ls-remote" in script:
            if self.verify_error:
                return RunResult(False, "", self.verify_error)
            return RunResult(True, "reachable\n")
        return RunResult(True, f"pub={PUB}\n")


def test_key_script_is_idempotent_and_uses_a_dedicated_alias():
    script = build_key_script("game", "pi", KEYS)
    assert '[ -f "$k" ] || ssh-keygen' in script
    assert "grep -qxF" in script and "Host github-game" in script
    assert "IdentitiesOnly yes" in script and "ssh-keygen -F github.com" in script
    assert "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMq" in script


def test_github_keys_must_look_like_host_keys():
    with pytest.raises(DeployKeyError):
        build_key_script("game", "pi", ["x; rm -rf /"])


@pytest.mark.parametrize("bad", ["game; id", "", "a b", "-x"])
def test_unsafe_names_are_refused(bad):
    with pytest.raises(DeployKeyError):
        build_key_script(bad, "pi", KEYS)


@pytest.mark.parametrize("bad", ["owner", "o/r/x", "a/..", "-x/y", "a b/c", ""])
def test_unsafe_repos_are_refused(bad):
    with pytest.raises(DeployKeyError):
        check_repo(bad)


def test_clone_url_goes_through_the_alias():
    assert clone_url("game", "me/web-game.git") == "git@github-game:me/web-game.git"
    assert "GIT_TERMINAL_PROMPT=0" in build_verify_script("game", "me/web-game")


def test_ensure_key_dry_run_changes_nothing():
    pi = Pi()
    result = ensure_key(HOST, pi, "game", dry_run=True, lookup=lambda: KEYS)
    assert result.ok and result.script and not pi.scripts


def test_ensure_key_returns_the_public_key():
    result = ensure_key(HOST, Pi(), "game", lookup=lambda: KEYS)
    assert result.ok and result.public_key == PUB


def test_ensure_key_reports_a_github_lookup_failure():
    def broken():
        raise DeployKeyError("offline")

    result = ensure_key(HOST, Pi(), "game", lookup=broken)
    assert not result.ok and "offline" in result.message


def test_verify_explains_a_missing_deploy_key():
    result = verify_repo(
        HOST, Pi("git@github.com: Permission denied (publickey)"), "game", "me/r"
    )
    assert not result.ok and "Deploy keys" in result.message
    assert verify_repo(HOST, Pi(), "game", "me/r").ok


def test_cli_deploy_key_dry_run_and_verify(monkeypatch):
    from raspi.cli import main as cli

    monkeypatch.setattr(cli, "_live_host", lambda name: (HOST, HOST))
    pi = Pi()
    monkeypatch.setattr("raspi.cli.provision.SshTransport", lambda: pi)
    monkeypatch.setattr("raspi.cli.provision.fetch_github_keys", lambda: KEYS)
    runner = CliRunner()
    dry = runner.invoke(cli.app, ["host", "deploy-key", "pi", "game", "--dry-run"])
    assert dry.exit_code == 0 and "ssh-keygen" in dry.output and not pi.scripts
    done = runner.invoke(
        cli.app, ["host", "deploy-key", "pi", "game", "--repo", "me/r", "--yes"]
    )
    assert done.exit_code == 0 and PUB in done.output and "can read me/r" in done.output
