import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
import pytest
from PySide6.QtWidgets import QApplication
from typer.testing import CliRunner

from raspi.core.models import Host
from raspi.core.provision import parse_checks
from raspi.core.tools import (
    ToolError,
    YtDlpDownload,
    asset_for,
    build_tools_script,
    check_tag,
    ffmpeg_short,
    parse_sums,
)
from raspi.services.tools import (
    ToolState,
    fetch_release,
    install_tools,
    plan_tools,
    read_state,
)
from raspi.ssh.transport import RunResult

TAG = "2026.10.26"
SHA = "e" * 64
FILE = YtDlpDownload(TAG, "yt-dlp_linux_aarch64", "file", SHA)
BUNDLE = YtDlpDownload(TAG, "yt-dlp_linux_armv7l.zip", "bundle", SHA)
HOST = Host("pi", "10.0.0.1", "pi")
SUMS = f"{SHA}  yt-dlp_linux_aarch64\n{'f' * 64}  yt-dlp_linux_armv7l.zip\n{'a' * 64}  yt-dlp\n"


def probe(machine="aarch64", sudo="yes", curl="yes", unzip="yes", ffmpeg="", ytdlp=""):
    return (
        f"machine={machine}\nsudo={sudo}\ncurl={curl}\nunzip={unzip}\n"
        f"ffmpeg={ffmpeg}\nytdlp={ytdlp}\n"
    )


class Pi:
    def __init__(
        self,
        facts: str | None = None,
        install_ok=True,
        out="ffmpeg=ffmpeg version 7.1.2 Copyright\nyt-dlp=2026.10.26\n",
    ):
        self.facts = facts or probe()
        self.install_ok, self.out, self.scripts = install_ok, out, []

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        if "uname -m" in script:
            return RunResult(True, self.facts)
        if not self.install_ok:
            return RunResult(False, "", "checksum mismatch: download refused")
        return RunResult(True, self.out)


def lookup(machine):
    return {"aarch64": FILE, "armv7l": BUNDLE}[machine]


# --- the pure part --------------------------------------------------------------------------
def test_each_architecture_has_its_official_file_and_unknown_ones_are_refused():
    assert asset_for("aarch64") == ("yt-dlp_linux_aarch64", "file")
    assert asset_for("x86_64") == ("yt-dlp_linux", "file")
    assert asset_for("armv7l") == ("yt-dlp_linux_armv7l.zip", "bundle")
    with pytest.raises(ToolError):
        asset_for("riscv64")


def test_checksum_is_taken_from_the_published_sums_for_exactly_that_file():
    assert parse_sums(SUMS, "yt-dlp_linux_aarch64") == SHA
    assert parse_sums(SUMS, "yt-dlp") == "a" * 64  # not confused with yt-dlp_linux_...
    assert parse_sums(SUMS, "yt-dlp_linux") is None
    assert check_tag(TAG) == TAG
    with pytest.raises(ToolError):
        check_tag("latest; rm -rf /")


def test_the_download_is_checked_then_run_before_it_replaces_anything():
    script = build_tools_script(False, FILE, [])
    assert (
        script.index("sha256sum -c")
        < script.index("--version >/dev/null")
        < script.index("sudo install")
    )
    assert "nothing was installed" in script and "exit 4" in script
    assert "/usr/local/bin/yt-dlp" in script and SHA in script
    bundle = build_tools_script(False, BUNDLE, ["unzip"])
    assert (
        bundle.index("sha256sum -c")
        < bundle.index("--version >/dev/null")
        < bundle.index("sudo rm -rf /opt/yt-dlp\n")
    )
    assert "ln -sf /opt/yt-dlp/yt-dlp_linux_armv7l /usr/local/bin/yt-dlp" in bundle
    assert "chown -R root:root" in bundle


def test_only_whitelisted_apt_packages_and_nothing_to_do_means_no_script_changes():
    ffmpeg_only = build_tools_script(True, None, [])
    assert "apt-get install -y ffmpeg" in ffmpeg_only and "curl" not in ffmpeg_only
    with pytest.raises(ToolError):
        build_tools_script(False, None, ["ffmpeg; rm -rf /"])


def test_ffmpeg_version_is_shortened():
    assert (
        ffmpeg_short("ffmpeg version 7.1.2-0+deb13u1 Copyright (c) 2000")
        == "7.1.2-0+deb13u1"
    )
    assert ffmpeg_short("") == ""


def test_the_doctor_lists_both_tools_as_optional():
    out = "machine=aarch64\nsudo=yes\nffmpeg=ffmpeg version 4.3.9 Copyright\nytdlp=\n"
    checks = {c.key: c for c in parse_checks(out)}
    assert (
        checks["ffmpeg"].ok
        and checks["ffmpeg"].detail == "4.3.9"
        and not checks["ffmpeg"].required
    )
    assert not checks["ytdlp"].ok and "Install tools" in checks["ytdlp"].fix


# --- finding the release -------------------------------------------------------------------------
def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_release_lookup_reads_the_tag_and_the_checksum_of_the_right_file():
    def handler(request):
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(200, json={"tag_name": TAG})
        assert request.url.path.endswith(f"/{TAG}/SHA2-256SUMS")
        return httpx.Response(200, text=SUMS)

    assert fetch_release("aarch64", client(handler)) == FILE


