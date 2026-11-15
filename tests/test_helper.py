import binascii
import hashlib
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
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


def test_setup_logging_writes_no_log_file_by_default(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AQT_LOG_FILE", raising=False)
    monkeypatch.delenv("LOG_CFG", raising=False)
    helper.setup_logging()
    helper.getLogger("aqt.main").info("a line for every handler")
    assert list(tmp_path.iterdir()) == []


def test_setup_logging_takes_a_configuration_of_ones_own_as_it_is(monkeypatch, tmp_path):
    custom_config = tmp_path / "logging.ini"
    custom_config.write_text("a logging configuration of one's own")
    configured_with = []
    monkeypatch.delenv("AQT_LOG_FILE", raising=False)
    monkeypatch.setenv("LOG_CFG", str(custom_config))
    monkeypatch.setattr(helper.Settings, "loggingconf", helper.Settings.loggingconf)
    monkeypatch.setattr(helper.logging.config, "fileConfig", configured_with.append)
    helper.setup_logging()
    assert configured_with == [str(custom_config)]


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


# A test that plays mirrors of different speeds does so by sleeping a few milliseconds a request,
# and then counts on which mirror the download found the faster one. Windows sleeps in steps of
# 15.6 ms, and a busy machine of a CI service sleeps longer than asked, so the played mirrors look
# alike there, and these tests are left out. They run on a developer's machine.
plays_speeds = pytest.mark.skipif(
    sys.platform == "win32" or bool(os.environ.get("CI")),
    reason="This machine sleeps too coarsely to play mirror speeds",
)


class MirrorsWithRanges:
    """Stands in for Qt's server, which sends a download on to a mirror, and for the mirrors, which
    hand out any range of bytes of the file. Notes which mirror was asked for which range."""

    QT_URL = "https://download.example.com/online/qtsdkrepository/qt/archive.7z"
    MIRRORS = [f"https://mirror{number}.example.com/pub/online/qtsdkrepository/qt/archive.7z" for number in (1, 2, 3)]
    METALINK = """<?xml version="1.0" encoding="UTF-8"?>
<metalink xmlns="urn:ietf:params:xml:ns:metalink">
  <publisher><url>https://download.example.com</url></publisher>
  <file name="archive.7z">
    <url location="de" priority="2">{1}</url>
    <url location="fi" priority="1">{0}</url>
    <url location="us" priority="3">{2}</url>
    <url location="cn" priority="4">http://mirrors.tuna.tsinghua.edu.cn/online/qtsdkrepository/qt/archive.7z</url>
  </file>
</metalink>""".format(*MIRRORS)

    def __init__(
        self,
        monkeypatch,
        content: bytes,
        broken_mirrors=(),
        accept_ranges="bytes",
        without_file=(),
        seconds=None,
        bytes_a_second=None,
        silent_mirrors=(),
        takes_connections=None,
    ):
        self.content = content
        self.broken_mirrors = broken_mirrors
        self.without_file = without_file
        self.asked_for_file = []
        self.asked_qt_server = 0
        self.accept_ranges = accept_ranges
        self.asked = []
        # How long each mirror takes to answer a request for a range, its latency, and how fast
        # the range's bytes then come, its bandwidth: a number, or a function that gives it for
        # the mirror's first, second and every further such request. A mirror not named answers
        # at once and delivers 5,000 bytes a second, so that a range takes long enough to time.
        self.seconds = seconds or {}
        self.bytes_a_second = bytes_a_second or {}
        # Mirrors that answer their test, and from then on say nothing until the caller's patience ends
        self.silent_mirrors = silent_mirrors
        self.patience = []
        # The connections that asked each mirror for a range, and how many a mirror takes at once
        self.connections = {}
        self.takes_connections = takes_connections
        self.asked_lock = threading.Lock()
        mirrors = self

        class Answer:
            def __init__(self, url: str, status_code: int, headers: Dict[str, str], body: bytes = b"", bytes_a_second=0):
                self.url, self.status_code, self.headers, self.body = url, status_code, headers, body
                self.reason = "a reason"
                self.bytes_a_second = bytes_a_second

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def close(self):
                pass

            def iter_content(self, chunk_size):
                for start in range(0, len(self.body), chunk_size):
                    chunk = self.body[start : start + chunk_size]
                    if self.bytes_a_second:
                        time.sleep(len(chunk) / self.bytes_a_second)
                    yield chunk

        def mock_get(session, url, **kwargs):
            wanted = kwargs.get("headers", {}).get("Range")
            if url == mirrors.QT_URL:
                mirrors.asked_qt_server += 1
                return Answer(url, 302, {"Location": mirrors.MIRRORS[0]})
            if wanted is None:
                mirrors.asked_for_file.append(url)
                if url in mirrors.without_file:
                    return Answer(url, 404, {})
                headers = {"Content-Length": str(len(mirrors.content)), "Accept-Ranges": mirrors.accept_ranges}
                return Answer(url, 200, headers, mirrors.content)
            first, last = (int(number) for number in wanted[len("bytes=") :].split("-"))
            with mirrors.asked_lock:
                mirrors.asked.append((url, first, last))
                nth = sum(1 for asked_url, _, _ in mirrors.asked if asked_url == url)
                connections = mirrors.connections.setdefault(url, [])
                if id(session) not in connections:
                    if mirrors.takes_connections is not None and len(connections) >= mirrors.takes_connections:
                        return Answer(url, 503, {})
                    connections.append(id(session))
            mirrors.patience.append(kwargs["timeout"])
            if url in mirrors.silent_mirrors and nth > 1:
                raise requests.exceptions.ReadTimeout(f"{url} sent nothing for {kwargs['timeout'][1]} seconds")
            seconds = mirrors.seconds.get(url, 0)
            time.sleep(seconds(nth) if callable(seconds) else seconds)
            if url in mirrors.broken_mirrors:
                return Answer(url, 503, {})
            bytes_a_second = mirrors.bytes_a_second.get(url, 5_000)
            bytes_a_second = bytes_a_second(nth) if callable(bytes_a_second) else bytes_a_second
            return Answer(url, 206, {}, mirrors.content[first : last + 1], bytes_a_second)

        class Metalink:
            status_code = 200
            headers = {"content-type": "application/metalink4+xml; charset=utf-8"}
            text = mirrors.METALINK

        monkeypatch.setattr(requests.Session, "get", mock_get)
        monkeypatch.setattr(helper, "_get_meta", lambda url: Metalink())
        # A file of some thousand bytes then counts as a large one
        monkeypatch.setattr(helper, "BYTES_PER_RANGE", 300)
        monkeypatch.setattr(helper, "BYTES_PER_TEST", 100)
        monkeypatch.setattr(helper, "RANGES_PER_JUDGEMENT", 4)
        # Nothing is known of any server when a test starts
        monkeypatch.setattr(helper, "_servers_known", {})

    def ranges_from(self, mirror: str) -> int:
        return sum(1 for url, _, _ in self.asked if url == mirror)


@plays_speeds
def test_helper_downloadBinary_in_ranges_from_the_mirror_with_the_most_bandwidth(tmp_path, monkeypatch):
    content = os.urandom(10_000)
    first, second, third = MirrorsWithRanges.MIRRORS
    # The second mirror answers last, but delivers ten times as fast as the others
    mirrors = MirrorsWithRanges(
        monkeypatch, content, seconds={first: 0.005, second: 0.03, third: 0.005}, bytes_a_second={second: 50_000}
    )
    out = tmp_path / "archive.7z"
    helper.downloadBinaryFile(mirrors.QT_URL, out, "sha256", hashlib.sha256(content).digest(), (5, 30))
    assert out.read_bytes() == content
    # Each mirror was asked for one small range as a test, and none was the blacklisted one
    assert {url for url, _, _ in mirrors.asked} == set(mirrors.MIRRORS)
    assert {(first_byte, last_byte) for _, first_byte, last_byte in mirrors.asked if first_byte < 300} == {
        (0, 99),
        (100, 199),
        (200, 299),
    }
    # The other two finish their tests first and serve the connections that are free by then, until the
    # second mirror's test shows what it can do; from then on it serves them all
    assert mirrors.ranges_from(second) > mirrors.ranges_from(first) + mirrors.ranges_from(third)
    assert mirrors.asked[-1][0] == second
    known = helper._servers_known
    assert set(known) == {
        "https://mirror1.example.com/pub/",
        "https://mirror2.example.com/pub/",
        "https://mirror3.example.com/pub/",
    }
    assert known[helper._server_of(second)].bandwidth > 5 * known[helper._server_of(first)].bandwidth
    assert known[helper._server_of(second)].latency > known[helper._server_of(first)].latency
    # No connection waits longer than a few seconds for a mirror that has gone silent
    assert set(mirrors.patience) == {(5, helper.SECONDS_OF_SILENCE_AT_MOST)}


@pytest.mark.parametrize(
    "known, expected",
    [
        ({}, "a"),
        ({"b": (9e6, 0.2), "c": (30e6, 0.4)}, "c"),
        # About as fast as each other: the one that answers sooner
        ({"a": (27.5e6, 0.05), "b": (30e6, 0.4), "c": (10e6, 0.01)}, "a"),
        ({"a": (26e6, 0.05), "b": (30e6, 0.4), "c": (10e6, 0.01)}, "b"),
        # A server that takes over a second to answer counts only when all do
        ({"a": (5e6, 0.3), "b": (40e6, 1.5)}, "a"),
        ({"a": (5e6, 1.3), "b": (40e6, 1.5)}, "b"),
    ],
)
def test_helper_best_server(known, expected):
    facts = {server: helper.ServerFacts(*figures) for server, figures in known.items()}
    assert helper._best_server(["a", "b", "c"], facts) == expected


@plays_speeds
def test_helper_downloadBinary_goes_to_the_best_mirror_once_it_is_known(tmp_path, monkeypatch):
    content = os.urandom(10_000)
    first, second, third = MirrorsWithRanges.MIRRORS
    mirrors = MirrorsWithRanges(monkeypatch, content, bytes_a_second={second: 50_000})
    out = tmp_path / "archive.7z"
    helper.downloadBinaryFile(mirrors.QT_URL, out, "sha256", hashlib.sha256(content).digest(), (5, 5))
    assert mirrors.asked_qt_server == 1

    # A second large file goes to the best mirror at once: no test, and no question to Qt's server
    mirrors.asked.clear()
    mirrors.asked_for_file.clear()
    helper.downloadBinaryFile(mirrors.QT_URL, out, "sha256", hashlib.sha256(content).digest(), (5, 5))
    assert out.read_bytes() == content
    assert mirrors.asked_qt_server == 1
    assert mirrors.asked_for_file == [second]
    assert {url for url, _, _ in mirrors.asked} == {second}
    assert all(last_byte - first_byte + 1 == 300 or last_byte == 9_999 for _, first_byte, last_byte in mirrors.asked)

    # And so does a small file, over its one connection
    mirrors.content = os.urandom(500)
    mirrors.asked.clear()
    mirrors.asked_for_file.clear()
    helper.downloadBinaryFile(mirrors.QT_URL, out, "sha256", hashlib.sha256(mirrors.content).digest(), (5, 5))
    assert out.read_bytes() == mirrors.content
    assert mirrors.asked_qt_server == 1
    assert mirrors.asked_for_file == [second]
    assert mirrors.asked == []


def test_helper_downloadBinary_goes_back_to_qts_server_when_the_best_mirror_has_not_got_the_file(tmp_path, monkeypatch):
    content = os.urandom(500)
    first, second, third = MirrorsWithRanges.MIRRORS
    mirrors = MirrorsWithRanges(monkeypatch, content, without_file=[second])
    helper._servers_known[helper._server_of(second)] = helper.ServerFacts(9e6, 0.1)
    out = tmp_path / "archive.7z"
    helper.downloadBinaryFile(mirrors.QT_URL, out, "sha256", hashlib.sha256(content).digest(), (5, 5))
    assert out.read_bytes() == content
    assert mirrors.asked_for_file == [second, first]
    assert mirrors.asked_qt_server == 1


def test_helper_downloadBinary_in_ranges_tests_no_mirror_for_a_file_too_small(tmp_path, monkeypatch):
    # Three tests of 100 bytes would be more than a quarter of this file
    content = os.urandom(1_000)
    mirrors = MirrorsWithRanges(monkeypatch, content)
    out = tmp_path / "archive.7z"
    helper.downloadBinaryFile(mirrors.QT_URL, out, "sha256", hashlib.sha256(content).digest(), (5, 5))
    assert out.read_bytes() == content
    assert {url for url, _, _ in mirrors.asked} == {mirrors.MIRRORS[0]}
    assert helper._servers_known == {}


@plays_speeds
def test_helper_downloadBinary_in_ranges_leaves_a_mirror_that_falls_behind(tmp_path, monkeypatch):
    content = os.urandom(40_000)
    first, second, third = MirrorsWithRanges.MIRRORS
    # The second mirror is the fastest in its test and in its first ranges, and then slows down to a
    # tenth, far under three quarters of what the first mirror, half as fast in its test, would do
    mirrors = MirrorsWithRanges(
        monkeypatch,
        content,
        bytes_a_second={first: 20_000, second: lambda nth: 40_000 if nth <= 16 else 4_000, third: 5_000},
    )
    out = tmp_path / "archive.7z"
    helper.downloadBinaryFile(mirrors.QT_URL, out, "sha256", hashlib.sha256(content).digest(), (5, 5))
    assert out.read_bytes() == content
    # The first mirror, the next best, took over and delivered most of the file
    assert mirrors.ranges_from(first) > mirrors.ranges_from(second)
    assert mirrors.asked[-1][0] == first
    assert mirrors.ranges_from(third) == 1
    assert (
        helper._servers_known[helper._server_of(second)].bandwidth
        < helper._servers_known[helper._server_of(first)].bandwidth
    )


@plays_speeds
def test_helper_downloadBinary_in_ranges_leaves_a_mirror_that_goes_silent(tmp_path, monkeypatch):
    content = os.urandom(10_000)
    first, second, third = MirrorsWithRanges.MIRRORS
    mirrors = MirrorsWithRanges(
        monkeypatch, content, bytes_a_second={first: 20_000, second: 100_000}, silent_mirrors=[second]
    )
    out = tmp_path / "archive.7z"
    helper.downloadBinaryFile(mirrors.QT_URL, out, "sha256", hashlib.sha256(content).digest(), (5, 30))
    assert out.read_bytes() == content
    assert mirrors.asked[-1][0] == first


def test_helper_downloadBinary_in_ranges_passes_a_broken_mirror_by(tmp_path, monkeypatch):
    content = os.urandom(10_000)
    mirrors = MirrorsWithRanges(monkeypatch, content, broken_mirrors=MirrorsWithRanges.MIRRORS[1:2])
    out = tmp_path / "archive.7z"
    helper.downloadBinaryFile(mirrors.QT_URL, out, "sha256", hashlib.sha256(content).digest(), (5, 5))
    assert out.read_bytes() == content
    # Known as failed, so that no later download asks it
    assert helper._servers_known[helper._server_of(MirrorsWithRanges.MIRRORS[1])] == helper.ServerFacts(0.0, 0.0)


def test_helper_downloadBinary_in_ranges_fails_when_no_mirror_delivers(tmp_path, monkeypatch):
    content = os.urandom(10_000)
    mirrors = MirrorsWithRanges(monkeypatch, content, broken_mirrors=MirrorsWithRanges.MIRRORS)
    with pytest.raises(ArchiveConnectionError) as e:
        helper.downloadBinaryFile(
            mirrors.QT_URL, tmp_path / "archive.7z", "sha256", hashlib.sha256(content).digest(), (5, 5)
        )
    assert "none of the servers delivered it" in format(e.value)


def test_helper_downloadBinary_in_ranges_checks_the_checksum(tmp_path, monkeypatch):
    content = os.urandom(10_000)
    mirrors = MirrorsWithRanges(monkeypatch, content)
    with pytest.raises(ArchiveChecksumError):
        helper.downloadBinaryFile(mirrors.QT_URL, tmp_path / "archive.7z", "sha256", bytes(32), (5, 5))


@pytest.mark.parametrize(
    "size, accept_ranges, connections",
    [(10_000, "none", 8), (10_000, "bytes", 1), (599, "bytes", 8)],
    ids=["a server without ranges", "one connection allowed", "a small file"],
)
def test_helper_downloadBinary_in_one_piece(tmp_path, monkeypatch, size, accept_ranges, connections):
    content = os.urandom(size)
    mirrors = MirrorsWithRanges(monkeypatch, content, accept_ranges=accept_ranges)
    monkeypatch.setattr(helper.SettingsClass, "connections", connections)
    out = tmp_path / "archive.7z"
    helper.downloadBinaryFile(mirrors.QT_URL, out, "sha256", hashlib.sha256(content).digest(), (5, 5))
    assert out.read_bytes() == content
    assert mirrors.asked == []


def test_helper_downloadBinary_connection_breaks_in_the_middle(tmp_path, monkeypatch):
    def iter_broken_content(*args, **kwargs):
        yield b"the first bytes of the file"
        raise requests.exceptions.ChunkedEncodingError("Connection broken: BrokenPipeError(32, 'Broken pipe')")

    def mock_requests_get(*args, **kwargs):
        response = Response()
        response.status_code = 200
        response.url = "http://example.com/test.xml"
        response.iter_content = iter_broken_content
        return response

    monkeypatch.setattr(requests.Session, "get", mock_requests_get)

    # An error that lets the caller go on with a fallback server, as for one that cannot be reached
    with pytest.raises(ArchiveConnectionError) as e:
        helper.downloadBinaryFile("http://example.com/test.xml", tmp_path / "text.xml", "md5", bytes(16), 60)
    assert format(e.value).startswith("Connection to example.com broke: Connection broken")


def test_helper_downloadBinary_asks_the_next_mirrors_for_a_file_one_has_not_got(tmp_path, monkeypatch):
    content = os.urandom(500)
    mirrors = MirrorsWithRanges(monkeypatch, content, without_file=MirrorsWithRanges.MIRRORS[:2])
    out = tmp_path / "archive.7z"
    helper.downloadBinaryFile(mirrors.QT_URL, out, "sha256", hashlib.sha256(content).digest(), (5, 5))
    assert out.read_bytes() == content
    assert mirrors.asked_for_file == mirrors.MIRRORS


def test_helper_downloadBinary_gives_up_when_no_mirror_has_the_file(tmp_path, monkeypatch):
    mirrors = MirrorsWithRanges(monkeypatch, os.urandom(500), without_file=MirrorsWithRanges.MIRRORS)
    out = tmp_path / "archive.7z"
    with pytest.raises(ArchiveConnectionError) as e:
        helper.downloadBinaryFile(mirrors.QT_URL, out, "sha256", bytes(32), (5, 5))
    assert (
        format(e.value)
        == "Failed to download archive.7z from mirror3.example.com\nServer response code: 404, reason: a reason"
    )
    assert mirrors.asked_for_file == mirrors.MIRRORS
    assert not out.exists()


@plays_speeds
def test_helper_downloadBinary_in_ranges_opens_more_connections_while_it_is_fast(tmp_path, monkeypatch):
    content = os.urandom(60_000)
    mirrors = MirrorsWithRanges(monkeypatch, content, bytes_a_second={MirrorsWithRanges.MIRRORS[0]: 20_000})
    # Whatever this download delivers counts as fast, and one connection may follow another at once
    monkeypatch.setattr(helper, "MEGABITS_PER_EXTRA_CONNECTION", 1e-9)
    monkeypatch.setattr(helper, "MEGABITS_BEFORE_EXTRA_CONNECTIONS", 0)
    monkeypatch.setattr(helper, "SECONDS_BETWEEN_EXTRA_CONNECTIONS", 0)
    out = tmp_path / "archive.7z"
    first = mirrors.MIRRORS[0]
    # Without leave to open more, the download stays with its eight connections
    helper._download_in_ranges([first], out, len(content), 8, (5, 5))
    assert out.read_bytes() == content
    assert len(mirrors.connections[first]) == 8
    # With it, the download goes up to the most it may have
    mirrors.connections.clear()
    helper._download_in_ranges([first], out, len(content), 8, (5, 5), 12)
    assert out.read_bytes() == content
    assert len(mirrors.connections[first]) == 12


@plays_speeds
def test_helper_downloadBinary_in_ranges_stays_at_its_connections_while_it_is_slow(tmp_path, monkeypatch):
    content = os.urandom(20_000)
    mirrors = MirrorsWithRanges(monkeypatch, content, bytes_a_second={MirrorsWithRanges.MIRRORS[0]: 20_000})
    monkeypatch.setattr(helper, "SECONDS_BETWEEN_EXTRA_CONNECTIONS", 0)
    out = tmp_path / "archive.7z"
    first = mirrors.MIRRORS[0]
    # Some hundred thousand bytes a second are far from the megabits that earn a connection more
    helper._download_in_ranges([first], out, len(content), 8, (5, 5), 32)
    assert out.read_bytes() == content
    assert len(mirrors.connections[first]) == 8


@plays_speeds
def test_helper_downloadBinary_in_ranges_learns_how_many_connections_a_mirror_takes(tmp_path, monkeypatch):
    content = os.urandom(60_000)
    first = MirrorsWithRanges.MIRRORS[0]
    mirrors = MirrorsWithRanges(monkeypatch, content, bytes_a_second={first: 20_000}, takes_connections=10)
    monkeypatch.setattr(helper, "MEGABITS_PER_EXTRA_CONNECTION", 1e-9)
    monkeypatch.setattr(helper, "MEGABITS_BEFORE_EXTRA_CONNECTIONS", 0)
    monkeypatch.setattr(helper, "SECONDS_BETWEEN_EXTRA_CONNECTIONS", 0)
    helper._servers_known[helper._server_of(first)] = helper.ServerFacts(9e6, 0.1)
    out = tmp_path / "archive.7z"
    helper._download_in_ranges([first], out, len(content), 8, (5, 5), 32)
    assert out.read_bytes() == content
    # The eleventh connection was refused: the mirror is known to take ten, and is still the one in use
    assert len(mirrors.connections[first]) == 10
    assert helper._servers_known[helper._server_of(first)] == helper.ServerFacts(9e6, 0.1, 10)
    # A later download asks it for no more than that
    mirrors.connections.clear()
    helper._download_in_ranges([first], out, len(content), 8, (5, 5), 32)
    assert out.read_bytes() == content
    assert len(mirrors.connections[first]) == 10
    assert mirrors.asked.count((first, 0, 299)) >= 1


def test_connection_budget():
    budget = helper.ConnectionBudget(threading.Condition(), {}, 8)
    events = []
    lock = threading.Lock()

    def download(name: str, connections: int, seconds: float) -> None:
        with budget.take(connections):
            with lock:
                events.append(f"{name} starts")
            time.sleep(seconds)
            with lock:
                events.append(f"{name} ends")

    # Two small files, then a large one that needs every connection, then a small one that asked after it
    threads = []
    for name, connections, seconds in (("small 1", 1, 0.1), ("small 2", 1, 0.1), ("large", 8, 0.1), ("small 3", 1, 0.02)):
        threads.append(threading.Thread(target=download, args=(name, connections, seconds)))
        threads[-1].start()
        time.sleep(0.02)
    for thread in threads:
        thread.join()
    # The small ones ran side by side; the large one waited for both, and ran alone; the last small one,
    # which asked after the large one, waited for it though a connection was free before
    assert events[:2] == ["small 1 starts", "small 2 starts"]
    assert events.index("large starts") > events.index("small 1 ends")
    assert events.index("large starts") > events.index("small 2 ends")
    assert events.index("small 3 starts") > events.index("large ends")


def test_helper_downloadBinary_takes_connections_of_the_budget(tmp_path, monkeypatch):
    taken = []

    class Budget:
        @contextmanager
        def take(self, connections):
            taken.append(connections)
            yield
            taken.append(-connections)

    monkeypatch.setattr(helper, "_connection_budget", Budget())
    out = tmp_path / "archive.7z"
    # A small file downloads over the one connection it began with
    content = os.urandom(500)
    mirrors = MirrorsWithRanges(monkeypatch, content)
    helper.downloadBinaryFile(mirrors.QT_URL, out, "sha256", hashlib.sha256(content).digest(), (5, 5))
    assert taken == [1, -1]
    # A large one gives that connection back, and takes as many as it is worth
    taken.clear()
    mirrors.content = os.urandom(10_000)
    helper.downloadBinaryFile(mirrors.QT_URL, out, "sha256", hashlib.sha256(mirrors.content).digest(), (5, 5))
    assert taken == [1, -1, 8, -8]


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
