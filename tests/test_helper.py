import binascii
import hashlib
import os
import re
import threading
from pathlib import Path
from typing import Dict
from urllib.parse import urlparse

import pytest
import requests
from requests.models import Response

from aqt import helper
from aqt.exceptions import ArchiveChecksumError, ArchiveConnectionError, ArchiveDownloadError, ChecksumDownloadFailure
from aqt.helper import Settings, get_hash, getUrl, retry_on_errors
from aqt.metadata import Version


@pytest.fixture(autouse=True)
def load_default_settings(use_defaults: bool = True):
    """For each test, first load the default settings file, unless marked otherwise"""
    if use_defaults:
        helper.Settings.load_settings()


@pytest.mark.parametrize(
    "osarch, filename",
    [
        ("windows-x64", "qt-online-installer-windows-x64-online.exe"),
        ("windows-arm64", "qt-online-installer-windows-arm64-online.exe"),
        ("linux-x64", "qt-online-installer-linux-x64-online.run"),
        ("linux-arm64", "qt-online-installer-linux-arm64-online.run"),
        ("mac-x64", "qt-online-installer-macOS-x64-online.dmg"),
        ("mac-arm64", "qt-online-installer-macOS-arm64-online.dmg"),
        ("mac-x64", "qt-online-installer-macOS-universal.dmg"),
        ("mac-arm64", "qt-online-installer-macOS-universal.dmg"),
    ],
)
def test_qt_installer_name(monkeypatch, osarch, filename):
    response = Response()
    response.status_code = 200
    response._content = (
        f'<a href="../">Parent Directory</a><a href="{filename}">{filename}</a><a href="{filename}.mirrorlist">Details</a>'
    ).encode()

    def mock_get(url, timeout):
        assert url == f"{Settings.baseurl}/official_releases/online_installers/"
        assert timeout == Settings.response_timeout
        return response

    monkeypatch.setattr(requests, "get", mock_get)
    monkeypatch.setattr(helper, "get_os_arch", lambda: osarch)
    assert helper.get_qt_installer_name() == filename


@pytest.mark.parametrize(
    "error",
    [requests.exceptions.ConnectionError("Offline"), requests.exceptions.Timeout("Timed out")],
)
def test_qt_installers_connection_error(monkeypatch, error):
    def mock_get(*args, **kwargs):
        raise error

    monkeypatch.setattr(requests, "get", mock_get)
    with pytest.raises(ArchiveConnectionError, match="Failed to fetch Qt installer listing") as exc:
        helper.get_qt_installer_name()
    assert exc.value.__cause__ is error


def test_qt_installers_http_error(monkeypatch):
    response = Response()
    response.status_code = 503
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: response)
    with pytest.raises(ArchiveDownloadError, match="Failed to fetch Qt installer listing"):
        helper.get_qt_installer_name()


@pytest.mark.parametrize("listing", ["", '<a href="windows-x64.exe">windows-x64.exe</a>'])
def test_qt_installer_missing(monkeypatch, listing):
    response = Response()
    response.status_code = 200
    response._content = listing.encode()
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: response)
    monkeypatch.setattr(helper, "get_os_arch", lambda: "mac-arm64")
    with pytest.raises(helper.ArchiveListError, match="No Qt installer found for mac-arm64"):
        helper.get_qt_installer_name()


