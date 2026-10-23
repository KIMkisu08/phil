"""다운로드·변환·태그 처리 (화면과 분리된 핵심 로직)."""
from __future__ import annotations

import functools
import io
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass

import yt_dlp
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TIT2, TPE1, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4Cover
from PIL import Image, ImageOps

# 음원 저장 방식: (화면 표시명, 내부 키). 첫 항목이 기본값
AUDIO_MODES = {
    "아이폰용 AAC 256kbps (.m4a)": "aac",
    "MP3 320kbps (차량/기타 기기)": "mp3",
    "CD 굽기용 FLAC (44.1kHz/16bit)": "cd",
}

BROWSERS = {"Firefox (권장)": "firefox", "Chrome": "chrome", "Edge": "edge", "사용 안 함": None}

# 윈도우에서 ffmpeg/pip 실행 시 콘솔 창이 뜨지 않게
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_NOISE = re.compile(
    r"[\(\[](?:[^\)\]]*?(?:official|lyric|lyrics|audio|video|m/?v|visualizer|가사|뮤직비디오|공식)[^\)\]]*?)[\)\]]",
    re.IGNORECASE,
)
_WIN_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class DownloadError(Exception):
    pass


@dataclass
class VideoInfo:
    url: str
    title: str
    channel: str
    duration: int
    thumbnail: str | None
    artist_guess: str
    title_guess: str
    best_audio: dict | None
    video_formats: dict[int, dict]      # 화질(짧은 변) → 그 화질에서 실제로 받을 영상 형식 (H.264 우선)
    thumb_bytes: bytes | None = None    # 미리보기·앨범아트가 같이 쓰는 썸네일 (한 번만 받음)

    @property
    def heights(self) -> list[int]:
        return sorted(self.video_formats, reverse=True)

    def is_iphone_ok(self, height: int) -> bool:
        return (self.video_formats[height].get("vcodec") or "").startswith("avc1")

    def default_height(self) -> int | None:
        """아이폰 호환(H.264) 최고 화질, 없으면 최고 화질."""
        return next((h for h in self.heights if self.is_iphone_ok(h)), next(iter(self.heights), None))

    def default_title(self, kind: str) -> str:
        """음원은 '아티스트 - 제목'에서 분리한 곡명, 영상은 원래 영상 제목 전체."""
        return self.title if kind == "video" else self.title_guess

    def thumbnail_bytes(self) -> bytes | None:
        if self.thumb_bytes is None and self.thumbnail:
            self.thumb_bytes = fetch_bytes(self.thumbnail)
        return self.thumb_bytes

    @property
    def is_premium_audio(self) -> bool:
        a = self.best_audio or {}
        return a.get("format_id", "").split("-")[0] == "774" or "premium" in (a.get("format_note") or "").lower()

    @property
    def audio_label(self) -> str:
        a = self.best_audio
        if not a:
            return "음원 정보 없음"
        abr = a.get("abr") or a.get("tbr") or 0
        tag = "Premium 고음질" if self.is_premium_audio else "일반 음질"
        return f"{tag} · {a.get('acodec', '?').split('.')[0]} {abr:.0f}kbps (id {a.get('format_id')})"


# ---------- 유틸 ----------

def ffmpeg_path() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise DownloadError("FFmpeg를 찾을 수 없습니다. 프로그램을 다시 실행하면 자동 설치를 안내합니다.")
    return path


def fetch_bytes(url: str, timeout: int = 15) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def safe_filename(name: str) -> str:
    name = _WIN_BAD.sub("_", name).strip().rstrip(". ")
    if not name:
        name = "제목없음"
    if name.split(".")[0].upper() in _WIN_RESERVED:
        name = "_" + name
    return name[:180]


def unique_path(folder: str, stem: str, ext: str) -> str:
    path = os.path.join(folder, f"{stem}.{ext}")
    n = 1
    while os.path.exists(path):
        path = os.path.join(folder, f"{stem} ({n}).{ext}")
        n += 1
    return path


def guess_artist_title(info: dict) -> tuple[str, str]:
    artist = info.get("artist") or ", ".join(info.get("artists") or [])
    track = info.get("track")
    if artist and track:
        return artist, track
    title = _NOISE.sub("", info.get("title") or "").strip()
    for sep in (" - ", " – ", " — ", " _ "):
        if sep in title:
            a, t = title.split(sep, 1)
            return a.strip(), t.strip()
    channel = (info.get("channel") or info.get("uploader") or "").removesuffix(" - Topic").strip()
    return channel, title


