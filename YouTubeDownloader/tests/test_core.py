"""core.py 오프라인 테스트: 인터넷·유튜브·FFmpeg 없이 돈다 (yt-dlp와 ffmpeg 호출은 가짜로 대체)."""
import io
import os

import pytest
import yt_dlp
from mutagen.id3 import ID3
from PIL import Image

import core


# ---------- 링크 판별 ----------

@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ?si=abc",
    "youtube.com/shorts/abcdefghijk",
    "http://YouTube.com/watch?v=x",
])
def test_is_youtube_url_accepts_youtube(url):
    assert core.is_youtube_url(url)


@pytest.mark.parametrize("url", [
    "", "그냥 글자", "https://vimeo.com/123", "https://youtube.com.evil.example/watch?v=x",
    "https://notyoutube.com/watch?v=x", "ftp://youtube.com/x", "https://[::1/",
])
def test_is_youtube_url_rejects_others(url):
    assert not core.is_youtube_url(url)


def test_fetch_info_rejects_non_youtube_without_network(monkeypatch):
    monkeypatch.setattr(yt_dlp, "YoutubeDL", _explode)
    with pytest.raises(core.DownloadError, match="유튜브 링크가 아닙니다"):
        core.fetch_info("https://example.com/video")


def _explode(*a, **k):
    raise AssertionError("네트워크(yt-dlp)를 호출하면 안 됨")


# ---------- 파일 이름 ----------

def test_safe_filename_replaces_windows_bad_chars():
    assert core.safe_filename('AC/DC: "Back" <in> Black?*|') == "AC_DC_ _Back_ _in_ Black___"


def test_safe_filename_empty_and_dots():
    assert core.safe_filename("") == "제목없음"
    assert core.safe_filename(" ... ") == "제목없음"
    assert core.safe_filename("곡 이름. ") == "곡 이름"


@pytest.mark.parametrize("name", ["CON", "con", "NUL.mp3", "COM1", "LPT9"])
def test_safe_filename_reserved_names(name):
    assert core.safe_filename(name).startswith("_")


def test_safe_filename_length_limit():
    assert len(core.safe_filename("가" * 500)) == 180


def test_unique_path_counts_up(tmp_path):
    first = core.unique_path(str(tmp_path), "곡", "m4a")
    assert os.path.basename(first) == "곡.m4a"
    open(first, "w").close()
    second = core.unique_path(str(tmp_path), "곡", "m4a")
    assert os.path.basename(second) == "곡 (1).m4a"
    open(second, "w").close()
    assert os.path.basename(core.unique_path(str(tmp_path), "곡", "m4a")) == "곡 (2).m4a"


# ---------- 아티스트/제목 추측 ----------

def test_guess_uses_music_metadata_first():
    assert core.guess_artist_title({"artist": "IU", "track": "Love wins all", "title": "x"}) == ("IU", "Love wins all")
    assert core.guess_artist_title({"artists": ["A", "B"], "track": "T"}) == ("A, B", "T")


def test_guess_splits_title_and_strips_noise():
    info = {"title": "NewJeans - Ditto (Official MV)", "channel": "HYBE LABELS"}
    assert core.guess_artist_title(info) == ("NewJeans", "Ditto")
    assert core.guess_artist_title({"title": "아이유 – 밤편지 [가사]"}) == ("아이유", "밤편지")


def test_guess_falls_back_to_topic_channel():
    assert core.guess_artist_title({"title": "Hype Boy", "channel": "NewJeans - Topic"}) == ("NewJeans", "Hype Boy")
    assert core.guess_artist_title({}) == ("", "")


# ---------- 형식 고르기 ----------