def test_helper_altlink(monkeypatch):
    class Message:
        headers = {"content-type": "text/plain", "length": 300}
        text = """<?xml version="1.0" encoding="UTF-8"?>
<metalink xmlns="urn:ietf:params:xml:ns:metalink">
  <generator>MirrorBrain/2.17.0</generator>
  <origin dynamic="true">http://download.example.io/boo.7z.meta4</origin>
  <published>2020-03-04T01:11:48Z</published>
  <publisher>
    <name>Example Project</name>
    <url>https://download.example.io</url>
  </publisher>

  <file name="boo.7z">
    <size>651</size>
    <hash type="md5">d49eba3937fb063caa48769e8f28377c</hash>
    <hash type="sha-1">25d3a33d00c1e5880679a17fd4b8b831134cfa6f</hash>
    <hash type="sha-256">37e50248cf061109e2cb92105cd2c36a6e271701d6d4a72c4e73c6d82aad790a</hash>
    <pieces length="262144" type="sha-1">
      <hash>bec628a149ed24a3a9b83747776ecca5a1fad11c</hash>
      <hash>98b1dee3f741de51167a9428b0560cd2d1f4d945</hash>
      <hash>8717a0cb3d14c1958de5981635c9b90b146da165</hash>
      <hash>78cd2ae3ae37ca7c080a56a2b34eb33ec44a9ef1</hash>
    </pieces>
    <url location="cn" priority="1">http://mirrors.geekpie.club/boo.7z</url>
    <url location="jp" priority="2">http://ftp.jaist.ac.jp/pub/boo.7z</url>
    <url location="jp" priority="3">http://ftp.yz.yamagata-u.ac.jp/pub/boo.7z</url>
  </file>
</metalink>
"""

    def mock_return(url):
        return Message()

    monkeypatch.setattr(helper, "_get_meta", mock_return)

    url = "http://foo.baz/qtproject/boo.7z"
    alt = "http://mirrors.geekpie.club/boo.7z"
    newurl = helper.altlink(url, alt)
    assert newurl.startswith("http://ftp.jaist.ac.jp/")


def test_helper_altlink_black(monkeypatch):
    class Message:
        headers = {"content-type": "text/plain", "length": 300}
        text = """<?xml version="1.0" encoding="UTF-8"?>
<metalink xmlns="urn:ietf:params:xml:ns:metalink">
  <generator>MirrorBrain/2.17.0</generator>
  <origin dynamic="true">http://download.example.io/boo.7z.meta4</origin>
  <published>2020-03-04T01:11:48Z</published>
  <publisher>
    <name>Example Project</name>
    <url>https://download.example.io</url>
  </publisher>

  <file name="boo.7z">
    <size>651</size>
    <hash type="md5">d49eba3937fb063caa48769e8f28377c</hash>
    <hash type="sha-1">25d3a33d00c1e5880679a17fd4b8b831134cfa6f</hash>
    <hash type="sha-256">37e50248cf061109e2cb92105cd2c36a6e271701d6d4a72c4e73c6d82aad790a</hash>
    <pieces length="262144" type="sha-1">
      <hash>bec628a149ed24a3a9b83747776ecca5a1fad11c</hash>
      <hash>98b1dee3f741de51167a9428b0560cd2d1f4d945</hash>
      <hash>8717a0cb3d14c1958de5981635c9b90b146da165</hash>
      <hash>78cd2ae3ae37ca7c080a56a2b34eb33ec44a9ef1</hash>
    </pieces>
    <url location="cn" priority="1">http://mirrors.geekpie.club/boo.7z</url>
  </file>
</metalink>
"""

    def mock_return(url):
        return Message()

    monkeypatch.setattr(helper, "_get_meta", mock_return)

    url = "http://foo.baz/qtproject/boo.7z"
    alt = "http://ftp.yz.yamagata-u.ac.jp/pub/boo.7z"
    newurl = helper.altlink(url, alt)
    assert newurl.startswith("http://ftp.yz.yamagata-u.ac.jp/pub/boo.7z")


@pytest.mark.load_default_settings(False)
def test_settings(tmp_path):
    helper.Settings.load_settings(os.path.join(os.path.dirname(__file__), "data", "settings.ini"))
    assert helper.Settings.concurrency == 3
    assert "http://mirror.example.com" in helper.Settings.blacklist


def test_setup_logging_reads_the_configuration_as_ever_by_default(monkeypatch):
    configured_with = []
    monkeypatch.delenv("AQT_LOG_FILE", raising=False)
    monkeypatch.setattr(helper.logging.config, "fileConfig", configured_with.append)
    helper.setup_logging()
    assert configured_with == [helper.Settings.loggingconf]


