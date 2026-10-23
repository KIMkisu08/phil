"""실행 환경 준비: 최신 yt-dlp 사용, FFmpeg/Deno 확인·설치 (exe·소스 실행 공통)."""
from __future__ import annotations

import glob
import hashlib
import importlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "YouTubeDownloader")
LIB_DIR = os.path.join(APP_DIR, "lib")

RELEASES_API = "https://api.github.com/repos/KIMkisu08/phil/releases?per_page=30"
RELEASE_TAG_PREFIX = "ytd-v"   # 이 프로그램의 릴리스 태그 (저장소의 다른 릴리스와 구분)

ENGINE_PACKAGES = ("yt-dlp", "yt-dlp-ejs")   # 다운로드 엔진 + 유튜브 해석 스크립트
TOOLS = {"ffmpeg": "Gyan.FFmpeg", "deno": "DenoLand.Deno"}   # 실행 파일 → winget ID

# 업데이트 파일 이름: <패키지>-<버전>.whl  (예: yt_dlp-2026.10.21.whl)
_WHEEL_RE = re.compile(r"^(yt_dlp(?:_ejs)?)-([\d.]+)\.whl$")


def _vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def _bundled_version() -> str:
    try:
        return importlib.metadata.version("yt-dlp")
    except importlib.metadata.PackageNotFoundError:
        return "0"


def _override_wheels() -> list[str]:
    """패키지별 최신 업데이트 파일만 남기고 나머지는 정리. 내장 엔진보다 새로울 때만 사용."""
    newest: dict[str, tuple[tuple[int, ...], str]] = {}
    for path in glob.glob(os.path.join(LIB_DIR, "*.whl")):
        m = _WHEEL_RE.match(os.path.basename(path))
        if not m:
            continue
        pkg, ver = m.group(1), _vtuple(m.group(2))
        if pkg in newest and newest[pkg][0] >= ver:
            _remove(path)
            continue
        if pkg in newest:
            _remove(newest[pkg][1])
        newest[pkg] = (ver, path)
    if "yt_dlp" not in newest or newest["yt_dlp"][0] <= _vtuple(_bundled_version()):
        return []   # 내장 엔진이 같거나 더 새로움 (새 exe로 교체한 경우 등)
    return [path for _, path in newest.values()]


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _refresh_path() -> None:
    """winget 설치 직후에도 ffmpeg/deno를 찾도록 레지스트리의 최신 PATH와 winget 링크 폴더를 반영."""
    dirs = [os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links"),
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "WinGet", "Links")]
    try:
        import winreg
        for root, key in ((winreg.HKEY_CURRENT_USER, "Environment"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
            try:
                with winreg.OpenKey(root, key) as k:
                    dirs += os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]).split(os.pathsep)
            except OSError:
                pass
    except ImportError:   # 윈도우가 아닐 때 (개발용)
        pass
    current = os.environ.get("PATH", "").split(os.pathsep)
    extra = [d for d in dirs if d and os.path.isdir(d) and d not in current]
    if extra:
        os.environ["PATH"] = os.pathsep.join(current + extra)


def prepare() -> None:
    """core(yt_dlp) import 전에 호출."""
    _refresh_path()
    wheels = _override_wheels()
    if not wheels:
        return
    sys.path[:0] = wheels
    try:
        importlib.import_module("yt_dlp")   # 업데이트한 엔진이 정상인지 미리 확인
    except Exception:
        # 손상·비호환 업데이트면 버리고 내장 엔진으로 실행 (프로그램이 안 켜지는 일 방지)
        for p in wheels:
            sys.path.remove(p)
            _remove(p)
        for name in [m for m in sys.modules if m.split(".")[0] in ("yt_dlp", "yt_dlp_ejs")]:
            del sys.modules[name]


def missing_tools() -> list[str]:
    return [t for t in TOOLS if not shutil.which(t)]


def install_tools(tools: list[str]) -> list[str]:
    """winget으로 설치 (진행 상황이 보이도록 콘솔 창 표시). 설치 후에도 없는 도구를 돌려줌."""
    if not shutil.which("winget"):
        raise RuntimeError("winget이 없습니다. Microsoft Store에서 '앱 설치 관리자'를 설치한 뒤 다시 시도하세요.")
    failed = []
    for t in tools:
        r = subprocess.run(["winget", "install", "-e", "--id", TOOLS[t],
                            "--accept-source-agreements", "--accept-package-agreements"],
                           creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        if r.returncode != 0:
            failed.append(f"{t} (winget 오류 코드 {r.returncode:#x})")
    _refresh_path()
    left = missing_tools()
    if failed and left:
        raise RuntimeError("설치 실패: " + ", ".join(failed))
    return left


def update_engine() -> str:
    """PyPI에서 최신 엔진 wheel을 받아 해시 검증 후 저장. 다음 실행부터 적용.
    실행 중인 파일은 덮어쓰지 않고 버전별 새 파일로 저장 (옛 파일은 다음 실행 때 정리)."""
    os.makedirs(LIB_DIR, exist_ok=True)
    done = []
    try:
        for pkg in ENGINE_PACKAGES:
            with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=20) as r:
                meta = json.load(r)
            ver = meta["info"]["version"]
            whl = next((u for u in meta["urls"] if u["packagetype"] == "bdist_wheel"), None)
            if not whl:
                raise RuntimeError(f"{pkg} {ver} 설치 파일이 아직 준비되지 않았습니다. 잠시 후 다시 시도하세요.")
            dest = os.path.join(LIB_DIR, f"{pkg.replace('-', '_')}-{ver}.whl")
            if not os.path.exists(dest):
                with urllib.request.urlopen(whl["url"], timeout=120) as r:
                    data = r.read()
                if hashlib.sha256(data).hexdigest() != whl["digests"]["sha256"]:
                    raise RuntimeError(f"{pkg} 파일 검증 실패 (해시 불일치). 다시 시도하세요.")
                with open(dest + ".tmp", "wb") as f:
                    f.write(data)
                os.replace(dest + ".tmp", dest)
            done.append(f"{pkg} {ver}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"인터넷 연결을 확인하세요. ({e.reason})") from e
    return ", ".join(done)


def check_app_update(current: str) -> tuple[str, str] | None:
    """GitHub 릴리스에 더 새 exe가 있으면 (버전, 릴리스 페이지 주소). 없거나 확인 실패면 None."""
    if not re.fullmatch(r"\d+(\.\d+)*", current):
        return None   # "dev" 등 직접 빌드한 실행 파일
    try:
        req = urllib.request.Request(RELEASES_API, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            releases = json.load(r)
    except (OSError, ValueError):
        return None   # 오프라인 등: 조용히 넘어감
    best = None
    for rel in releases:
        tag = rel.get("tag_name") or ""
        if rel.get("draft") or rel.get("prerelease") or not tag.startswith(RELEASE_TAG_PREFIX):
            continue
        ver = tag[len(RELEASE_TAG_PREFIX):]
        if best is None or _vtuple(ver) > _vtuple(best[0]):
            best = (ver, rel.get("html_url") or "")
    if best and _vtuple(best[0]) > _vtuple(current):
        return best
    return None
