# 유튜브 음원 다운로더 (YouTubeDownloader)

유튜브 링크를 넣으면 음원(AAC·MP3·FLAC)이나 영상(MP4)으로 받아 주는 Windows 프로그램입니다.
자세한 사용법은 [사용법.txt](사용법.txt)를 보세요.

## 다운로드

1. [Releases 페이지](https://github.com/KIMkisu08/phil/releases)에서 이름이 **유튜브 음원 다운로더**로 시작하는 최신 릴리스를 엽니다.
2. **YouTubeDownloader_exe.zip**을 받아 압축을 풉니다.
3. `YouTubeDownloader.exe`를 더블클릭합니다.
   "Windows의 PC 보호" 창이 뜨면 [추가 정보] → [실행]을 누르세요. 서명이 없는 개인 프로그램이라 뜨는 경고입니다.

새 버전이 나오면 프로그램을 켤 때 알려 주고, [예]를 누르면 이 페이지가 열립니다.
다운로드가 갑자기 안 될 때는 프로그램 아래의 **다운로드 엔진(yt-dlp) 업데이트** 버튼을 먼저 눌러 보세요.

## 새 버전 내보내기 (개발용)

`ytd-v` 로 시작하는 태그를 푸시하면 GitHub Actions가 Windows에서 exe를 빌드해 릴리스에 올립니다.

```
git tag ytd-v1.0.0
git push origin ytd-v1.0.0
```

PR에서 이 폴더를 바꾸면 빌드만 확인하고, 결과 exe는 해당 실행의 Artifacts에서 받을 수 있습니다.
직접 빌드하려면 Windows에서 `빌드.bat`을 실행하세요 (결과: `dist\YouTubeDownloader.exe`).
