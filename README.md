# 블랙이글 - 예제 홈페이지

간단한 Flask 기반 예제 홈페이지입니다.

로컬에서 실행하는 방법 (macOS, zsh):

```bash
# 가상환경 생성 (권장)
python3 -m venv .venv
source .venv/bin/activate

# 의존성 설치
pip install -r requirements.txt

# 앱 실행 (기본은 PORT=5001 — 만약 5000 포트가 이미 사용중이면 다른 포트로 지정)
# 예: 기본(5001)
PORT=5001 python3 app.py

# 또는 원하는 포트로 실행
PORT=5002 python3 app.py
```

브라우저에서 http://127.0.0.1:5001/ 로 접속하세요 (또는 사용한 포트 번호로 접속).

참고: 일부 macOS 시스템 서비스가 5000 포트를 점유할 수 있어, 기본 실행 포트를 5001로 변경했습니다.

원하시면 디자인과 내용을 한국어로 더 맞춰드릴게요.
