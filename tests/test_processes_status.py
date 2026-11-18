import subprocess

from raspi.core.models import AppRef, EnvFile, Process
from raspi.core.remote import _unit_read_lines, build_status_script, parse_status
from raspi.core.stage import READY, next_step
from raspi.core.unit import render_unit, unit_drift

BOT = Process("bot", "vocalog.service", "node bot.js", "keep")
SERVER = Process("server", "vocalog-server.service", "node server.js")
APP = AppRef(
    "vocalog",
    "vocalog.service",
    repo="me/vocalog",
    run="node bot.js",
    built=None,
    env_files=(EnvFile("default", "/home/pi/apps/vocalog/.env"),),
    processes=(BOT, SERVER),
)
UNIT = "/etc/systemd/system/{}"

SHOW = "LoadState=loaded\nActiveState={}\nSubState=x\nMainPID=5\nNRestarts=2\n"
REPO = "key=yes\nenvfile0=yes\nrev=abc1234\ndirty=0\n"


def output(bot="active", server="active", extra="") -> str:
    return (
        f"@@APP vocalog\n{SHOW.format(bot)}{REPO}"
        f"@@PROC bot\n{SHOW.format(bot)}"
        f"@@PROC server\n{SHOW.format(server)}{extra}"
    )


def test_status_script_reads_each_process_and_only_reads():
    script = build_status_script((APP,))
    assert "systemctl show vocalog-server.service" in script
    assert script.count("echo '@@PROC ") == 2 or script.count("@@PROC") == 2
    for forbidden in ("sudo", "systemctl start", "restart", "tee", "rm "):
        assert forbidden not in script


def test_app_lines_come_before_the_process_blocks():
    script = build_status_script((APP,))
    assert script.index("rev-parse") < script.index("@@PROC")
    status = parse_status(output(), (APP,))[0]
    assert status.rev == "abc1234" and status.key_present is True
    assert status.env_present == (True,)


def test_each_process_gets_its_own_state():
    status = parse_status(output(bot="active", server="inactive"), (APP,))[0]
    assert [(p.name, p.state) for p in status.processes] == [
        ("bot", "running"),
        ("server", "stopped"),
    ]
    assert status.state == "running"  # the app's own fields stay the first unit's


def test_an_app_without_processes_has_none():
    plain = AppRef("vocalog", "vocalog.service", run="x")
    status = parse_status("@@APP vocalog\n" + SHOW.format("active"), (plain,))[0]
    assert status.processes == ()


def run_reader(app: AppRef, unit: str, text: str, tmp_path):
    path = tmp_path / unit
    path.write_text(text, encoding="utf-8", newline="\n")
    script = "\n".join(_unit_read_lines(app, unit)).replace(
        UNIT.format(unit), path.as_posix()
    )
    out = subprocess.run(
        ["sh", "-c", script], capture_output=True, text=True, check=True
    ).stdout
    return out.replace("home=" + out.split("home=")[1].split("\n")[0], "home=/home/pi")


def unit_text(process: Process, run: str | None = None) -> str:
    other = Process(process.name, process.unit, run or process.run)
    return render_unit(APP, "pi", "/home/pi/apps/vocalog", "/n/bin", other)


def read_both(tmp_path, server_run=None) -> object:
    blocks = (
        "@@APP vocalog\n"
        + SHOW.format("active")
        + run_reader(APP, BOT.unit, unit_text(BOT), tmp_path)
        + REPO
        + "@@PROC bot\n"
        + SHOW.format("active")
        + run_reader(APP, BOT.unit, unit_text(BOT), tmp_path)
        + "@@PROC server\n"
        + SHOW.format("active")
        + run_reader(APP, SERVER.unit, unit_text(SERVER, server_run), tmp_path)
    )
    return parse_status(blocks, (APP,))[0]


def test_drift_is_judged_per_process_and_names_the_process(tmp_path):
    assert unit_drift(APP, read_both(tmp_path)) == []
    drift = unit_drift(APP, read_both(tmp_path, server_run="node old.js"))
    assert drift == ["run command (server)"]


def test_next_step_names_the_process_with_a_stale_unit(tmp_path):
    step = next_step(APP, read_both(tmp_path, server_run="node old.js"))
    assert step.label == "Unit out of date (Install unit…)"
    assert "run command (server)" in step.why and "node" not in step.why


def test_next_step_when_a_process_has_no_unit_or_is_not_running():
    missing = "LoadState=not-found\nActiveState=inactive\n"
    status = parse_status(
        f"@@APP vocalog\n{SHOW.format('active')}{REPO}"
        f"@@PROC bot\n{SHOW.format('active')}@@PROC server\n{missing}",
        (APP,),
    )[0]
    step = next_step(APP, status)
    assert step.label == "Install unit…" and "server" in step.why
    idle = parse_status(output(server="inactive"), (APP,))[0]
    step = next_step(APP, idle)
    assert step.label == "Start" and step.why == "Not running: server"
    assert next_step(APP, parse_status(output(), (APP,))[0]).label == READY
