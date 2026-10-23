"""유튜브 음원·영상 다운로더 (개인용)."""
from __future__ import annotations

import io
import json
import os
import queue
import sys
import threading
import webbrowser
from tkinter import filedialog, messagebox

import updater

updater.prepare()   # 최신 엔진·ffmpeg 경로 준비 (yt_dlp import 전에)

import customtkinter as ctk  # noqa: E402
import yt_dlp.version  # noqa: E402
from PIL import Image  # noqa: E402
from yt_dlp.utils import formatSeconds  # noqa: E402

import core  # noqa: E402
from version import __version__  # noqa: E402

ICON = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "icon.ico")
SETTINGS = os.path.join(os.path.expanduser("~"), ".ytdl_settings.json")
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Music", "YouTube")
ROW_PAD = {"fill": "x", "padx": 16, "pady": (8, 0)}
HINT_GRAY, HINT_WARN = "gray60", "#d9822b"


def load_settings() -> dict:
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"유튜브 다운로더 {__version__} (엔진 {yt_dlp.version.__version__})")
        self.geometry("640x620")
        self.minsize(600, 600)
        ctk.set_appearance_mode("system")

        self.s = load_settings()
        self.info: core.VideoInfo | None = None
        self.titles: dict[str, str] = {}     # 형식(음원/영상)별 제목 칸 내용
        self.shown_kind: str | None = None
        self.q: queue.Queue = queue.Queue()
        self.busy = False

        # ① 링크
        row, _ = self._row()
        self.url = ctk.CTkEntry(row, placeholder_text="유튜브 링크를 붙여넣으세요 (Ctrl+V)")
        self.url.pack(side="left", fill="x", expand=True)
        self.url.bind("<Return>", lambda e: self.on_fetch())
        self.fetch_btn = ctk.CTkButton(row, text="불러오기", width=90, command=self.on_fetch)
        self.fetch_btn.pack(side="left", padx=(8, 0))

        # 로그인(Premium) 설정
        row, _ = self._row("Premium 로그인:", width=0)
        self.browser = ctk.CTkOptionMenu(row, values=list(core.BROWSERS), width=150)
        saved = self.s.get("browser")
        self.browser.set(saved if saved in core.BROWSERS else next(iter(core.BROWSERS)))
        self.browser.pack(side="left", padx=8)
        self.cookie_file = self.s.get("cookie_file") or ""
        self.cookie_btn = ctk.CTkButton(row, text=self._cookie_text(), width=150, fg_color="gray40",
                                        command=self.on_cookie_file)
        self.cookie_btn.pack(side="left")

        # 영상 정보
        box = ctk.CTkFrame(self)
        box.pack(**ROW_PAD)
        self.thumb = ctk.CTkLabel(box, text="", width=160, height=90)
        self.thumb.pack(side="left", padx=8, pady=8)
        self.meta = ctk.CTkLabel(box, text="링크를 넣고 [불러오기]를 누르세요.", justify="left",
                                 anchor="w", wraplength=400)
        self.meta.pack(side="left", fill="x", expand=True, padx=8)

        # ② 형식
        row, _ = self._row("형식:")
        self.kind = ctk.StringVar(value=self.s.get("kind", "audio"))
        ctk.CTkRadioButton(row, text="음원", variable=self.kind, value="audio",
                           command=self.refresh_options).pack(side="left", padx=8)
        ctk.CTkRadioButton(row, text="영상 (MP4)", variable=self.kind, value="video",
                           command=self.refresh_options).pack(side="left", padx=8)

        # ③ 음질/화질
        row, self.opt_label = self._row("음질:")
        self.option = ctk.CTkOptionMenu(row, values=["-"], width=300, command=lambda _: self.update_hint())
        self.option.pack(side="left", padx=8)
        self.hint = ctk.CTkLabel(self, text="", text_color=HINT_GRAY, anchor="w", justify="left", wraplength=600)
        self.hint.pack(fill="x", padx=16, pady=(4, 0))

        # ④ 아티스트 / 제목 (영상은 제목만)
        self.artist_row, _, self.artist = self._field("아티스트:")
        self.song_row, self.song_label, self.song = self._field("곡 제목:")

        # ⑤ 저장 위치
        row, _ = self._row("저장 위치:")
        self.out_dir = ctk.CTkEntry(row)
        self.out_dir.insert(0, self.s.get("out_dir", DEFAULT_DIR))
        self.out_dir.pack(side="left", fill="x", expand=True, padx=8)
        ctk.CTkButton(row, text="변경", width=60, command=self.on_pick_dir).pack(side="left")
        ctk.CTkButton(row, text="열기", width=60, fg_color="gray40", command=self.on_open_dir).pack(side="left", padx=(6, 0))

        # 다운로드 + 진행률
        self.dl_btn = ctk.CTkButton(self, text="다운로드", height=42, state="disabled", command=self.on_download)
        self.dl_btn.pack(fill="x", padx=16, pady=(16, 0))
        self.bar = ctk.CTkProgressBar(self)
        self.bar.set(0)
        self.bar.pack(fill="x", padx=16, pady=(10, 0))
        self.status = ctk.CTkLabel(self, text="", anchor="w")
        self.status.pack(fill="x", padx=16)

        ctk.CTkButton(self, text="다운로드 엔진(yt-dlp) 업데이트", fg_color="gray30", height=26,
                      command=self.on_update).pack(side="bottom", pady=10)

        self.refresh_options()
        self.after(100, self.poll)
        self.after(600, self.check_tools)
        threading.Thread(target=lambda: self.q.put(("update", updater.check_app_update(__version__))),
                         daemon=True).start()
        if sys.platform == "win32" and os.path.exists(ICON):   # customtkinter 기본 아이콘을 덮어쓴 뒤에 적용
            self.after(250, lambda: self.iconbitmap(ICON))

    # ---------- 화면 구성 도우미 ----------
    def _row(self, label: str | None = None, width: int = 70) -> tuple[ctk.CTkFrame, ctk.CTkLabel | None]:
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(**ROW_PAD)
        lab = None
        if label:
            lab = ctk.CTkLabel(row, text=label, width=width, anchor="w")
            lab.pack(side="left")
        return row, lab

    def _field(self, label: str) -> tuple[ctk.CTkFrame, ctk.CTkLabel, ctk.CTkEntry]:
        row, lab = self._row(label)
        e = ctk.CTkEntry(row)
        e.pack(side="left", fill="x", expand=True, padx=8)
        return row, lab, e

    def _cookie_text(self) -> str:
        return "쿠키 파일: " + (os.path.basename(self.cookie_file) if self.cookie_file else "없음")

    def _auth(self) -> tuple[str | None, str | None]:
        return core.BROWSERS.get(self.browser.get()), (self.cookie_file or None)

    def _selected_height(self) -> int | None:
        sel = self.option.get().rstrip("p")
        return int(sel) if sel.isdigit() else None

    def _can_download(self) -> bool:
        return bool(self.info) and not self.busy and (self.kind.get() == "audio" or bool(self.info.heights))

    def refresh_options(self):
        kind = self.kind.get()
        if kind == "audio":
            self.opt_label.configure(text="음질:")
            values = list(core.AUDIO_MODES)
            saved = next((k for k, v in core.AUDIO_MODES.items() if v == self.s.get("audio_mode")), values[0])
            self.option.configure(values=values)
            self.option.set(saved)
            self.song_label.configure(text="곡 제목:")
            if not self.artist_row.winfo_manager():
                self.artist_row.pack(**ROW_PAD, before=self.song_row)
        else:
            self.opt_label.configure(text="화질:")
            heights = self.info.heights if self.info else []
            values = [f"{h}p" for h in heights] or ["-"]
            saved = self.s.get("video_res")
            default = saved if saved in heights else (self.info.default_height() if self.info else None)
            self.option.configure(values=values)
            self.option.set(f"{default}p" if default else values[0])
            self.song_label.configure(text="제목:")
            self.artist_row.pack_forget()

        # 제목 칸은 형식별로 따로 기억 (음원: 곡명 / 영상: 영상 제목 전체)
        if self.shown_kind and self.shown_kind != kind:
            self.titles[self.shown_kind] = self.song.get()
        if self.info and self.shown_kind != kind:
            self.song.delete(0, "end")
            self.song.insert(0, self.titles.get(kind, ""))
        self.shown_kind = kind
        self.update_hint()
        self.dl_btn.configure(state="normal" if self._can_download() else "disabled")

    def update_hint(self):
        text, color = "", HINT_GRAY
        if self.kind.get() == "audio":
            text = ("· 아이폰용 AAC: 음악 앱에서 바로 재생, 같은 용량 대비 MP3보다 고음질\n"
                    "· CD 굽기용 FLAC: CD 규격으로 한 번만 변환해 음질 손실 최소화 (아이폰 음악 앱 재생 불가)")
        elif not self.info:
            text = "· 링크를 불러오면 이 영상에 있는 화질이 표시됩니다."
        elif (h := self._selected_height()) is None:
            text, color = "⚠ 이 영상은 받을 수 있는 화질이 없습니다. 음원으로 받아주세요.", HINT_WARN
        elif not self.info.is_iphone_ok(h):
            text, color = f"⚠ {h}p는 VP9/AV1 형식뿐이라 아이폰에서 재생되지 않을 수 있습니다 (노트북 감상용).", HINT_WARN
        else:
            text = "· 아이폰 호환 H.264 형식으로 받습니다. 파일명은 제목만 사용합니다."
        self.hint.configure(text=text, text_color=color)

    # ---------- 동작 ----------
    def on_cookie_file(self):
        path = filedialog.askopenfilename(title="cookies.txt 선택 (취소하면 해제)", filetypes=[("텍스트", "*.txt")])
        self.cookie_file = path or ""
        self.cookie_btn.configure(text=self._cookie_text())

    def on_pick_dir(self):
        d = filedialog.askdirectory(initialdir=self.out_dir.get() or DEFAULT_DIR)
        if d:
            self.out_dir.delete(0, "end")
            self.out_dir.insert(0, d)

    def on_open_dir(self):
        d = self.out_dir.get().strip()
        if os.path.isdir(d):
            os.startfile(d)

    def on_fetch(self):
        url = self.url.get().strip()
        if not url or self.busy:
            return
        self.set_busy(True, "정보 불러오는 중…")
        browser, cookie = self._auth()
        self.run_bg(lambda: ("info", core.fetch_info(url, browser, cookie)))

    def on_download(self):
        if not self._can_download() or not self.check_tools():
            return
        kind = self.kind.get()
        if kind == "audio":
            option, artist = core.AUDIO_MODES[self.option.get()], self.artist.get()
            self.s["audio_mode"] = option
        else:
            option, artist = self._selected_height(), ""   # 영상은 제목만
            self.s["video_res"] = option
        title, out_dir = self.song.get(), self.out_dir.get().strip()
        browser, cookie = self._auth()
        self.save_settings()
        self.set_busy(True, "다운로드 시작…")
        info = self.info

        def job():
            path = core.download(info, kind, option, artist, title, out_dir, browser, cookie,
                                 progress=lambda p, t: self.q.put(("progress", (p, t))))
            return "done", path
        self.run_bg(job)

    def check_tools(self) -> bool:
        """필수 프로그램이 모두 있으면 True. 없으면 설치를 안내하고 False."""
        missing = updater.missing_tools()
        if not missing:
            return True
        if self.busy:
            return False
        names = ", ".join({"ffmpeg": "FFmpeg(변환)", "deno": "Deno(유튜브 해석)"}[t] for t in missing)
        if not messagebox.askyesno("필수 프로그램 설치",
                                   f"다음 프로그램이 필요합니다: {names}\n\n지금 자동으로 설치할까요? (설치 창이 잠시 열립니다)"):
            self.status.configure(text="⚠ 필수 프로그램이 없어 다운로드할 수 없습니다.")
            return False
        self.set_busy(True, "필수 프로그램 설치 중… (설치 창이 닫힐 때까지 기다려주세요)")

        def job():
            left = updater.install_tools(missing)
            if left:
                raise core.DownloadError(f"설치되지 않았습니다: {', '.join(left)}\n프로그램을 다시 실행하거나 PC를 재시작해 보세요.")
            return "msg", "필수 프로그램 설치 완료"
        self.run_bg(job)
        return False

    def on_update(self):
        if self.busy:
            return
        self.set_busy(True, "다운로드 엔진 업데이트 중…")

        def job():
            return "msg", f"업데이트 완료 ({updater.update_engine()}). 프로그램을 다시 실행하면 적용됩니다."
        self.run_bg(job)

    def run_bg(self, fn):
        def wrap():
            try:
                self.q.put(fn())
            except (core.DownloadError, RuntimeError) as e:   # 사용자에게 보여줄 안내 메시지
                self.q.put(("error", str(e)))
            except Exception as e:  # 예상 못 한 오류도 화면에 표시
                self.q.put(("error", f"{type(e).__name__}: {e}"))
        threading.Thread(target=wrap, daemon=True).start()

    def poll(self):
        latest_progress = None   # 진행률은 마지막 값만 반영 (화면 갱신 최소화)
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "progress":
                    latest_progress = data
                elif kind == "info":
                    self.show_info(data)
                elif kind == "thumb" and data:
                    self.thumb_img = ctk.CTkImage(light_image=data, dark_image=data, size=data.size)
                    self.thumb.configure(image=self.thumb_img, text="")
                elif kind == "done":
                    latest_progress = None
                    self.set_busy(False, "완료: " + os.path.basename(data))
                    self.bar.set(1)
                elif kind == "msg":
                    self.set_busy(False, data)
                elif kind == "update" and data:
                    ver, url = data
                    if messagebox.askyesno("새 버전", f"새 버전 {ver}이(가) 나왔습니다 (현재 {__version__}).\n\n"
                                           "다운로드 페이지를 열까요?"):
                        webbrowser.open(url)
                elif kind == "error":
                    self.set_busy(False, "오류")
                    messagebox.showerror("오류", data)
        except queue.Empty:
            pass
        if latest_progress and self.busy:
            pct, text = latest_progress
            self.bar.set(pct / 100)
            self.status.configure(text=text)
        self.after(100, self.poll)

    def show_info(self, info: core.VideoInfo):
        self.info = info
        warn = "" if info.is_premium_audio else "\n※ Premium 음질이 아닙니다 — 로그인 브라우저 설정을 확인하세요."
        self.meta.configure(text=f"{info.title}\n채널: {info.channel} · {formatSeconds(info.duration)}\n"
                                 f"음원: {info.audio_label}{warn}")
        self.thumb.configure(image=None)
        self.artist.delete(0, "end")
        self.artist.insert(0, info.artist_guess)
        self.titles = {k: info.default_title(k) for k in ("audio", "video")}
        self.shown_kind = None   # 다음 refresh_options에서 현재 형식의 제목을 채움
        self.busy = False
        self.refresh_options()
        self.set_busy(False, "형식과 음질/화질을 고르고 제목을 확인한 뒤 [다운로드]")
        self.bar.set(0)
        if info.thumbnail:
            def load():
                data = info.thumbnail_bytes()   # 앨범아트에도 그대로 재사용
                if not data:
                    return "thumb", None
                img = Image.open(io.BytesIO(data))
                img.thumbnail((160, 90))
                return "thumb", img
            self.run_bg(load)

    def set_busy(self, busy: bool, text: str):
        self.busy = busy
        self.status.configure(text=text)
        self.fetch_btn.configure(state="disabled" if busy else "normal")
        self.dl_btn.configure(state="normal" if self._can_download() else "disabled")

    def save_settings(self):
        self.s.update({"browser": self.browser.get(), "cookie_file": self.cookie_file,
                       "kind": self.kind.get(), "out_dir": self.out_dir.get().strip()})
        try:
            with open(SETTINGS, "w", encoding="utf-8") as f:
                json.dump(self.s, f, ensure_ascii=False, indent=2)
        except OSError:
            pass


if __name__ == "__main__":
    App().mainloop()
