"""테스트 공통 설정: YouTubeDownloader 폴더의 모듈(core, updater)을 바로 import."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
