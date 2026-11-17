import pytest
from typer.testing import CliRunner

from raspi.core.models import AppRef, EnvFile, Host, Requirement
from raspi.core.move import MoveError, build_probe_script, parse_probe
from raspi.core.remote import ENV_EXISTS
from raspi.services.move import apply_move, plan_move
from raspi.ssh.transport import ByteResult, RunResult

REV = "a" * 40
SECRET = "TOKEN=hunter2-never-shown"
APP = AppRef(
    "journal",
    "journal.service",
    path="/home/pi/apps/journal",
    repo="me/journal",
    env_files=(EnvFile("default", "/home/pi/apps/journal/.env"),),
    requires=(Requirement("mount", "/mnt/data"), Requirement("folder", "/mnt/data/db")),
)
SRC = Host("old", "10.0.0.1", "pi", (APP,))
DST = Host("new", "10.0.0.2", "pi", (APP,))


class Pis:
    """Two simulated Pis: each has a unit state, a data folder and an env file."""

    def __init__(self, **over):
        self.state = {"old": "active", "new": "inactive"}
        self.rev = {"old": REV, "new": REV}
        self.arch = {"old": "aarch64", "new": "aarch64"}
        self.node = {"old": "v22.1.0", "new": "v22.9.0"}
        self.folder = {"old": "data", "new": "empty"}
        self.files = {"old": {"a.db": "1", "b.wal": "2"}, "new": {}}
        self.env = {"old": SECRET + "\n", "new": None}
        self.fail_unpack = self.corrupt = self.fail_start = self.fail_stop = False
        self.log = []
        self.__dict__.update(over)

    def run_script(self, host, script, timeout=20.0):
        n = host.name
        if 'echo "arch=' in script:
            lines = [
                f"arch={self.arch[n]}",
                f"node={self.node[n]}",
                "sudo=yes",
                "disk_mb=9000",
                f"rev={self.rev[n]}",
                f"state={self.state[n]}",
                "size0=4096",
                f"folder0={self.folder[n]}",
                "mount0=yes",
                "envdir0=yes",
                f"env0={'yes' if self.env[n] is not None else 'no'}",
            ]
            return RunResult(True, "\n".join(lines) + "\n")
        if "systemctl stop" in script or "systemctl start" in script:
            action = "stop" if "systemctl stop" in script else "start"
            self.log.append(f"{action} {n}")
            if action == "stop" and self.fail_stop:
                return RunResult(True, "exit=1\nActiveState=active\n")
            if action == "start" and n == "new" and self.fail_start:
                return RunResult(True, "exit=1\nActiveState=failed\n")
            self.state[n] = "active" if action == "start" else "inactive"
            return RunResult(True, f"exit=0\nActiveState={self.state[n]}\n")
        if "sha256sum" in script:
            listing = "".join(f"{v}  ./{k}\n" for k, v in sorted(self.files[n].items()))
            return RunResult(True, listing)
        if "-delete" in script:
            self.log.append(f"cleanup {n}")
            self.files[n] = {}
            return RunResult(True, "")
        if "systemctl disable" in script:
            self.log.append(f"disable {n}")
            return RunResult(True, "")
        if ENV_EXISTS in script and "cat" in script:
            return RunResult(True, f"{ENV_EXISTS}\n{self.env[n]}")
        if "base64 -d" in script:
            self.log.append(f"env {n}")
            self.env[n] = "pushed"
            return RunResult(True, "written: 1 lines\n")
        return RunResult(False, "", "unexpected script")

    def run_bytes(self, host, script, data=b"", timeout=600.0):
        n = host.name
        if "-cf -" in script:
            return ByteResult(True, b"TAR")
        self.log.append(f"unpack {n}")
        if self.fail_unpack:
            return ByteResult(False, b"", "No space left")
        self.files[n] = {
            k: ("X" if self.corrupt else v) for k, v in self.files["old"].items()
        }
        return ByteResult(True, b"")


def test_probe_script_is_read_only_and_parses():
    script = build_probe_script(APP)
    for word in ("rm ", "mkdir", ">>", "systemctl start", "systemctl stop"):
        assert word not in script
    facts = parse_probe(
        "arch=x\nstate=active\nsize0=7\nfolder0=data\nmount0=yes\n", APP
    )
    assert (
        facts.sizes == (7,) and facts.folders == ("data",) and facts.mounts == (True,)
    )


def test_plan_lists_steps_and_never_shows_the_secret():
    plan = plan_move(SRC, DST, "journal", Pis())
    text = "\n".join(plan.steps) + plan.scripts
    assert any("Stop journal.service on old" in s for s in plan.steps)
    assert "Compare SHA-256" in text and "hunter2" not in text


