import os

# Render에서 PORT 환경변수를 자동 제공 (기본 10000)
bind = "0.0.0.0:" + os.environ.get("PORT", "10000")

# 워커 설정
workers = 2
worker_class = "sync"
timeout = 120          # 워커 타임아웃 (기본 30초 → 120초)
graceful_timeout = 30

# 로깅
accesslog = "-"
errorlog = "-"
loglevel = "info"

# 앱 초기화 시 preload로 한 번만 실행
preload_app = True
