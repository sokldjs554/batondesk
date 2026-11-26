# 공개 데모용 이미지. 한 컨테이너에 API·웹 화면·예시 주문 시스템이 모두 들어 있다.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

COPY kb ./kb
COPY prompts ./prompts
COPY data ./data
COPY web ./web
COPY eval_results ./eval_results

# 설치된 패키지가 아니라 /app 의 자료를 읽게 한다.
# CI 는 --build-arg GIT_SHA 로 커밋을 넣는다. Render 는 넣지 않으므로 비워 두고, 실행할 때 RENDER_GIT_COMMIT 을 읽는다.
ARG GIT_SHA=""
ENV BATONDESK_GIT_SHA=${GIT_SHA} \
    BATONDESK_KB_DIR=/app/kb \
    BATONDESK_PROMPTS_DIR=/app/prompts \
    BATONDESK_DATA_DIR=/app/data \
    BATONDESK_WEB_DIR=/app/web \
    BATONDESK_DB_PATH=/tmp/batondesk.sqlite3 \
    PORT=8000

RUN useradd --create-home app && chown -R app /app
USER app
EXPOSE 8000
CMD ["sh", "-c", "batondesk serve --port ${PORT}"]