def is_youtube_url(url: str) -> bool:
    """주소 문자열이 아니라 실제 도메인으로 판단 (youtube.com / music.youtube.com / youtu.be 등)."""
    try:
        parts = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme in ("http", "https") and (
        host in ("youtube.com", "youtu.be") or host.endswith((".youtube.com", ".youtu.be")))


def _ydl_opts(browser: str | None, cookie_file: str | None) -> dict:
    if cookie_file and not os.path.isfile(cookie_file):
        raise DownloadError(f"쿠키 파일을 찾을 수 없습니다: {cookie_file}\n쿠키 파일을 다시 고르거나 해제하세요.")
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    if cookie_file:
        opts["cookiefile"] = cookie_file
    elif browser:
        opts["cookiesfrombrowser"] = (browser,)
    return opts


def _pick_best_audio(formats: list[dict]) -> dict | None:
    audio = [
        f for f in formats
        if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")
        and "drc" not in f.get("format_id", "")            # 음량 압축(DRC) 버전 제외
        and f.get("protocol", "").startswith("http")
    ]
    if not audio:
        return None
    return max(audio, key=lambda f: (f.get("language_preference") or 0, f.get("abr") or f.get("tbr") or 0))


def _res(f: dict) -> int:
    """짧은 변 기준 화질. 1080x1920 세로 영상(Shorts)도 1080으로 취급."""
    return min(f["height"], f.get("width") or f["height"])


def _pick_video_formats(formats: list[dict]) -> dict[int, dict]:
    """화질별로 받을 영상 형식 하나씩: 아이폰 호환 H.264 → 높은 fps → 높은 비트레이트 순."""
    def rank(f):
        return (f.get("vcodec") or "").startswith("avc1"), f.get("fps") or 0, f.get("tbr") or 0

    best: dict[int, dict] = {}
    for f in formats:
        if not f.get("height") or f.get("vcodec") in (None, "none") or f.get("acodec") not in (None, "none"):
            continue  # 소리가 섞인 형식은 제외 (소리는 따로 AAC로 합침)
        h = _res(f)
        if h not in best or rank(f) > rank(best[h]):
            best[h] = f
    return best


# ---------- 정보 불러오기 ----------

def fetch_info(url: str, browser: str | None = None, cookie_file: str | None = None) -> VideoInfo:
    if not is_youtube_url(url):
        raise DownloadError("유튜브 링크가 아닙니다.")
    try:
        with yt_dlp.YoutubeDL(_ydl_opts(browser, cookie_file)) as ydl:
            info = ydl.extract_info(url, download=False)
    except (yt_dlp.utils.YoutubeDLError, OSError) as e:   # 쿠키 읽기 실패(CookieLoadError) 포함
        raise DownloadError(_friendly(str(e))) from e
    if info.get("_type") == "playlist":
        raise DownloadError("재생목록 링크입니다. 곡 하나의 링크를 넣어주세요.")
    formats = info.get("formats") or []
    artist, title = guess_artist_title(info)
    return VideoInfo(
        url=info.get("webpage_url") or url,
        title=info.get("title") or "",
        channel=info.get("channel") or info.get("uploader") or "",
        duration=int(info.get("duration") or 0),
        thumbnail=info.get("thumbnail"),
        artist_guess=artist,
        title_guess=title,
        best_audio=_pick_best_audio(formats),
        video_formats=_pick_video_formats(formats),
    )


_NETWORK_HINTS = ("getaddrinfo", "name or service not known", "nodename nor servname", "timed out",
                  "connection reset", "connection refused", "connection aborted", "network is unreachable",
                  "no route to host", "remote end closed", "incompleteread", "tunnel connection failed",
                  "urlopen error", "http error 5")


