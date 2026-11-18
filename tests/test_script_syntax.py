"""Every shell script raspi-manager sends to a Pi must at least parse, and stay POSIX.

The Pi runs them with `sh` (dash on Raspberry Pi OS). A syntax error or a bashism would only show up
on the real Pi, in the middle of a change, so each generated script is checked here with `sh -n`
(nothing is executed).
"""

import re
import shutil
import subprocess

import pytest

from raspi.core import (
    connector,
    deploykey,
    deploykey_manage,
    install,
    move,
    provision,
    remote,
    remove,
    runtime,
    scan,
    tools,
    unit,
    update,
)
from raspi.core.models import AppRef, EnvFile, Requirement

APP = AppRef(
    "notes-app",
    "notes-app.service",
    path="/home/pi/apps/notes-app",
    env_files=(EnvFile("default", "/home/pi/apps/notes-app/.env"),),
    repo="SonSonDev/notes-app",
    requires=(
        Requirement("mount", "/mnt/data"),
        Requirement("file", "/mnt/data/.marker"),
        Requirement("folder", "/mnt/data/db"),
    ),
    build="npm ci && npm run build",
    run="npm start",
)
APP_NO_PATH = AppRef("game", "game.service", repo="me/game", build="make", run="./run")
DOWNLOAD = connector.Download("2026.11.0", "cloudflared-linux-arm64", "a" * 64)
KEYS = ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMq", "ecdsa-sha2-nistp256 AAAAE2Vj/+="]

SCRIPTS = {
    "status": remote.build_status_script((APP, APP_NO_PATH)),
    "action": remote.build_action_script("a.service", "restart"),
    "logs": remote.build_logs_script("a.service", 50),
    "env read": remote.build_env_read_script("/home/pi/app/.env"),
    "manifest read": remote.build_manifest_read_script(),
    "manifest push": remote.build_manifest_push_script("version: 1\napps: []\n"),
    "env detect": remote.build_env_detect_script("/home/pi/app"),
    "env detect default dir": remote.build_env_detect_script("$HOME/apps/notes-app"),
    "env template read": remote.build_template_read_script("/home/pi/app/.env"),
    "env push": remote.build_env_push_script("/home/pi/app/.env", "A=1\n"),
    "scan": scan.build_scan_script(),
    "move probe": move.build_probe_script(APP),
    "move pack": move.build_pack_script("/mnt/data/db"),
    "move unpack": move.build_unpack_script("/mnt/data/db"),
    "move manifest": move.build_manifest_script("/mnt/data/db"),
    "move cleanup": move.build_cleanup_script("/mnt/data/db"),
    "move disable": move.build_disable_script("a.service"),
    "key list": deploykey_manage.build_list_script(),
    "key rotate": deploykey_manage.build_rotate_script("game", "pi", KEYS),
    "key delete": deploykey_manage.build_delete_script("game"),
    "doctor": provision.build_check_script(),
    "provision": provision.build_provision_script(["git", "curl"], True),
    "provision dir only": provision.build_provision_script([], True),
    "node with nvm": runtime.build_node_script("22", True),
    "node without nvm": runtime.build_node_script("22", False),
    "deploy key": deploykey.build_key_script("notes-app", "raspi-v", KEYS),
    "deploy key verify": deploykey.build_verify_script("notes-app", "me/repo"),
    "install probe": install.build_probe_script(APP),
    "install probe default dir": install.build_probe_script(APP_NO_PATH),
    "clone and build": install.build_install_script(APP, "missing"),
    "build existing": install.build_install_script(APP_NO_PATH, "repo"),
    "unit probe": unit.build_probe_script(APP),
    "tools probe": tools.build_probe_script(),
    "tools ffmpeg and file": tools.build_tools_script(
        True,
        tools.YtDlpDownload("2026.10.26", "yt-dlp_linux_aarch64", "file", "a" * 64),
        [],
    ),
    "tools bundle": tools.build_tools_script(
        False,
        tools.YtDlpDownload(
            "2026.10.26", "yt-dlp_linux_armv7l.zip", "bundle", "a" * 64
        ),
        ["unzip"],
    ),
    "tools ffmpeg only": tools.build_tools_script(True, None, []),
    "remove probe": remove.build_probe_script(APP),
    "remove everything": remove.build_remove_script(
        APP, set(remove.ORDER), remove.RemoveFacts(env_present=(True,))
    ),
    "update probe": update.build_probe_script(APP),
    "update probe ref": update.build_probe_script(APP, ref="v1.2/x"),
    "update probe sha": update.build_probe_script(APP, sha="abc1234"),
    "update": update.build_update_script(APP, "b" * 40),
    "unit install": unit.build_install_script(
        APP, unit.render_unit(APP, "pi", "/home/pi/apps/notes-app", "/n/bin")
    ),
    "units install (several)": unit.build_units_script(
        (("a.service", "[Unit]\n"), ("a-web.service", "[Unit]\n"))
    ),
    "connector check": connector.build_check_script("raspi-tunnel-x.service"),
    "connector install": connector.build_install_script(
        "raspi-tunnel-x.service", "[Unit]\n", "tok", DOWNLOAD
    ),
    "connector install, no download": connector.build_install_script(
        "raspi-tunnel-x.service", "[Unit]\n", "tok", None
    ),
    "ports": connector.build_ports_script("a.service"),
}
BASHISMS = {
    "[[": re.compile(r"\[\["),
    "here-string": re.compile(r"<<<"),
    "function keyword": re.compile(r"^\s*function\s", re.MULTILINE),
    "$'...'": re.compile(r"\$'"),
    "process substitution": re.compile(r"<\("),
    "[ a == b ]": re.compile(r"\[ [^\]]*==[^\]]*\]"),
}
SH = shutil.which("dash") or shutil.which("sh")


@pytest.mark.skipif(SH is None, reason="no local sh")
@pytest.mark.parametrize("name", SCRIPTS)
def test_script_parses(name):
    result = subprocess.run(
        [SH, "-n"], input=SCRIPTS[name].encode(), capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr.decode()


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_has_no_bashism(name):
    found = [
        label for label, pattern in BASHISMS.items() if pattern.search(SCRIPTS[name])
    ]
    assert not found, f"{name}: {found}"