@pytest.mark.parametrize("log_file", ["logs/aqt.log", r"C:\Users\me\aqt 100%.log", "it's.log"])
def test_logging_config_names_the_log_file(log_file):
    config = helper._logging_config(log_file)
    # logging.config.fileConfig() evaluates the value as a Python expression
    assert eval(config.get("handler_file", "args")) == (log_file, "a")
    assert config.get("logger_aqt", "handlers") == "console,file"


def custom_logging_config(tmp_path, monkeypatch, handler_file: str) -> None:
    """Makes a logging configuration of the user's own the one in use, as LOG_CFG does"""
    with open(helper.Settings.loggingconf, "r") as f:
        default_config = f.read()
    default_handler = (
        "[handler_file]\nlevel=DEBUG\nclass=logging.FileHandler\nformatter=verbose\nargs=('aqtinstall.log', 'a')\n"
    )
    assert default_handler in default_config
    custom_config = tmp_path / "logging.ini"
    custom_config.write_text(default_config.replace(default_handler, handler_file))
    monkeypatch.setattr(helper.Settings, "loggingconf", str(custom_config))


def test_logging_config_keeps_the_other_arguments_of_the_log_file(monkeypatch, tmp_path):
    custom_logging_config(
        tmp_path,
        monkeypatch,
        "[handler_file]\nlevel=INFO\nclass=logging.FileHandler\nformatter=brief\nargs=('mine.log', 'w', 'utf-8')\n",
    )
    config = helper._logging_config("logs/aqt.log")
    assert eval(config.get("handler_file", "args")) == ("logs/aqt.log", "w", "utf-8")
    assert config.get("handler_file", "level") == "INFO"
    assert config.get("handler_file", "formatter") == "brief"


def test_logging_config_without_a_log_file():
    config = helper._logging_config("")
    assert not config.has_section("handler_file")
    assert config.get("handlers", "keys") == "console"
    assert config.get("logger_aqt", "handlers") == "console"
    assert config.get("logger_root", "handlers") == "console"


@pytest.mark.parametrize("log_file", ["", "logs/aqt.log"])
def test_logging_config_leaves_a_configuration_without_the_handler_alone(monkeypatch, tmp_path, log_file):
    with open(helper.Settings.loggingconf, "r") as f:
        default_config = f.read()
    custom_config = tmp_path / "logging.ini"
    custom_config.write_text(default_config.replace("handler_file", "handler_mine").replace(",file", ",mine"))
    monkeypatch.setattr(helper.Settings, "loggingconf", str(custom_config))
    assert helper._logging_config(log_file) is None