def _friendly(msg: str) -> str:
    # yt-dlp 오류 앞뒤의 "ERROR:" 와 "버그 신고해 주세요" 안내는 사용자에게 의미가 없어 뺌
    msg = re.sub(r"^\s*ERROR:\s*", "", msg)
    msg = re.split(r";\s*please report this issue", msg, flags=re.IGNORECASE)[0].strip()
    low = msg.lower()
    if "confirm your age" in low or re.search(r"\bage\b.*restrict|restrict.*\bage\b", low):
        return "연령 제한 영상입니다. 로그인 쿠키(Firefox)를 켜고 다시 시도하세요."
    if "private" in low:
        return "비공개 영상입니다."
    if "cookie" in low and ("could not" in low or "failed" in low):
        return "브라우저 쿠키를 읽지 못했습니다. Firefox에서 유튜브에 로그인했는지 확인하세요.\n" + msg
    if any(h in low for h in _NETWORK_HINTS):
        return "인터넷 연결이 끊겼거나 유튜브에 접속하지 못했습니다. 연결을 확인하고 다시 시도하세요.\n" + msg
    if "unavailable" in low:
        return "볼 수 없는 영상입니다(삭제/지역 제한)."
    if "unsupported url" in low or "incomplete youtube id" in low or "is not a valid url" in low:
        return "링크를 이해하지 못했습니다. 영상 주소를 그대로 다시 복사해 붙여넣으세요."
    if "no space left" in low or "disk full" in low or "errno 28" in low:
        return "저장 공간이 부족합니다. 디스크 여유 공간을 확인하세요."
    if "permission denied" in low or "errno 13" in low:
        return "저장 위치에 쓸 권한이 없습니다. 다른 폴더를 고르세요.\n" + msg
    if "http error 403" in low:
        return "유튜브가 다운로드를 막았습니다. 아래 [다운로드 엔진(yt-dlp) 업데이트] 후 다시 실행해 보세요.\n" + msg
    return msg


# ---------- 다운로드 ----------

def download(
    info: VideoInfo,
    kind: str,                 # "audio" | "video"
    option: str | int,         # audio: AUDIO_MODES 값 / video: 화질(px)
    artist: str,
    title: str,
    out_dir: str,
    browser: str | None = None,
    cookie_file: str | None = None,
    progress=lambda pct, text: None,
) -> str:
    ffmpeg = ffmpeg_path()
    artist = "" if kind == "video" else artist.strip()   # 영상은 제목만 (파일명·태그 모두)
    title = title.strip()
    if not title:
        raise DownloadError("제목을 입력하세요.")
    if kind == "audio":
        if not info.best_audio:
            raise DownloadError("받을 수 있는 음원이 없습니다.")
        if option not in AUDIO_MODES.values():
            raise DownloadError(f"알 수 없는 음원 방식: {option}")
        fmt = info.best_audio["format_id"]
    else:
        vf = info.video_formats.get(option)
        if not vf:
            raise DownloadError("선택한 화질을 찾을 수 없습니다.")
        fmt = f"{vf['format_id']}+ba[acodec^=mp4a]/{vf['format_id']}+ba"   # 소리는 아이폰 호환 AAC 우선
    stem = safe_filename(f"{artist} - {title}" if artist else title)
    out_dir = out_dir.strip()
    if not out_dir:
        raise DownloadError("저장 위치를 입력하세요.")
    try:
        os.makedirs(out_dir, exist_ok=True)
        return _download_to(out_dir, stem, info, kind, option, fmt, artist, title, ffmpeg,
                            _ydl_opts(browser, cookie_file), progress)
    except OSError as e:   # 폴더 만들기·임시 파일·이동 실패 (없는 드라이브, 권한, 디스크 부족 등)
        raise DownloadError(_friendly(f"파일을 저장하지 못했습니다 ({out_dir}): {e}")) from e


def _download_to(out_dir, stem, info, kind, option, fmt, artist, title, ffmpeg, opts, progress) -> str:
    # 저장 폴더 안에 임시 폴더: 저장 폴더가 다른 드라이브여도 마지막 이동이 즉시 끝남
    # (백신 등이 임시 파일을 잡고 있어 정리에 실패해도, 이미 저장된 결과를 오류로 만들지 않음)
    with tempfile.TemporaryDirectory(prefix=".ytdl_", dir=out_dir, ignore_cleanup_errors=True) as tmp:
        opts.update({
            "format": fmt,
            "outtmpl": os.path.join(tmp, "src.%(ext)s"),
            "ffmpeg_location": ffmpeg,
            "progress_hooks": [_hook(progress)],
        })
        if kind == "video":
            opts["merge_output_format"] = "mp4"
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([info.url])
        except yt_dlp.utils.YoutubeDLError as e:
            raise DownloadError(_friendly(str(e))) from e

        src = next((os.path.join(tmp, f) for f in sorted(os.listdir(tmp))
                    if f.startswith("src.") and not f.endswith((".part", ".ytdl"))), None)
        if not src:
            raise DownloadError("다운로드한 파일을 찾지 못했습니다.")
        progress(100, "변환 및 태그 기록 중…")

        if kind == "video":
            out, ext, cover = src, "mp4", None   # 영상은 제목 태그만
        else:
            ext, codec = _audio_args(option, info, ffmpeg)
            out = os.path.join(tmp, f"out.{ext}")
            _ffmpeg(ffmpeg, ["-i", src, "-vn", "-map_metadata", "-1", *codec, out])
            cover = _square_cover(info.thumbnail_bytes())

        # 태그를 먼저 쓰고 옮김: 태그 기록이 실패해도 저장 폴더에 반쯤 된 파일이 남지 않음
        tag_file(out, artist, title, cover)
        final = unique_path(out_dir, stem, ext)
        shutil.move(out, final)
        return final


