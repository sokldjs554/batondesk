# 공고 대조표 — 센드버드 AI Agent Software Engineer, Intern

> 모든 주문·고객·정책 데이터는 가상의 예시 쇼핑몰 "바통마켓"의 **예시 데이터**입니다.

- 공고: https://job-boards.greenhouse.io/get-delight/jobs/8639423002 (게시 2026-08-12, 회사 표기 delight.ai, 서울, 인턴 8~12주)
- 공고 원문을 2026-11-26에 다시 내려받아 아래 문장을 옮겼다.

**상태 표시**

| 표시 | 뜻 |
|---|---|
| ✅ 구현·확인 | 코드가 있고, 모델 호출 없이 확인할 수 있는 범위에서 테스트로 확인했다 |
| 🟡 구현, 실측 전 | 코드와 준비물은 있지만 실제 모델·CI·배포로 실행해 보지 못했다 |
| 🔎 검토만 | 조사·비교만 했다. 쓴 코드가 없다 |
| ❌ 안 함 | 하지 않았다 |

🟡 항목은 **경험으로 내세울 수 없다.** 실행한 뒤에야 ✅가 된다(`docs/how-this-was-built.md` 의 체크리스트).

## 주요 업무 (이런 일을 하실 수 있어요)

| 공고 문장 | 상태 | 이 저장소에서 | 근거 |
|---|---|---|---|
| 에이전트 프로토타이핑 및 개발 | 🟡 | 검색 → 모델·도구 반복 → 근거 검사 → 재작성/인계의 한 턴 그래프 | `src/batondesk/agent/graph.py`, `tests/test_agent_graph.py`(대역으로 분기 확인). 실제 모델로는 실행 전 |
| 프롬프트 엔지니어링으로 Hallucination 탐지 … 마이크로 API 개발 | 🟡 | `POST /v1/checks/grounding`: 답변을 주장 단위로 판정(규칙 + LLM). 에이전트와 무관하게 단독 호출 가능 | `src/batondesk/checks/grounding.py`, `prompts/grounding/v1.yaml`, `docs/api.md`. 규칙 부분은 측정됨(재현율 30%, `docs/evaluation.md` 4절). LLM 판정의 정확도는 실측 전 |
| … 대화 Resolution 등 마이크로 API 개발 | 🟡 | `POST /v1/checks/resolution`: 확인된 해결 / 가정된 해결 / 미해결 / 고객 답 대기 / 해당 없음 / 인계 | `src/batondesk/checks/resolution.py`, `prompts/resolution/v1.yaml`. 정확도 실측 전 |
| 내부 데이터셋과 도구를 활용해 AI Agent의 정확도를 테스트하고 검증 | 🟡 | 평가 자료 284문항(개발용·평가용 분리, 평가용 동결), 채점기, 반복 실행기, 문항 단위 신뢰구간, 실행 간 비교 | `data/evals/`, `src/batondesk/evals/`, `docs/evaluation.md`. 검색·채점기 점검은 측정됨. **에이전트 정확도는 실측 전** |
| RAG, 지식 기반 및 맞춤형 툴체인을 지원하는 백엔드 API를 통합 | ✅ / 🟡 | 지식 문서 23건과 검색 API, 도구 7개와 예시 주문 시스템 API, 도구 실행기의 안전장치 | `src/batondesk/kb/`, `tools/`, `commerce/`, `tests/test_kb.py`·`test_tools.py`·`test_commerce.py`. 검색 품질 측정됨(평가용 5위 내 77.5%). 모델이 도구를 실제로 고르는 품질은 실측 전 |
| 다양한 사용 사례에 맞는 프롬프트를 제작, 테스트 및 세부 조정 | 🟡 | 프롬프트 3종(에이전트·근거 판정·해결 판정)의 첫 버전 | `prompts/`. **세부 조정(v2 이후)은 하지 못했다** — 실패 사례를 볼 실제 실행이 없었다 |
| 프롬프트 버전 및 에이전트 배포를 위한 CI/CD 파이프라인을 설정 | 🟡 | 프롬프트 파일 버전 + 해시 잠금, 프롬프트·에이전트 변경 시 도는 평가 워크플로와 게이트, 배포 설정 | `src/batondesk/prompts/registry.py`, `prompts/LOCK.json`, `.github/workflows/ci.yml`·`eval-live.yml`, `Dockerfile`, `render.yaml`. 잠금 검사는 테스트로 확인(`tests/test_prompts.py`). **워크플로는 실행된 적이 없다** |
| 웹, 채팅 및 API 중심 환경에 에이전트를 임베드 | ✅ / 🟡 | 웹 채팅 화면, 한 줄 스크립트로 넣는 위젯, REST·SSE API, 외부 채팅 채널용 웹훅 | `web/chat.html`, `web/widget.js`, `/embed-demo`, `docs/api.md`, 화면 점검 118항목(`tests/e2e/`). 규칙 엔진으로 확인. 특정 메신저 플랫폼 연동은 안 함 |
| 실시간 성능 지표를 모니터링하고 로그, 대시보드 등의 관측 기능을 구현 | ✅ / 🟡 | 구조화 로그(한 줄 JSON), 이벤트 저장, 운영 화면(해결률·인계율·근거 보류·단계별 지연·모델 호출·도구 실패·검토 대기) | `src/batondesk/obs/`, `web/ops.html`, `GET /v1/metrics/summary`, `GET /v1/events`, `tests/test_service_api.py`의 지표 테스트. 실제 트래픽·실제 모델의 수치는 없다 |
| 시니어 엔지니어, 제품 관리자 및 UX 디자이너와 긴밀히 협력 | ❌ | 혼자(와 AI 에이전트로) 만든 프로젝트다. 협업 경험을 보여 주지 않는다 | — |

