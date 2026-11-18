import pytest
import yaml

from raspi.config.loader import ConfigError, parse_manifest
from raspi.core.manifest import render_manifest
from raspi.core.models import AppRef, Process
from raspi.core.processes import MAIN, all_processes


def parse(entry: dict) -> AppRef:
    entry = {"name": "vocalog", "repo": "me/vocalog", **entry}
    return parse_manifest({"version": 1, "apps": [entry]})[0]


TWO = {
    "processes": [
        {
            "name": "bot",
            "run": "node bot.js",
            "unit": "vocalog.service",
            "on_update": "keep",
        },
        {"name": "server", "run": "node server.js"},
    ]
}


def test_processes_get_default_units_and_rule():
    app = parse(TWO)
    assert [(p.name, p.unit, p.on_update) for p in app.processes] == [
        ("bot", "vocalog.service", "keep"),  # an adopted unit keeps its name
        ("server", "vocalog-server.service", "restart"),
    ]


def test_the_first_process_drives_unit_and_run_so_old_code_keeps_working():
    app = parse(TWO)
    assert (app.unit, app.run) == ("vocalog.service", "node bot.js")


def test_an_app_without_processes_is_one_process_called_main():
    app = parse({"unit": "vocalog.service", "run": "npm start"})
    assert app.processes == ()
    assert all_processes(app) == (Process(MAIN, "vocalog.service", "npm start"),)


def test_the_list_round_trips_and_run_is_written_once():
    app = parse(TWO)
    text = render_manifest((app,))
    entry = yaml.safe_load(text)["apps"][0]
    assert "run" not in entry and entry["unit"] == "vocalog.service"
    assert entry["processes"][1] == {"name": "server", "run": "node server.js"}
    assert entry["processes"][0]["on_update"] == "keep"
    assert parse_manifest(yaml.safe_load(text)) == (app,)


def test_a_plain_app_renders_as_before():
    app = parse({"unit": "vocalog.service", "run": "npm start"})
    entry = yaml.safe_load(render_manifest((app,)))["apps"][0]
    assert entry["run"] == "npm start" and "processes" not in entry


@pytest.mark.parametrize(
    ("processes", "message"),
    [
        ([{"name": "Bot", "run": "x"}], "process name"),
        ([{"name": "bot"}], "run command of process bot"),
        ([{"name": "bot", "run": "x", "on_update": "never"}], "on_update"),
        (
            [{"name": "a", "run": "x"}, {"name": "a", "run": "y"}],
            "Duplicate process name",
        ),
        (
            [
                {"name": "a", "run": "x", "unit": "z.service"},
                {"name": "b", "run": "y", "unit": "z.service"},
            ],
            "Duplicate process unit",
        ),
        ([{"name": "a", "run": "x", "unit": "z"}], "process unit"),
        ([{"name": f"p{i}", "run": "x"} for i in range(9)], "At most 8"),
        (["bot"], "processes entries"),
    ],
)
def test_bad_processes_are_refused_with_a_reason(processes, message):
    with pytest.raises(ConfigError, match=message):
        parse({"processes": processes})