FORMATS = [
    {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 129, "protocol": "https"},
    {"format_id": "251", "vcodec": "none", "acodec": "opus", "abr": 135, "protocol": "https"},
    {"format_id": "251-drc", "vcodec": "none", "acodec": "opus", "abr": 300, "protocol": "https"},
    {"format_id": "774", "vcodec": "none", "acodec": "opus", "abr": 256, "protocol": "https",
     "format_note": "Premium"},
    {"format_id": "hls-1", "vcodec": "none", "acodec": "mp4a", "abr": 999, "protocol": "m3u8_native"},
    {"format_id": "18", "vcodec": "avc1.42001E", "acodec": "mp4a.40.2", "height": 360, "width": 640},
    {"format_id": "137", "vcodec": "avc1.640028", "acodec": "none", "height": 1080, "width": 1920, "fps": 30},
    {"format_id": "248", "vcodec": "vp9", "acodec": "none", "height": 1080, "width": 1920, "fps": 30, "tbr": 9999},
    {"format_id": "313", "vcodec": "vp9", "acodec": "none", "height": 2160, "width": 3840, "fps": 30},
    {"format_id": "sb0", "vcodec": "none", "acodec": "none", "protocol": "mhtml"},
]


def test_pick_best_audio_prefers_premium_and_skips_drc_and_hls():
    assert core._pick_best_audio(FORMATS)["format_id"] == "774"
    assert core._pick_best_audio([f for f in FORMATS if f["format_id"] != "774"])["format_id"] == "251"
    assert core._pick_best_audio([]) is None


def test_pick_video_formats_prefers_h264_and_skips_muxed():
    v = core._pick_video_formats(FORMATS)
    assert sorted(v) == [1080, 2160]          # 360p는 소리 섞인 형식뿐이라 제외
    assert v[1080]["format_id"] == "137"      # 비트레이트가 높아도 VP9보다 H.264 우선


def test_pick_video_formats_vertical_short():
    v = core._pick_video_formats([{"format_id": "1", "vcodec": "avc1", "acodec": "none",
                                   "height": 1920, "width": 1080}])
    assert list(v) == [1080]


def _info(**kw):
    base = dict(url="https://www.youtube.com/watch?v=x", title="원래 제목", channel="채널", duration=10,
                thumbnail=None, artist_guess="가수", title_guess="곡", best_audio=core._pick_best_audio(FORMATS),
                video_formats=core._pick_video_formats(FORMATS))
    base.update(kw)
    return core.VideoInfo(**base)


def test_video_info_defaults_and_labels():
    info = _info()
    assert info.heights == [2160, 1080]
    assert info.default_height() == 1080        # 아이폰 호환 최고 화질
    assert info.is_iphone_ok(1080) and not info.is_iphone_ok(2160)
    assert info.is_premium_audio
    assert info.audio_label.startswith("Premium 고음질 · opus 256kbps")
    assert info.default_title("audio") == "곡" and info.default_title("video") == "원래 제목"


def test_video_info_without_formats():
    info = _info(best_audio=None, video_formats={})
    assert info.default_height() is None
    assert info.audio_label == "음원 정보 없음"
    assert not info.is_premium_audio


# ---------- 오류 메시지 ----------

NETWORK_ERRORS = [
    "ERROR: [youtube] x: Unable to download API page: <urlopen error [Errno 11001] getaddrinfo failed>",
    "ERROR: [youtube] x: Unable to download API page: <urlopen error Tunnel connection failed: 403 Forbidden> "
    "(caused by ProxyError('...')); please report this issue on  https://github.com/yt-dlp/yt-dlp/issues?q= , "
    "filling out the appropriate issue template.",
    "ERROR: unable to download video data: The read operation timed out",
    "ERROR: [download] Got error: [WinError 10054] Connection reset by peer",
    "ERROR: unable to download video data: HTTP Error 503: Service Unavailable",   # "unavailable"이어도 영상 문제 아님
]


@pytest.mark.parametrize("msg", NETWORK_ERRORS)
def test_friendly_network(msg):
    out = core._friendly(msg)
    assert out.startswith("인터넷 연결이 끊겼거나")
    assert "please report" not in out and not out.splitlines()[1].startswith("ERROR")