@pytest.mark.parametrize("failure", ["no sums", "network", "bad tag"])
def test_release_lookup_failures_are_explained(failure):
    def handler(request):
        if failure == "network":
            raise httpx.ConnectError("offline")
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(
                200, json={"tag_name": "nightly" if failure == "bad tag" else TAG}
            )
        return httpx.Response(200, text="" if failure == "no sums" else SUMS)

    with pytest.raises(ToolError):
        fetch_release("aarch64", client(handler))


# --- planning and installing ---------------------------------------------------------------------------
def test_plan_for_a_bare_pi_installs_both_and_changes_nothing_yet():
    pi = Pi()
    plan = plan_tools(HOST, pi, {"ffmpeg", "yt-dlp"}, lookup)
    assert plan.steps[0].startswith("Install with apt: ffmpeg")
    assert "yt-dlp_linux_aarch64" in plan.steps[1]
    assert (
        "apt-get install -y ffmpeg" in plan.script and len(pi.scripts) == 1
    )  # only the probe ran


def test_what_is_already_there_is_left_alone_and_said():
    pi = Pi(probe(ffmpeg="ffmpeg version 4.3.9 Copyright", ytdlp=TAG))
    plan = plan_tools(HOST, pi, {"ffmpeg", "yt-dlp"}, lookup)
    assert plan.nothing_to_do and any("already the latest" in n for n in plan.notes)
    assert any("ffmpeg is already installed" in n for n in plan.notes)


def test_an_older_yt_dlp_is_updated_and_the_32_bit_pi_gets_the_bundle_and_unzip():
    old = Pi(probe(machine="armv7l", unzip="no", ffmpeg="x", ytdlp="2025.04.30"))
    plan = plan_tools(HOST, old, {"yt-dlp"}, lookup)
    assert "Update yt-dlp 2025.04.30 to" in plan.steps[-1]
    assert "unzip" in plan.script and "ln -sf /opt/yt-dlp" in plan.script


@pytest.mark.parametrize(
    ("facts", "message"),
    [
        (probe(sudo="no"), "sudo"),
        (probe(curl="no"), "curl"),
        (probe(machine="riscv64"), "No official"),
    ],
)
def test_the_plan_refuses_with_the_reason(facts, message):
    def strict(machine):
        return asset_and_build(machine)

    def asset_and_build(machine):
        asset_for(machine)
        return FILE

    with pytest.raises(ToolError, match=message):
        plan_tools(HOST, Pi(facts), {"ffmpeg", "yt-dlp"}, strict)


def test_an_unknown_tool_is_refused():
    with pytest.raises(ToolError):
        plan_tools(HOST, Pi(), {"ffmpeg", "vlc"}, lookup)


def test_install_reports_what_was_installed_and_failures_plainly():
    pi = Pi()
    plan = plan_tools(HOST, pi, {"ffmpeg", "yt-dlp"}, lookup)
    result = install_tools(HOST, pi, plan)
    assert result.ok and result.messages == [
        "ffmpeg 7.1.2 installed on pi",
        f"yt-dlp {TAG} installed on pi",
    ]
    bad = Pi(install_ok=False)
    failed = install_tools(HOST, bad, plan)
    assert not failed.ok and "checksum mismatch" in failed.messages[0]


def test_the_picker_state_says_what_is_missing_or_old():
    state = read_state(
        HOST,
        Pi(probe(ffmpeg="ffmpeg version 4.3.9 Copyright", ytdlp="2025.04.30")),
        lookup,
    )
    assert state.needed() == {"yt-dlp"}
    assert (
        "newer available" in state.lines()["yt-dlp"]
        and "4.3.9" in state.lines()["ffmpeg"]
    )

    def offline(machine):
        raise ToolError("offline")

    unknown = read_state(HOST, Pi(probe(ytdlp="2025.04.30")), offline)
    assert unknown.latest == "" and unknown.needed() == {"ffmpeg"}


# --- the CLI and the window ----------------------------------------------------------------------------------
def test_cli_dry_run_shows_the_script_and_runs_nothing(monkeypatch):
    from raspi.cli import main as cli

    pi = Pi()
    monkeypatch.setattr(cli, "_live_host", lambda name: (HOST, HOST))
    monkeypatch.setattr("raspi.cli.tools.SshTransport", lambda: pi)
    monkeypatch.setattr("raspi.services.tools.fetch_release", lookup)
    monkeypatch.setattr(
        "raspi.cli.tools.plan_tools",
        lambda host, t, wanted: plan_tools(host, t, wanted, lookup),
    )
    out = CliRunner().invoke(cli.app, ["host", "install-tools", "pi", "--dry-run"])
    assert out.exit_code == 0 and "sha256sum -c" in out.output and len(pi.scripts) == 1


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_the_picker_ticks_what_is_needed_and_the_window_offers_it(qapp):
    from raspi.gui.main_window import MainWindow
    from raspi.gui.tools_dialog import ToolsDialog

    state = ToolState(
        __import__("raspi.core.tools", fromlist=["x"]).parse_probe(
            probe(ffmpeg="x", ytdlp="2025.04.30")
        ),
        TAG,
    )
    dialog = ToolsDialog("pi", state)
    assert dialog.chosen() == {"yt-dlp"}
    dialog._boxes["yt-dlp"].setChecked(False)
    assert not dialog.ok_button.isEnabled()
    assert hasattr(MainWindow, "install_tools_form")
