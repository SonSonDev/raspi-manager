"""“Install unit…” writes one unit per process of an app."""

import base64
import re

import pytest

from raspi.core.models import AppRef, EnvFile, Host, Process
from raspi.core.unit import MARKER, UnitError, build_probe_script, parse_probe
from raspi.services.unit import install_unit, plan_unit
from raspi.ssh.transport import RunResult

APP = AppRef(
    "vocalog",
    "vocalog.service",
    repo="me/vocalog",
    run="node bot.js",
    env_files=(EnvFile("default", "/home/pi/apps/vocalog/.env"),),
    processes=(
        Process("bot", "vocalog.service", "node bot.js", "keep"),
        Process("server", "vocalog-server.service", "node server.js"),
    ),
)
HOST = Host("pi", "10.0.0.1", "pi", (APP,))


class Pi:
    def __init__(self, states=("ours", "missing")):
        keys = ["unit"] + [f"unit{i}" for i in range(1, len(states))]
        facts = "".join(f"{k}={s}\n" for k, s in zip(keys, states, strict=True))
        self.facts = f"home=/home/pi\nsudo=yes\n{facts}envfile0=yes\nnodebin=/n/bin\n"
        self.scripts: list[str] = []

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        if 'echo "sudo=' in script:
            return RunResult(True, self.facts)
        return RunResult(True, "ok\n")


def written(script: str) -> dict[str, str]:
    pairs = re.findall(
        r"printf %s (\S+) \| base64 -d \| sudo tee (\S+) >/dev/null", script
    )
    return {
        path.rsplit("/", 1)[1]: base64.b64decode(b64).decode() for b64, path in pairs
    }


def test_probe_reads_one_state_per_process_and_stays_read_only():
    script = build_probe_script(APP)
    assert "echo unit=" in script and "echo unit1=" in script
    assert "sudo tee" not in script and "systemctl" not in script
    facts = parse_probe("unit=ours\nunit1=foreign\n", 0, 2)
    assert (facts.unit, facts.units) == ("ours", ("ours", "foreign"))


def test_one_unit_per_process_with_the_same_user_folder_and_env_files():
    plan = plan_unit(HOST, Pi(), APP)
    files = written(plan.script)
    assert set(files) == {"vocalog.service", "vocalog-server.service"}
    assert 'exec node bot.js"' in files["vocalog.service"]
    assert 'exec node server.js"' in files["vocalog-server.service"]
    for text in files.values():
        assert text.startswith(MARKER)
        assert "User=pi" in text and "WorkingDirectory=/home/pi/apps/vocalog" in text
        assert "EnvironmentFile=/home/pi/apps/vocalog/.env" in text
    assert (
        "Description=vocalog server (raspi-manager)" in files["vocalog-server.service"]
    )


def test_one_reload_every_unit_enabled_and_nothing_started_or_restarted():
    script = plan_unit(HOST, Pi(), APP).script
    assert script.count("daemon-reload") == 1
    assert "systemctl enable vocalog.service" in script
    assert "systemctl enable vocalog-server.service" in script
    assert "systemctl start" not in script and "restart" not in script
    assert script.index("daemon-reload") > script.index("vocalog-server.service")


def test_the_plan_says_which_unit_is_replaced_and_which_is_created():
    plan = plan_unit(HOST, Pi(), APP)
    assert plan.existing
    assert plan.steps[0].startswith("Replace /etc/systemd/system/vocalog.service")
    assert plan.steps[1].startswith("Create /etc/systemd/system/vocalog-server.service")
    assert "none is started or restarted" in plan.steps[-1]
    assert "vocalog-server.service" in plan.text and "vocalog.service" in plan.text


def test_a_foreign_unit_of_any_process_stops_the_whole_plan():
    with pytest.raises(UnitError, match="vocalog-server.service exists"):
        plan_unit(HOST, Pi(("ours", "foreign")), APP)


def test_every_process_needs_a_run_command():
    bad = AppRef(
        "vocalog", "vocalog.service", run="x", processes=(Process("a", "a.service"),)
    )
    with pytest.raises(UnitError, match="a of vocalog has no run command"):
        plan_unit(HOST, Pi(("missing",)), bad)


def test_install_runs_the_one_script_once():
    pi = Pi()
    plan = plan_unit(HOST, pi, APP)
    result = install_unit(HOST, pi, plan)
    assert result.ok and pi.scripts.count(plan.script) == 1