@pytest.mark.parametrize("msg,start", [
    ("ERROR: [youtube] x: Sign in to confirm your age.", "연령 제한"),
    ("ERROR: [youtube] x: Private video. Sign in if you've been granted access", "비공개"),
    ("ERROR: [youtube] x: Video unavailable. This video has been removed", "볼 수 없는"),
    ("ERROR: could not find firefox cookies database in '/x'", "브라우저 쿠키를"),
    ("ERROR: [youtube] x: Incomplete YouTube ID x", "링크를 이해하지"),
    ("ERROR: unable to open for writing: [Errno 13] Permission denied: 'C:\\x'", "저장 위치에 쓸 권한"),
    ("ERROR: unable to write data: [Errno 28] No space left on device", "저장 공간이 부족"),
    ("ERROR: unable to download video data: HTTP Error 403: Forbidden", "유튜브가 다운로드를 막았"),
])
def test_friendly_known_errors(msg, start):
    assert core._friendly(msg).startswith(start)


def test_friendly_age_word_not_matched_inside_other_words():
    # 예전에는 "page"·"message" 안의 "age" 때문에 연령 제한으로 잘못 안내함
    msg = "ERROR: this page is restricted in your region"
    assert not core._friendly(msg).startswith("연령 제한")


def test_friendly_unknown_passes_through_without_prefix():
    assert core._friendly("ERROR: something new") == "something new"


# ---------- 정보 불러오기 (가짜 yt-dlp) ----------

class FakeYDL:
    """yt_dlp.YoutubeDL 대역: 정해 둔 정보를 돌려주거나 정해 둔 오류를 냄."""
    result = None
    error = None
    write = None          # download() 때 임시 폴더에 만들 파일 {이름: 내용}
    last_opts = None

    def __init__(self, opts):
        FakeYDL.last_opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        if FakeYDL.error:
            raise FakeYDL.error
        return FakeYDL.result

    def download(self, urls):
        if FakeYDL.error:
            raise FakeYDL.error
        folder = os.path.dirname(self.last_opts["outtmpl"])
        for name, data in (FakeYDL.write or {}).items():
            with open(os.path.join(folder, name), "wb") as f:
                f.write(data)


@pytest.fixture
def fake_ydl(monkeypatch):
    FakeYDL.result = FakeYDL.error = FakeYDL.write = FakeYDL.last_opts = None
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    return FakeYDL


URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_fetch_info_builds_video_info(fake_ydl):
    fake_ydl.result = {"title": "NewJeans - Ditto (Official MV)", "channel": "HYBE LABELS", "duration": 185.6,
                       "webpage_url": URL, "thumbnail": "https://i.ytimg.com/x.jpg", "formats": FORMATS}
    info = core.fetch_info(URL)
    assert (info.artist_guess, info.title_guess, info.duration) == ("NewJeans", "Ditto", 185)
    assert info.best_audio["format_id"] == "774" and info.default_height() == 1080
    assert fake_ydl.last_opts["noplaylist"] is True


def test_fetch_info_playlist(fake_ydl):
    fake_ydl.result = {"_type": "playlist", "entries": []}
    with pytest.raises(core.DownloadError, match="재생목록"):
        core.fetch_info("https://www.youtube.com/playlist?list=PL1")


def test_fetch_info_network_error_is_friendly(fake_ydl):
    fake_ydl.error = yt_dlp.utils.DownloadError(NETWORK_ERRORS[0])
    with pytest.raises(core.DownloadError, match="인터넷 연결"):
        core.fetch_info(URL)


def test_fetch_info_cookie_load_error_is_caught(fake_ydl):
    # 브라우저 쿠키 읽기 실패는 DownloadError가 아닌 CookieLoadError로도 올라옴
    from yt_dlp.cookies import CookieLoadError
    fake_ydl.error = CookieLoadError("failed to load cookies")
    with pytest.raises(core.DownloadError, match="브라우저 쿠키를"):
        core.fetch_info(URL, browser="chrome")


def test_fetch_info_missing_cookie_file(fake_ydl, tmp_path):
    with pytest.raises(core.DownloadError, match="쿠키 파일을 찾을 수 없습니다"):
        core.fetch_info(URL, cookie_file=str(tmp_path / "없는파일.txt"))


