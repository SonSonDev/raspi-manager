import pytest

from raspi.core.models import Host
from raspi.core.runtime import NVM_SHA256, NVM_TAG, RuntimeError_, build_node_script
from raspi.services.runtime import install_node, plan_node
from raspi.ssh.transport import RunResult

FIT = (
    "os=Debian\nmachine=aarch64\nsudo=yes\ngit=git version 2.47\ncurl=curl 8\n"
    "uv=\nnode=\ncloudflared=\napps_dir=yes\ndisk_mb=9000\nmem_mb=900\n"
)
HOST = Host("pi", "10.0.0.1", "pi")


class Pi:
    def __init__(self, check: str = FIT, nvm: str = "no"):
        self.check, self.nvm, self.scripts = check, nvm, []

    def run_script(self, host, script, timeout=20.0):
        self.scripts.append(script)
        if "uname -m" in script:
            return RunResult(True, self.check)
        if "nvm.sh" in script and "nvm install" not in script:
            return RunResult(True, self.nvm + "\n")
        return RunResult(
            True, "node=/home/pi/.nvm/versions/node/v22.1.0/bin/node\n"
        )


def test_installer_is_checked_before_it_runs():
    script = build_node_script("22", have_nvm=False)
    assert NVM_TAG in script and NVM_SHA256 in script
    assert script.index("sha256sum -c") < script.index('bash "$tmp/nvm-install.sh"')
    assert "nvm install 22" in script and "sudo" not in script


def test_no_download_when_nvm_exists():
    assert "curl" not in build_node_script("22", have_nvm=True)


@pytest.mark.parametrize("bad", ["22; rm -rf /", "lts", "", "v22"])
def test_version_must_be_a_plain_major_number(bad):
    with pytest.raises(RuntimeError_):
        build_node_script(bad, True)


def test_needs_git_and_curl_first():
    bare = FIT.replace("git=git version 2.47", "git=")
    with pytest.raises(RuntimeError_, match="provision"):
        plan_node(HOST, Pi(bare), "22")


def test_dry_run_changes_nothing_then_real_install():
    pi = Pi()
    ok, _, script = install_node(HOST, pi, "22", dry_run=True)
    assert ok and "nvm install 22" in script
    assert not any("nvm install" in s for s in pi.scripts)
    ok, message, _ = install_node(HOST, Pi(nvm="yes"), "22")
    assert ok and "v22.1.0" in message
