import re
import sys

import pytest

import aqt


@pytest.mark.enable_socket
@pytest.mark.remote_data
def test_cli_unknown_version(capsys):
    wrong_version = "5.16.0"
    cli = aqt.installer.Cli()
    assert cli.run(["install-qt", "mac", "desktop", wrong_version]) == 1
    out, err = capsys.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)
    assert not out

    """
    Expected result when no redirect occurs:

    INFO    : aqtinstall(aqt) v.* on Python 3.*
    ERROR   : Failed to locate XML data for Qt version '5.16.0'.
    ==============================Suggested follow-up:==============================
    * Please use 'aqt list-qt mac desktop' to show versions available.

    Expected result when redirect occurs:

    INFO    : aqtinstall(aqt) v.* on Python 3.*
    WARNING : Connection to 'https://download.qt.io' failed. Retrying with fallback '.*'.
    ERROR   : Failed to locate XML data for Qt version '5.16.0'.
    """

    matcher = re.compile(
        r"[^\n]*aqtinstall\(aqt\) v.* on Python 3.*\n"
        r"(?:WARNING : Connection to 'https://download.qt.io' failed. Retrying with fallback '.*'.\n)?"
        rf".*Failed to locate XML data for Qt version '{re.escape(wrong_version)}'\.\n"
        r"==============================Suggested follow-up:==============================\n"
        r"\* Please use 'aqt list-qt mac desktop' to show versions available\.\n",
    )

    assert matcher.match(err)
