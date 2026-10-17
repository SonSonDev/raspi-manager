import tomllib
from pathlib import Path

from typer.testing import CliRunner

import raspi
from raspi.cli.main import app

runner = CliRunner()


def test_command_without_arguments_opens_the_window(monkeypatch):
    opened = []
    monkeypatch.setattr("raspi.gui.app.run", lambda: opened.append(True) or 0)
    result = runner.invoke(app, [])
    assert result.exit_code == 0 and opened == [True]


def test_subcommands_do_not_open_the_window(monkeypatch):
    monkeypatch.setattr(
        "raspi.gui.app.run",
        lambda: (_ for _ in ()).throw(AssertionError("window opened")),
    )
    result = runner.invoke(app, ["--help"])
    assert (
        result.exit_code == 0 and "logs" in result.output and "restart" in result.output
    )


def test_project_name_and_command_aliases():
    data = tomllib.loads(
        (Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert data["project"]["name"] == "raspi-manager"
    assert set(data["project"]["scripts"]) == {"raspi-manager", "raspi", "rpim"}
    assert raspi.APP_NAME == "raspi-manager"
    assert data["tool"]["uv"]["build-backend"]["module-name"] == "raspi"