def test_cookie_options(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    assert core._ydl_opts("firefox", None)["cookiesfrombrowser"] == ("firefox",)
    opts = core._ydl_opts("firefox", str(cookies))
    assert opts["cookiefile"] == str(cookies) and "cookiesfrombrowser" not in opts   # 파일이 우선
    assert "cookiefile" not in core._ydl_opts(None, None)


# ---------- 다운로드 (가짜 yt-dlp + 가짜 ffmpeg) ----------

@pytest.fixture
def fake_tools(monkeypatch, fake_ydl):
    monkeypatch.setattr(core, "ffmpeg_path", lambda: "ffmpeg")
    calls = []

    def fake_ffmpeg(ffmpeg, args):
        calls.append(args)
        with open(args[-1], "wb") as f:
            f.write(b"\x00" * 64)
    monkeypatch.setattr(core, "_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(core, "_cd_resample", lambda ffmpeg: "aresample=44100")
    fake_ydl.write = {"src.webm": b"audio"}
    return calls


def _jpeg(w=320, h=180) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "red").save(buf, "JPEG")
    return buf.getvalue()


def test_download_mp3_names_tags_and_cleans_up(fake_tools, tmp_path):
    info = _info(thumb_bytes=_jpeg(), thumbnail="https://i.ytimg.com/x.jpg")
    path = core.download(info, "audio", "mp3", " 아이유 ", " 밤편지 ", str(tmp_path / "새 폴더"))
    assert os.path.basename(path) == "아이유 - 밤편지.mp3"
    tags = ID3(path)
    assert str(tags["TIT2"]) == "밤편지" and str(tags["TPE1"]) == "아이유"
    cover = Image.open(io.BytesIO(tags.getall("APIC")[0].data))
    assert cover.size == (180, 180)                       # 16:9 썸네일 → 정사각형 앨범아트
    assert os.listdir(tmp_path / "새 폴더") == ["아이유 - 밤편지.mp3"]   # 임시 폴더가 남지 않음
    assert fake_tools[0][-3:-1] == ["-b:a", "320k"]


def test_download_twice_does_not_overwrite(fake_tools, tmp_path):
    info = _info()
    a = core.download(info, "audio", "mp3", "A", "T", str(tmp_path))
    b = core.download(info, "audio", "mp3", "A", "T", str(tmp_path))
    assert os.path.basename(a) == "A - T.mp3" and os.path.basename(b) == "A - T (1).mp3"


def test_download_aac_copies_when_source_is_aac(fake_tools, tmp_path):
    info = _info(best_audio=FORMATS[0])
    args = core._audio_args("aac", info, "ffmpeg")
    assert args == ("m4a", ["-c:a", "copy", "-movflags", "+faststart"])
    assert core._audio_args("aac", _info(), "ffmpeg")[1][:4] == ["-c:a", "aac", "-b:a", "256k"]
    assert core._audio_args("cd", _info(), "ffmpeg")[0] == "flac"


@pytest.mark.parametrize("kwargs,match", [
    ({"title": "   "}, "제목을 입력하세요"),
    ({"out_dir": "  "}, "저장 위치를 입력하세요"),
    ({"option": "ogg"}, "알 수 없는 음원 방식"),
    ({"info": _info(best_audio=None)}, "받을 수 있는 음원이 없습니다"),
    ({"kind": "video", "option": 720}, "선택한 화질을 찾을 수 없습니다"),
])
def test_download_input_errors(fake_tools, tmp_path, kwargs, match):
    args = {"info": _info(), "kind": "audio", "option": "mp3", "artist": "A", "title": "T",
            "out_dir": str(tmp_path)}
    args.update(kwargs)
    with pytest.raises(core.DownloadError, match=match):
        core.download(**args)


def test_download_out_dir_is_a_file(fake_tools, tmp_path):
    blocker = tmp_path / "파일"
    blocker.write_text("x")
    with pytest.raises(core.DownloadError, match="파일을 저장하지 못했습니다"):
        core.download(_info(), "audio", "mp3", "A", "T", str(blocker / "하위폴더"))


def test_download_network_drop_is_friendly_and_leaves_nothing(fake_tools, fake_ydl, tmp_path):
    fake_ydl.error = yt_dlp.utils.DownloadError(NETWORK_ERRORS[3])
    with pytest.raises(core.DownloadError, match="인터넷 연결"):
        core.download(_info(), "audio", "mp3", "A", "T", str(tmp_path))
    assert os.listdir(tmp_path) == []


def test_download_ignores_partial_files(fake_tools, fake_ydl, tmp_path):
    fake_ydl.write = {"src.webm.part": b"half"}
    with pytest.raises(core.DownloadError, match="다운로드한 파일을 찾지 못했습니다"):
        core.download(_info(), "audio", "mp3", "A", "T", str(tmp_path))


def test_download_tag_failure_leaves_no_half_file(fake_tools, monkeypatch, tmp_path):
    def broken(*a):
        raise core.DownloadError("태그 실패")
    monkeypatch.setattr(core, "tag_file", broken)
    with pytest.raises(core.DownloadError, match="태그 실패"):
        core.download(_info(), "audio", "mp3", "A", "T", str(tmp_path))
    assert os.listdir(tmp_path) == []


def test_download_reports_progress(fake_tools, fake_ydl, tmp_path):
    seen = []
    core.download(_info(), "audio", "mp3", "A", "T", str(tmp_path), progress=lambda p, t: seen.append(p))
    assert seen[-1] == 100
    hook = core._hook(lambda p, t: seen.append((p, t)))
    hook({"status": "downloading", "downloaded_bytes": 5e6, "total_bytes": 10e6, "speed": 1e6, "eta": 5})
    hook({"status": "downloading", "downloaded_bytes": 5e6})          # 전체 크기를 모를 때
    assert seen[-2] == (50.0, "5.0/10.0MB · 1.0MB/s · 남은 시간 5초") and seen[-1][0] == 0


# ---------- 앨범아트 ----------

def test_square_cover():
    out = core._square_cover(_jpeg(1280, 720))
    assert Image.open(io.BytesIO(out)).size == (720, 720)
    assert core._square_cover(None) is None
    assert core._square_cover(b"not an image") is None


# ---------- 실제 FFmpeg로 변환 (FFmpeg가 설치된 PC에서만, 인터넷은 여전히 안 씀) ----------

needs_ffmpeg = pytest.mark.skipif(not core.shutil.which("ffmpeg"), reason="FFmpeg 없음")


def _tone(tmp_path, ext, *extra):
    path = tmp_path / f"tone.{ext}"
    core.subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                         "sine=frequency=440:duration=1:sample_rate=48000", *extra, str(path)], check=True)
    return path.read_bytes()


