import logging
import re
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from aqt.archives import TargetConfig
from aqt.exceptions import UpdaterError
from aqt.helper import Settings
from aqt.updater import Updater


@pytest.fixture(autouse=True)
def setup_settings():
    Settings.load_settings()


@pytest.mark.parametrize(
    "target_config, expected_err_pattern",
    (
        (
            TargetConfig("5.15.2", "winrt", "win64_msvc2019_winrt_x86", "windows"),
            re.compile(
                r"Updater caused an IO error: .*No such file or directory: "
                # '.*' wildcard used to match path separators on windows/*nix
                r".*5\.15\.2.*winrt_x86_msvc2019.*bin.*qt.conf.*"
            ),
        ),
        (
            TargetConfig("5.15.2", "desktop", "win64_msvc2019_64", "windows"),
            re.compile(
                r"Updater caused an IO error: .*No such file or directory: "
                # '.*' wildcard used to match path separators on windows/*nix
                r".*5\.15\.2.*msvc2019_64.*bin.*qt.conf.*"
            ),
        ),
    ),
)
def test_updater_update_license_io_error(monkeypatch, target_config: TargetConfig, expected_err_pattern: re.Pattern):
    """
    All of these tests will raise IOError when they attempt to patch the license file.
    """
    with pytest.raises(UpdaterError) as err:
        with TemporaryDirectory() as empty_dir:
            # Try to update a Qt installation that does not exist
            Updater.update(target_config, base_path=Path(empty_dir), installed_desktop_arch_dir=None)
    assert err.type == UpdaterError
    err_msg = format(err.value)
    assert expected_err_pattern.match(err_msg)


def test_patch_target_qt_conf_run_twice_leaves_the_file_as_the_first_run_left_it(tmp_path):
    """A module installed on top of a Qt build patches the same target_qt.conf a second time."""
    prefix = tmp_path / "6.12.0" / "android_arm64_v8a"
    (prefix / "bin").mkdir(parents=True)
    target_qt_conf = prefix / "bin" / "target_qt.conf"
    target_qt_conf.write_text(
        "[Paths]\nPrefix=/home/qt/work/install/target\nHostPrefix=../../\nHostData=target\nHostLibraryExecutables=./bin\n",
        "UTF-8",
    )
    updater = Updater(prefix, logging.getLogger(__name__))

    updater.patch_target_qt_conf(str(tmp_path), "6.12.0", "android_arm64_v8a", "linux", "gcc_arm64")
    after_first_run = target_qt_conf.read_text("UTF-8")
    updater.patch_target_qt_conf(str(tmp_path), "6.12.0", "android_arm64_v8a", "linux", "gcc_arm64")

    assert after_first_run == (
        "[Paths]\n"
        f"Prefix={tmp_path / '6.12.0' / 'android_arm64_v8a' / 'target'}\n"
        "HostPrefix=../../gcc_arm64\n"
        "HostData=../android_arm64_v8a\n"
        "HostLibraryExecutables=./libexec\n"
    )
    assert target_qt_conf.read_text("UTF-8") == after_first_run