## 자격 요건 (이런 분과 함께 하고 싶어요)

| 공고 문장 | 상태 | 이 저장소에서 | 주의 |
|---|---|---|---|
| Python: 깔끔하고 유지 관리가 용이한 Python 코드 작성 경험 | ✅ (코드의 상태) | 파이썬 약 6,900줄(평가 코드 1,700줄 포함). `mypy --strict` 통과, `ruff` 통과, 모듈별 책임 분리, 테스트 144건 | 코드는 AI 에이전트가 썼다. **지원자의 작성 경험을 증명하지 않는다.** 읽고 설명할 수 있어야 한다 |
| Backend: Django, FastAPI, Express 등 서버 측 프레임워크를 사용한 백엔드 개발 실무 경험 | ✅ (FastAPI 코드) | FastAPI 엔드포인트 37개, SSE, 하위 앱 마운트, 의존성 주입, 오류 형식 통일, OpenAPI 문서 | "실무 경험"은 아니다. 개인 프로젝트다 |
| 협업: 뛰어난 의사소통 능력, 문서화 기술, 애자일한 팀에서의 협업 경험 | 문서화만 | 설계 결정, API 설명, 테스트 방법, 평가 방법, 한계, 변경 기록 | 팀 협업 경험은 없다 |

## 우대 사항 (이런 점이 있으시면 더 좋아요)

| 공고 문장 | 상태 | 내용 |
|---|---|---|
| 프롬프트 엔지니어링 프레임워크(예: LangChain) 사용 경험 | ✅ LangGraph 사용 / 🔎 LangChain 모델 래퍼 | LangGraph로 에이전트 그래프를 구성했다(노드 8개, 조건 분기, 스트리밍). LangChain의 모델 래퍼·프롬프트 템플릿은 쓰지 않았다 — 이유는 `docs/design-decisions.md` D1 |
| 클라우드 플랫폼(AWS Lambda, Vercel, GCP Cloud Functions)에 대한 이해도 | 🔎 | 세 플랫폼을 이 서비스에 대어 보고 맞지 않는 이유를 정리했다(형태소 분석기의 메모리·콜드 스타트, SSE, 상태 저장). 실제로 올린 곳은 없다. Render 배포 설정만 작성했다 |
| AI 제품 관련 UX 디자인 또는 제품 관리 경험 | 일부 | 제품 조사를 바탕으로 출처 표시·인계 요약·데이터 구분 표시·검토 대기열을 설계했다(`docs/research.md` 6절). 사용자 테스트는 하지 않았다 |
| Django 또는 유사한 Python 웹 프레임워크 사용 경험 | ✅ FastAPI / 🔎 Django | FastAPI를 썼다. Django는 검토만 했다 |

## 기술 스택 대조

| 공고에 나온 기술 | 이 저장소 |
|---|---|
| Python | 사용 (3.12) |
| FastAPI / Django / Express | FastAPI 사용. Django·Express 미사용 |
| LLM API | Claude API (Anthropic 파이썬 SDK) — 코드 작성, 실제 호출 전 |
| RAG, 지식 기반 | 사용 (형태소 BM25 + 문자 n-gram, 지식 문서 23건) |
| 툴체인(Tool Calling) | 사용 (도구 7개, 구조화 출력, strict 스키마) — 실제 모델로는 실행 전 |
| LangChain | LangGraph 사용 |
| AWS Lambda / Vercel / GCP Cloud Functions | 검토만 |
| CI/CD | GitHub Actions 워크플로 작성 — 실행 전 |