@needs_ffmpeg
@pytest.mark.parametrize("mode,ext", [("aac", "m4a"), ("mp3", "mp3"), ("cd", "flac")])
def test_real_ffmpeg_audio_modes(fake_ydl, monkeypatch, tmp_path, mode, ext):
    from mutagen import File
    fake_ydl.write = {"src.webm": _tone(tmp_path, "webm", "-c:a", "libopus")}
    out = tmp_path / "out"
    info = _info(thumb_bytes=_jpeg(), thumbnail="https://i.ytimg.com/x.jpg")
    path = core.download(info, "audio", mode, "가수", "곡", str(out))
    assert os.path.basename(path) == f"가수 - 곡.{ext}"
    f = File(path)
    assert f.info.length == pytest.approx(1, abs=0.2)
    if mode == "cd":
        assert (f.info.sample_rate, f.info.bits_per_sample) == (44100, 16)
        assert f["title"] == ["곡"] and f.pictures
    if mode == "aac":
        assert f["\xa9ART"] == ["가수"] and f["covr"]


@needs_ffmpeg
def test_real_video_gets_title_tag(fake_ydl, tmp_path):
    from mutagen.mp4 import MP4
    fake_ydl.write = {"src.mp4": _tone(tmp_path, "mp4", "-c:a", "aac")}
    path = core.download(_info(), "video", 1080, "무시됨", "영상 제목", str(tmp_path / "out"))
    assert os.path.basename(path) == "영상 제목.mp4"
    tags = MP4(path)
    assert tags["\xa9nam"] == ["영상 제목"] and "\xa9ART" not in tags