@pytest.mark.parametrize(
    "over, word",
    [
        ({"state": {"old": "active", "new": "active"}}, "already running"),
        ({"state": {"old": "active", "new": "missing"}}, "no systemd unit"),
        ({"rev": {"old": REV, "new": "b" * 40}}, "same revision"),
        ({"arch": {"old": "aarch64", "new": "armv7l"}}, "architecture"),
        ({"node": {"old": "v22.1.0", "new": "v24.0.0"}}, "Node differs"),
        ({"folder": {"old": "data", "new": "data"}}, "never overwritten"),
        ({"folder": {"old": "data", "new": "missing-parent"}}, "parent"),
    ],
)
def test_plan_refuses(over, word):
    with pytest.raises(MoveError, match=word):
        plan_move(SRC, DST, "journal", Pis(**over))


def test_plan_refuses_same_pi_unknown_app_and_unset_target():
    with pytest.raises(MoveError):
        plan_move(SRC, SRC, "journal", Pis())
    with pytest.raises(MoveError, match="Unknown app"):
        plan_move(SRC, DST, "nope", Pis())
    with pytest.raises(MoveError, match="not in the inventory"):
        plan_move(SRC, Host("new", "x", "pi"), "journal", Pis())


def test_move_success_order_and_source_data_kept():
    pis = Pis()
    result = apply_move(plan_move(SRC, DST, "journal", pis), pis)
    assert result.ok and result.started and "hunter2" not in result.message
    assert pis.log == ["stop old", "unpack new", "env new", "start new", "disable old"]
    assert pis.files["old"] == {"a.db": "1", "b.wal": "2"}
    assert pis.files["new"] == pis.files["old"]
    assert pis.state == {"old": "inactive", "new": "active"}


def test_stopped_source_copies_but_starts_nothing():
    pis = Pis(state={"old": "inactive", "new": "inactive"})
    result = apply_move(plan_move(SRC, DST, "journal", pis), pis)
    assert result.ok and not result.started and "not started" in result.message
    assert "start new" not in pis.log and "stop old" not in pis.log


def test_failed_copy_restarts_the_source_and_cleans():
    pis = Pis(fail_unpack=True)
    result = apply_move(plan_move(SRC, DST, "journal", pis), pis)
    assert not result.ok and "No space left" in result.message
    assert pis.log[-2:] == ["cleanup new", "start old"]
    assert pis.state["old"] == "active" and "start new" not in pis.log


def test_checksum_mismatch_aborts_before_the_target_starts():
    pis = Pis(corrupt=True)
    result = apply_move(plan_move(SRC, DST, "journal", pis), pis)
    assert not result.ok and "differ" in result.message
    assert "start new" not in pis.log and "env new" not in pis.log
    assert pis.state == {"old": "active", "new": "inactive"}
    assert pis.files["new"] == {}


def test_target_that_does_not_start_is_stopped_and_source_restarted():
    pis = Pis(fail_start=True)
    result = apply_move(plan_move(SRC, DST, "journal", pis), pis)
    assert not result.ok and "did not start" in result.message
    assert pis.state == {"old": "active", "new": "inactive"}
    assert "disable old" not in pis.log


def test_failed_stop_aborts_and_restarts_in_doubt():
    pis = Pis(fail_stop=True)
    result = apply_move(plan_move(SRC, DST, "journal", pis), pis)
    assert not result.ok and "Could not stop" in result.message
    assert "unpack new" not in pis.log and pis.log[-1] == "start old"


def test_cli_dry_run_and_move(monkeypatch):
    from raspi.cli import main as cli

    hosts = {"old": SRC, "new": DST}
    monkeypatch.setattr(cli, "_live_host", lambda name: (hosts[name], hosts[name]))
    pis = Pis()
    monkeypatch.setattr("raspi.cli.move.SshTransport", lambda: pis)
    run = CliRunner().invoke
    dry = run(cli.app, ["host", "move-app", "old", "journal", "new", "--dry-run"])
    assert dry.exit_code == 0 and "tar -C" in dry.output and not pis.log
    done = run(cli.app, ["host", "move-app", "old", "journal", "new", "--yes"])
    assert done.exit_code == 0 and "runs on new" in done.output
    assert "hunter2" not in done.output
    pis2 = Pis(state={"old": "active", "new": "active"})
    monkeypatch.setattr("raspi.cli.move.SshTransport", lambda: pis2)
    refused = run(cli.app, ["host", "move-app", "old", "journal", "new", "--yes"])
    assert refused.exit_code == 1 and "already running" in refused.output
