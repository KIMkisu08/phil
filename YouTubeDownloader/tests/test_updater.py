"""updater.py 오프라인 테스트: 인터넷 대신 가짜 응답을 씀."""
import hashlib
import io
import json
import os
import socket
import urllib.error

import pytest

import updater


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "LIB_DIR", str(tmp_path))
    return tmp_path


def test_vtuple():
    assert updater._vtuple("2026.10.21") == (2026, 10, 21)
    assert updater._vtuple("2026.10.21") > updater._vtuple("2026.9.30")


def test_override_wheels_keeps_newest_only(lib, monkeypatch):
    monkeypatch.setattr(updater, "_bundled_version", lambda: "2026.1.1")
    for name in ("yt_dlp-2026.2.1.whl", "yt_dlp-2026.3.1.whl", "yt_dlp_ejs-0.1.0.whl", "yt_dlp_ejs-0.2.0.whl",
                 "something-else.whl"):
        (lib / name).write_bytes(b"")
    got = sorted(os.path.basename(p) for p in updater._override_wheels())
    assert got == ["yt_dlp-2026.3.1.whl", "yt_dlp_ejs-0.2.0.whl"]
    assert sorted(os.listdir(lib)) == ["something-else.whl", "yt_dlp-2026.3.1.whl", "yt_dlp_ejs-0.2.0.whl"]


def test_override_wheels_ignored_when_bundled_is_newer(lib, monkeypatch):
    monkeypatch.setattr(updater, "_bundled_version", lambda: "2026.5.1")
    (lib / "yt_dlp-2026.3.1.whl").write_bytes(b"")
    assert updater._override_wheels() == []


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _pypi(responses):
    """urlopen 대역: 주소별로 준비한 응답(bytes) 또는 오류(Exception)."""
    def urlopen(url, timeout=None):
        r = responses[url]
        if isinstance(r, Exception):
            raise r
        return FakeResponse(r)
    return urlopen


def _meta(pkg, ver, data, sha=None):
    return json.dumps({"info": {"version": ver}, "urls": [
        {"packagetype": "sdist", "url": f"https://files/{pkg}.tar.gz", "digests": {"sha256": "x"}},
        {"packagetype": "bdist_wheel", "url": f"https://files/{pkg}.whl",
         "digests": {"sha256": sha or hashlib.sha256(data).hexdigest()}},
    ]}).encode()


def test_update_engine_saves_verified_wheels(lib, monkeypatch):
    monkeypatch.setattr(updater.urllib.request, "urlopen", _pypi({
        "https://pypi.org/pypi/yt-dlp/json": _meta("yt-dlp", "2026.10.22", b"A"),
        "https://files/yt-dlp.whl": b"A",
        "https://pypi.org/pypi/yt-dlp-ejs/json": _meta("yt-dlp-ejs", "0.3.0", b"B"),
        "https://files/yt-dlp-ejs.whl": b"B",
    }))
    assert updater.update_engine() == "yt-dlp 2026.10.22, yt-dlp-ejs 0.3.0"
    assert sorted(os.listdir(lib)) == ["yt_dlp-2026.10.22.whl", "yt_dlp_ejs-0.3.0.whl"]


def test_update_engine_hash_mismatch_saves_nothing(lib, monkeypatch):
    monkeypatch.setattr(updater.urllib.request, "urlopen", _pypi({
        "https://pypi.org/pypi/yt-dlp/json": _meta("yt-dlp", "2026.10.22", b"A", sha="0" * 64),
        "https://files/yt-dlp.whl": b"tampered",
    }))
    with pytest.raises(RuntimeError, match="해시 불일치"):
        updater.update_engine()
    assert os.listdir(lib) == []


@pytest.mark.parametrize("error", [
    urllib.error.URLError("getaddrinfo failed"),
    TimeoutError("The read operation timed out"),
    socket.timeout("timed out"),
    ConnectionResetError(10054, "Connection reset by peer"),
])
def test_update_engine_network_errors_are_friendly(lib, monkeypatch, error):
    monkeypatch.setattr(updater.urllib.request, "urlopen", _pypi({"https://pypi.org/pypi/yt-dlp/json": error}))
    with pytest.raises(RuntimeError, match="인터넷 연결을 확인하세요"):
        updater.update_engine()


def test_update_engine_broken_response(lib, monkeypatch):
    monkeypatch.setattr(updater.urllib.request, "urlopen", _pypi({"https://pypi.org/pypi/yt-dlp/json": b"<html>"}))
    with pytest.raises(RuntimeError, match="업데이트 정보를 읽지 못했습니다"):
        updater.update_engine()


def test_check_app_update_offline_and_dev(monkeypatch):
    monkeypatch.setattr(updater.urllib.request, "urlopen", _pypi({}))   # 어떤 주소든 KeyError → 쓰면 안 됨
    assert updater.check_app_update("dev") is None

    def offline(*a, **k):
        raise urllib.error.URLError("offline")
    monkeypatch.setattr(updater.urllib.request, "urlopen", offline)
    assert updater.check_app_update("1.0.0") is None