def _audio_args(mode: str, info: VideoInfo, ffmpeg: str) -> tuple[str, list[str]]:
    """음원 방식별 (확장자, ffmpeg 인코딩 인자)."""
    if mode == "aac":
        # 원본이 이미 AAC면 재인코딩 없이 그대로 (추가 손실 방지)
        if (info.best_audio.get("acodec") or "").startswith("mp4a"):
            return "m4a", ["-c:a", "copy", "-movflags", "+faststart"]
        return "m4a", ["-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart"]
    if mode == "mp3":
        return "mp3", ["-c:a", "libmp3lame", "-b:a", "320k"]
    return "flac", ["-af", _cd_resample(ffmpeg), "-c:a", "flac", "-compression_level", "8"]


@functools.cache
def _cd_resample(ffmpeg: str) -> str:
    """CD 규격(44.1kHz/16bit) 변환. 가능하면 고품질 soxr 리샘플러 + 디더링."""
    try:
        conf = subprocess.run([ffmpeg, "-hide_banner", "-buildconf"], capture_output=True, text=True,
                              creationflags=NO_WINDOW).stdout
    except OSError:
        conf = ""
    if "soxr" in conf:
        return "aresample=44100:resampler=soxr:precision=28:osf=s16:dither_method=triangular"
    return "aresample=44100:filter_size=64:cutoff=0.97:osf=s16:dither_method=triangular"


def _ffmpeg(ffmpeg: str, args: list[str]) -> None:
    r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise DownloadError("FFmpeg 변환 실패:\n" + r.stderr[-800:])


def _hook(progress):
    def hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            pct = done / total * 100 if total else 0
            speed = d.get("speed") or 0
            eta = d.get("eta")
            text = f"{done / 1e6:.1f}/{total / 1e6:.1f}MB · {speed / 1e6:.1f}MB/s"
            if eta is not None:
                text += f" · 남은 시간 {int(eta)}초"
            progress(min(pct, 99.0), text)
        elif d["status"] == "finished":
            progress(99, "다운로드 완료, 처리 중…")
    return hook


# ---------- 앨범아트 / 태그 ----------

def _square_cover(data: bytes | None) -> bytes | None:
    """16:9 썸네일 가운데를 정사각형으로 잘라 앨범아트(JPEG)로."""
    if not data:
        return None
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None
    s = min(img.size)
    buf = io.BytesIO()
    ImageOps.fit(img, (s, s)).save(buf, "JPEG", quality=95)
    return buf.getvalue()


def tag_file(path: str, artist: str, title: str, cover: bytes | None) -> None:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("APIC")
        tags.add(TIT2(encoding=3, text=title))
        if artist:
            tags.add(TPE1(encoding=3, text=artist))
        if cover:
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover))
        tags.save(path, v2_version=3)  # v2.3: 윈도우 탐색기/구형 기기 호환
        return

    f = MP4(path) if ext in (".m4a", ".mp4") else FLAC(path)
    mp4 = isinstance(f, MP4)
    title_key, artist_key = ("\xa9nam", "\xa9ART") if mp4 else ("title", "artist")
    f[title_key] = [title]
    if artist:
        f[artist_key] = [artist]
    if cover and mp4:
        f["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
    elif cover:
        pic = Picture()
        pic.type, pic.mime, pic.data = 3, "image/jpeg", cover
        f.clear_pictures()
        f.add_picture(pic)
    f.save()