def test_setup_logging_writes_no_log_file_when_asked(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AQT_LOG_FILE", "")
    helper.setup_logging()
    helper.getLogger("aqt.main").info("a line for every handler")
    assert list(tmp_path.iterdir()) == []


def test_setup_logging_writes_the_log_file_where_asked(monkeypatch, tmp_path):
    log_file = tmp_path / "a new folder" / "aqt.log"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AQT_LOG_FILE", str(log_file))
    helper.setup_logging()
    helper.getLogger("aqt.main").info("a line for every handler")
    # Configure again without a log file, which closes the one above
    monkeypatch.setenv("AQT_LOG_FILE", "")
    helper.setup_logging()
    assert "a line for every handler" in log_file.read_text()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a new folder"]


def test_official_installer_temp_folder_is_made_only_when_asked_for(monkeypatch, tmp_path):
    monkeypatch.setattr(helper.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(helper.Settings, "_temp_dir", None)
    # Load the settings as a new process does
    monkeypatch.setattr(helper.Settings, "config", None)
    helper.Settings._initialize()
    helper.Settings.load_settings()
    helper.Settings.qt_installer_cleanup()
    assert list(tmp_path.iterdir()) == []

    temp_path = Path(helper.Settings.qt_installer_temp_path)
    assert temp_path.parent == tmp_path and temp_path.name.startswith("aqt-")
    assert helper.Settings.qt_installer_temp_path == str(temp_path)
    (temp_path / "installer.run").write_text("an installer")
    helper.Settings.qt_installer_cleanup()
    assert temp_path.is_dir() and list(temp_path.iterdir()) == []


@pytest.mark.load_default_settings(False)
def test_official_installer_temp_folder_from_the_settings(monkeypatch, tmp_path):
    configured = tmp_path / "of my own"
    configured.mkdir()
    (configured / "notes.txt").write_text("the user's own file")
    monkeypatch.setattr(helper.Settings, "_temp_dir", None)
    monkeypatch.setattr(helper.Settings, "config", helper.MyConfigParser())
    helper.Settings.config.read_dict({"qtofficial": {"temp_dir": str(configured)}})

    temp_path = Path(helper.Settings.qt_installer_temp_path)
    assert temp_path.parent == configured and temp_path.name.startswith("aqt-")
    (temp_path / "installer.run").write_text("an installer")
    helper.Settings.qt_installer_cleanup()
    assert list(temp_path.iterdir()) == []
    assert (configured / "notes.txt").read_text() == "the user's own file"


def mocked_iter_content(chunk_size):
    with open(os.path.join(os.path.dirname(__file__), "data", "windows-5150-update.xml"), "rb") as f:
        data = f.read(chunk_size)
        while len(data) > 0:
            yield data
            data = f.read(chunk_size)
        return b""


def mocked_requests_get(*args, **kwargs):
    response = Response()
    response.status_code = 200
    response.iter_content = mocked_iter_content
    return response


def test_helper_downloadBinary_md5(tmp_path, monkeypatch):
    monkeypatch.setattr(requests.Session, "get", mocked_requests_get)

    expected = binascii.unhexlify("1d41a93e4a585bb01e4518d4af431933")
    out = tmp_path.joinpath("text.xml")
    helper.downloadBinaryFile("http://example.com/test.xml", out, "md5", expected, 60)


def test_helper_downloadBinary_sha256(tmp_path, monkeypatch):
    monkeypatch.setattr(requests.Session, "get", mocked_requests_get)

    expected = binascii.unhexlify("07b3ef4606b712923a14816b1cfe9649687e617d030fc50f948920d784c0b1cd")
    out = tmp_path.joinpath("text.xml")
    timeout = (Settings.connection_timeout, Settings.response_timeout)
    helper.downloadBinaryFile("http://example.com/test.xml", out, "sha256", expected, timeout)


@pytest.mark.parametrize(
    "mock_exception, expected_err_msg",
    (
        (requests.exceptions.ConnectionError("Connection failed!"), "Connection error: ('Connection failed!',)"),
        (requests.exceptions.Timeout("Connection timed out!"), "Connection timeout: ('Connection timed out!',)"),
    ),
)
def test_helper_downloadBinary_connection_err(tmp_path, monkeypatch, mock_exception, expected_err_msg):
    def _mock_get_conn_error(*args, **kwargs):
        raise mock_exception

    monkeypatch.setattr(requests.Session, "get", _mock_get_conn_error)

    expected = binascii.unhexlify("1d41a93e4a585bb01e4518d4af431933")
    out = tmp_path.joinpath("text.xml")
    with pytest.raises(ArchiveConnectionError) as e:
        helper.downloadBinaryFile("http://example.com/test.xml", out, "md5", expected, 60)
    assert e.type == ArchiveConnectionError
    assert format(e.value) == expected_err_msg


def test_helper_downloadBinary_wrong_checksum(tmp_path, monkeypatch):
    monkeypatch.setattr(requests.Session, "get", mocked_requests_get)

    actual_hash = binascii.unhexlify("1d41a93e4a585bb01e4518d4af431933")
    wrong_hash = binascii.unhexlify("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    out = tmp_path.joinpath("test.xml")
    url = "http://example.com/test.xml"
    expected_err = (
        f"Downloaded file test.xml is corrupted! Detect checksum error."
        f"\nExpect {wrong_hash.hex()}: {url}"
        f"\nActual {actual_hash.hex()}: {out.name}"
    )
    with pytest.raises(ArchiveChecksumError) as e:
        helper.downloadBinaryFile(url, out, "md5", wrong_hash, 60)
    assert e.type == ArchiveChecksumError
    assert format(e.value) == expected_err


def test_helper_downloadBinary_error_page(tmp_path, monkeypatch):
    def mock_requests_get(self, url, *args, **kwargs):
        response = Response()
        response.status_code = 404
        response.reason = "Not Found"
        response.url = url
        response.iter_content = mocked_iter_content
        return response

    monkeypatch.setattr(requests.Session, "get", mock_requests_get)

    expected = binascii.unhexlify("1d41a93e4a585bb01e4518d4af431933")
    out = tmp_path.joinpath("text.xml")
    with pytest.raises(ArchiveConnectionError) as e:
        helper.downloadBinaryFile("http://example.com/test.xml", out, "md5", expected, 60)
    assert format(e.value) == "Failed to download test.xml from example.com\nServer response code: 404, reason: Not Found"
    assert not out.exists()


class MirrorsWithRanges:
    """Stands in for Qt's server, which sends a download on to a mirror, and for the mirrors, which
    hand out any range of bytes of the file. Notes which mirror was asked for which range."""

    QT_URL = "https://download.example.com/qt/archive.7z"
    MIRRORS = [f"https://mirror{number}.example.com/qt/archive.7z" for number in (1, 2, 3)]
    METALINK = """<?xml version="1.0" encoding="UTF-8"?>
<metalink xmlns="urn:ietf:params:xml:ns:metalink">
  <publisher><url>https://download.example.com</url></publisher>
  <file name="archive.7z">
    <url location="de" priority="2">{1}</url>
    <url location="fi" priority="1">{0}</url>
    <url location="us" priority="3">{2}</url>
    <url location="cn" priority="4">http://mirrors.tuna.tsinghua.edu.cn/qt/archive.7z</url>
  </file>
</metalink>""".format(*MIRRORS)

    def __init__(self, monkeypatch, content: bytes, broken_mirrors=(), accept_ranges="bytes", answer_once_asked_by=0):
        self.content = content
        self.broken_mirrors = broken_mirrors
        self.accept_ranges = accept_ranges
        self.asked = []
        # No range is answered before this many connections have asked for one, so that a test
        # can count on all of them taking part, however fast the first one is
        self.connections = set()
        self.connection_arrived = threading.Condition()
        mirrors = self

        class Answer:
            def __init__(self, url: str, status_code: int, headers: Dict[str, str], body: bytes = b""):
                self.url, self.status_code, self.headers, self.body = url, status_code, headers, body
                self.reason = "a reason"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def close(self):
                pass

            def iter_content(self, chunk_size):
                for start in range(0, len(self.body), chunk_size):
                    yield self.body[start : start + chunk_size]

        def mock_get(session, url, **kwargs):
            wanted = kwargs.get("headers", {}).get("Range")
            if url == mirrors.QT_URL:
                return Answer(url, 302, {"Location": mirrors.MIRRORS[0]})
            if wanted is None:
                headers = {"Content-Length": str(len(mirrors.content)), "Accept-Ranges": mirrors.accept_ranges}
                return Answer(url, 200, headers, mirrors.content)
            first, last = (int(number) for number in wanted[len("bytes=") :].split("-"))
            mirrors.asked.append((url, first, last))
            with mirrors.connection_arrived:
                mirrors.connections.add(threading.get_ident())
                mirrors.connection_arrived.notify_all()
                mirrors.connection_arrived.wait_for(lambda: len(mirrors.connections) >= answer_once_asked_by, timeout=5)
            if url in mirrors.broken_mirrors:
                return Answer(url, 503, {})
            return Answer(url, 206, {}, mirrors.content[first : last + 1])

        class Metalink:
            status_code = 200
            headers = {"content-type": "application/metalink4+xml; charset=utf-8"}
            text = mirrors.METALINK

        monkeypatch.setattr(requests.Session, "get", mock_get)
        monkeypatch.setattr(helper, "_get_meta", lambda url: Metalink())
        # A file of some thousand bytes then counts as a large one
        monkeypatch.setattr(helper, "BYTES_PER_CONNECTION", 1000)
        monkeypatch.setattr(helper, "BYTES_PER_RANGE", 300)


def test_helper_downloadBinary_in_ranges_over_mirrors(tmp_path, monkeypatch):
    content = os.urandom(10_000)
    mirrors = MirrorsWithRanges(monkeypatch, content, answer_once_asked_by=8)
    out = tmp_path / "archive.7z"
    helper.downloadBinaryFile(mirrors.QT_URL, out, "sha256", hashlib.sha256(content).digest(), (5, 5))
    assert out.read_bytes() == content
    # Every byte was asked for, in ranges of the set size
    assert {(first, last) for _, first, last in mirrors.asked} == {
        (first, min(first + 300, 10_000) - 1) for first in range(0, 10_000, 300)
    }
    # The eight connections took the three mirrors in turn, and none the blacklisted one
    assert {url for url, _, _ in mirrors.asked} == set(mirrors.MIRRORS)


def test_helper_downloadBinary_in_ranges_passes_a_broken_mirror_by(tmp_path, monkeypatch):
    content = os.urandom(10_000)
    mirrors = MirrorsWithRanges(monkeypatch, content, broken_mirrors=MirrorsWithRanges.MIRRORS[1:2])
    out = tmp_path / "archive.7z"
    helper.downloadBinaryFile(mirrors.QT_URL, out, "sha256", hashlib.sha256(content).digest(), (5, 5))
    assert out.read_bytes() == content


def test_helper_downloadBinary_in_ranges_fails_when_no_mirror_delivers(tmp_path, monkeypatch):
    content = os.urandom(10_000)
    mirrors = MirrorsWithRanges(monkeypatch, content, broken_mirrors=MirrorsWithRanges.MIRRORS)
    with pytest.raises(ArchiveConnectionError) as e:
        helper.downloadBinaryFile(
            mirrors.QT_URL, tmp_path / "archive.7z", "sha256", hashlib.sha256(content).digest(), (5, 5)
        )
    assert "none of the mirrors delivered it" in format(e.value)


def test_helper_downloadBinary_in_ranges_checks_the_checksum(tmp_path, monkeypatch):
    content = os.urandom(10_000)
    mirrors = MirrorsWithRanges(monkeypatch, content)
    with pytest.raises(ArchiveChecksumError):
        helper.downloadBinaryFile(mirrors.QT_URL, tmp_path / "archive.7z", "sha256", bytes(32), (5, 5))


@pytest.mark.parametrize(
    "size, accept_ranges, connections",
    [(10_000, "none", 8), (10_000, "bytes", 1), (1_500, "bytes", 8)],
    ids=["a server without ranges", "one connection allowed", "a small file"],
)
def test_helper_downloadBinary_in_one_piece(tmp_path, monkeypatch, size, accept_ranges, connections):
    content = os.urandom(size)
    mirrors = MirrorsWithRanges(monkeypatch, content, accept_ranges=accept_ranges)
    monkeypatch.setattr(helper.SettingsClass, "max_connections_per_file", connections)
    out = tmp_path / "archive.7z"
    helper.downloadBinaryFile(mirrors.QT_URL, out, "sha256", hashlib.sha256(content).digest(), (5, 5))
    assert out.read_bytes() == content
    assert mirrors.asked == []


def test_helper_downloadBinary_response_error_undefined(tmp_path, monkeypatch):
    contained_error_msg = "This chunk of downloaded content contains an error."

    def iter_broken_content(*args, **kwargs):
        raise RuntimeError(contained_error_msg)

    def mock_requests_get(*args, **kwargs):
        response = Response()
        response.status_code = 200
        response.iter_content = iter_broken_content
        return response

    monkeypatch.setattr(requests.Session, "get", mock_requests_get)

    expected = binascii.unhexlify("1d41a93e4a585bb01e4518d4af431933")
    out = tmp_path.joinpath("text.xml")
    with pytest.raises(ArchiveDownloadError) as e:
        helper.downloadBinaryFile("http://example.com/test.xml", out, "md5", expected, 60)
    assert e.type == ArchiveDownloadError
    assert format(e.value) == f"Download of test.xml has error: {contained_error_msg}"


@pytest.mark.parametrize(
    "num_attempts_before_success, num_retries_allowed",
    (
        (2, 5),
        (5, 5),
        (5, 2),
    ),
)
def test_helper_retry_on_error(num_attempts_before_success, num_retries_allowed):
    enclosed = {"call_count": 0}

    def action():
        enclosed["call_count"] += 1
        more_attempts_needed = num_attempts_before_success - enclosed["call_count"]
        if more_attempts_needed <= 0:
            return True
        raise RuntimeError(f"Must retry {more_attempts_needed} more times before success")

    if num_attempts_before_success > num_retries_allowed:
        with pytest.raises(RuntimeError) as e:
            retry_on_errors(action, (RuntimeError,), num_retries_allowed, "do something")
        assert e.type is RuntimeError
    else:
        assert retry_on_errors(action, (RuntimeError,), num_retries_allowed, "do something")


@pytest.mark.parametrize(
    "num_tries_required, num_retries_allowed",
    (
        (2, 5),
        (5, 5),
        (6, 5),
    ),
)
def test_helper_get_hash_retries(monkeypatch, num_tries_required, num_retries_allowed):
    num_tries = 0
    expected_hash = "a" * 64
    rest_of_url = "online/qtsdkrepository/some/path/to/archive.7z"

    def mock_getUrl(url, *args, **kwargs):
        nonlocal num_tries
        num_tries += 1
        if num_tries < num_tries_required:
            raise ArchiveConnectionError(f"Must retry {num_tries_required - num_tries} more times before success")
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        assert base in Settings.trusted_mirrors
        # Check that the url was composed properly
        assert url[len(base) :] == f"/{rest_of_url}.sha256"

        hash_filename = str(parsed.path.split("/")[-1])
        assert hash_filename == "archive.7z.sha256"
        return f"{expected_hash} archive.7z"

    monkeypatch.setattr("aqt.helper.getUrl", mock_getUrl)

    if num_tries_required > num_retries_allowed:
        with pytest.raises(ChecksumDownloadFailure) as e:
            get_hash(rest_of_url, "sha256", (5, 5))
        assert e.type == ChecksumDownloadFailure
    else:
        result = get_hash(rest_of_url, "sha256", (5, 5))
        assert result == binascii.unhexlify(expected_hash)


@pytest.mark.parametrize(
    "received_hash",
    (
        "",  # Empty
        "a" * 40,  # Hash length for sha1 checksums
        "q" * 64,  # Not a hex digit; you can't unhexlify this
    ),
)
def test_helper_get_hash_bad_hash(monkeypatch, received_hash):
    def mock_getUrl(url, *args, **kwargs):
        hash_filename = str(urlparse(url).path.split("/")[-1])
        assert hash_filename.endswith(".sha256")
        filename = hash_filename[: -len(".sha256")]
        return f"{received_hash} {filename}"

    monkeypatch.setattr("aqt.helper.getUrl", mock_getUrl)

    with pytest.raises(ChecksumDownloadFailure) as e:
        get_hash("online/qtsdkrepository/some/path/to/archive.7z", "sha256", (5, 5))
    assert e.type == ChecksumDownloadFailure


@pytest.mark.parametrize(
    "version, expect",
    [
        ("1.33.1", Version(major=1, minor=33, patch=1)),
        (
            "1.33.1-202102101246",
            Version(major=1, minor=33, patch=1, build=("202102101246",)),
        ),
        (
            "1.33-202102101246",
            Version(major=1, minor=33, patch=0, build=("202102101246",)),
        ),
        ("2020-05-19-1", Version(major=2020, minor=0, patch=0, build=("05-19-1",))),
    ],
)
def test_helper_to_version_permissive(version, expect):
    assert Version.permissive(version) == expect


def mocked_request_response_class(num_redirects: int = 0, forbidden_baseurls=None):
    if not forbidden_baseurls:
        forbidden_hostnames = []
    else:
        forbidden_hostnames = [urlparse(host).hostname for host in forbidden_baseurls]

    class MockResponse:
        redirects_for_host = {}

        def __init__(self, url: str, headers: Dict, text: str):
            self.url = url
            self.headers = {key: value for key, value in headers.items()}

            hostname = urlparse(url).hostname
            if hostname not in MockResponse.redirects_for_host:
                MockResponse.redirects_for_host[hostname] = num_redirects

            if MockResponse.redirects_for_host[hostname] > 0:
                MockResponse.redirects_for_host[hostname] -= 1
                self.status_code = 302
                self.headers["Location"] = f"{url}/redirect{MockResponse.redirects_for_host[hostname]}"
                self.text = f"Still {MockResponse.redirects_for_host[hostname]} redirects to go..."
                self.reason = "Redirect"
            elif hostname in forbidden_hostnames:
                raise requests.exceptions.ConnectionError()
            else:
                self.status_code = 200
                self.text = text

    return MockResponse


def mock_get_redirect(num_redirects: int):
    response_class = mocked_request_response_class(num_redirects)

    def _mock(url: str, timeout=None, allow_redirects=None):
        return response_class(url, {}, text="some_html_content")

    def _mock_session(self, url: str, allow_redirects=None, timeout=None, stream=None):
        return response_class(url, {}, text="some_html_content")

    return _mock, _mock_session


def test_helper_getUrl_ok(monkeypatch):
    mocked_get, mocked_session_get = mock_get_redirect(0)
    monkeypatch.setattr(requests, "get", mocked_get)
    monkeypatch.setattr(requests.Session, "get", mocked_session_get)
    assert getUrl("some_url", timeout=(5, 5)) == "some_html_content"


def test_helper_getUrl_redirect_5(monkeypatch):
    mocked_get, mocked_session_get = mock_get_redirect(num_redirects=5)
    monkeypatch.setattr(requests, "get", mocked_get)
    monkeypatch.setattr(requests.Session, "get", mocked_session_get)
    assert getUrl("some_url", (5, 5)) == "some_html_content"


def test_helper_getUrl_redirect_too_many(monkeypatch):
    mocked_get, mocked_session_get = mock_get_redirect(num_redirects=11)
    monkeypatch.setattr(requests, "get", mocked_get)
    monkeypatch.setattr(requests.Session, "get", mocked_session_get)
    with pytest.raises(ArchiveDownloadError) as e:
        getUrl("some_url", (5, 5))
    assert e.type == ArchiveDownloadError


def test_helper_getUrl_conn_error(monkeypatch):
    response_class = mocked_request_response_class(forbidden_baseurls=["https://www.forbidden.com"])
    url = "https://www.forbidden.com/some_path"
    timeout = (5, 5)

    expect_re = re.compile(r"^Failure to connect to.+" + re.escape(url))

    def _mock(url: str, *args, **kwargs):
        return response_class(url, {}, text="some_html_content")

    def _mock_session(self, url: str, *args, **kargs):
        return response_class(url, {}, text="some_html_content")

    monkeypatch.setattr(requests, "get", _mock)
    monkeypatch.setattr(requests.sessions.Session, "get", _mock_session)
    with pytest.raises(ArchiveConnectionError) as e:
        getUrl(url, timeout)
    assert e.type == ArchiveConnectionError
    assert expect_re.match(format(e.value))


def test_helper_getUrl_checksum_error(monkeypatch):
    mocked_get, mocked_session_get = mock_get_redirect(0)
    monkeypatch.setattr(requests, "get", mocked_get)
    monkeypatch.setattr(requests.Session, "get", mocked_session_get)
    with pytest.raises(ArchiveChecksumError) as e:
        getUrl("some_url", timeout=(5, 5), expected_hash=b"AAAAAAAAAAA")
    assert e.type == ArchiveChecksumError
