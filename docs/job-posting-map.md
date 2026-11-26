# 공고 대조표 — 센드버드 AI Agent Software Engineer, Intern

> 모든 주문·고객·정책 데이터는 가상의 예시 쇼핑몰 "바통마켓"의 **예시 데이터**입니다.

- 공고: https://job-boards.greenhouse.io/get-delight/jobs/8639423002 (게시 2026-08-12, 회사 표기 delight.ai, 서울, 인턴 8~12주)
- 공고 원문을 2026-11-26에 다시 내려받아 아래 문장을 옮겼다.

**상태 표시**

| 표시 | 뜻 |
|---|---|
| ✅ 실행·확인 | 코드가 있고, 테스트나 실제 모델 실행으로 확인했다. 결과가 좋았다는 뜻이 아니다 — 결과는 "근거" 칸에 있다 |
| 🟡 준비만 | 코드와 설정은 있지만 실행해 보지 못했다 |
| 🔎 검토만 | 조사·비교만 했다. 쓴 코드가 없다 |
| ❌ 안 함 | 하지 않았다 |

이 표는 **저장소에 무엇이 있는가**를 적는다. 누가 했는가는 `docs/how-this-was-built.md` 에 있다(구현·평가는 AI 에이전트가 했고, 저장소 주인이 직접 재현·검수한 기록은 아직 없다).

## 주요 업무 (이런 일을 하실 수 있어요)

| 공고 문장 | 상태 | 이 저장소에서 | 근거 |
|---|---|---|---|
| 에이전트 프로토타이핑 및 개발 | ✅ | 검색 → 모델·도구 반복 → 응답 제출 → 근거 검사 → 재작성/인계의 한 턴 그래프 | `src/batondesk/agent/graph.py`, `tests/test_agent_graph.py`. 실제 모델로 평가용 64문항 × 3회: 통과율 89.6% [82.8, 95.3] (재채점 92.7%), 주문 상태 일치 100% — `docs/evaluation.md` 6.2 |
| 프롬프트 엔지니어링으로 Hallucination 탐지 … 마이크로 API 개발 | ✅ | `POST /v1/checks/grounding`: 답변을 주장 단위로 판정(규칙 + LLM). 에이전트와 무관하게 단독 호출 가능 | `src/batondesk/checks/grounding.py`, `prompts/grounding/v1~v4.yaml`. 평가용 54문항 × 3회: 어긋난 답 78/78 탐지, 멀쩡한 답 77/84 통과(v4). **판정기가 틀린 사례와 버전별 오탐의 변화**는 `docs/evaluation.md` 4절 |
| … 대화 Resolution 등 마이크로 API 개발 | ✅ | `POST /v1/checks/resolution`: 확인된 해결 / 가정된 해결 / 미해결 / 고객 답 대기 / 해당 없음 / 인계 | `src/batondesk/checks/resolution.py`, `prompts/resolution/v1.yaml`. 평가용 36문항 × 3회: 5분류 97.2% [91.7, 100], 미해결을 해결이라고 한 경우 0건. 자동 판정이 틀릴 때의 처리(고객 피드백 우선, 인계는 해결로 세지 않음)는 5절 |
| 내부 데이터셋과 도구를 활용해 AI Agent의 정확도를 테스트하고 검증 | ✅ | 평가 자료 307문항(개발용·평가용 분리, 평가용 동결), 채점기, 반복 실행기, 문항 단위 신뢰구간, 같은 문항끼리의 비교, 재채점 | `data/evals/`, `src/batondesk/evals/`, `eval_results/runs/`(실행 25건). 실패를 읽어 채점 규칙 오류를 찾고 수정 전후를 모두 남긴 기록: `docs/evaluation.md` 6.3. **문항과 정답은 AI가 썼고 사람 검수는 없다** |
| RAG, 지식 기반 및 맞춤형 툴체인을 지원하는 백엔드 API를 통합 | ✅ | 지식 문서 23건과 검색 API, 도구 7개와 예시 주문 시스템 API, 도구 실행기의 안전장치 | `src/batondesk/kb/`, `tools/`, `commerce/`, `tests/test_kb.py`·`test_tools.py`·`test_commerce.py`. 검색: 평가용 5위 내 77.5%. 실제 모델 실행에서 불러야 할 도구를 부른 비율 98.6~100%. **평가용 실패의 가장 큰 원인이 검색이었다**(6.4) |
| 다양한 사용 사례에 맞는 프롬프트를 제작, 테스트 및 세부 조정 | ✅ | 에이전트 프롬프트 v1~v4, 근거 판정 v1~v4, 해결 판정 v1. 버전마다 무엇을 왜 바꿨는지와 결과를 파일의 `changes` 에 기록 | `prompts/`. 같은 문항으로 버전을 비교했고, **평가용에서 v4 는 v1 보다 통과율이 높지 않았다**(+0.5%p [−3.6, +5.2]). 지연 p95 는 15.0초 → 10.0초. `docs/evaluation.md` 6.1~6.2 |
| 프롬프트 버전 및 에이전트 배포를 위한 CI/CD 파이프라인을 설정 | ✅ | 프롬프트 파일 버전 + 해시 잠금, 프롬프트·에이전트 변경 시 실제 모델 평가와 회귀 게이트, 이미지 빌드와 기동 확인, push 마다 재배포와 배포본 검사 | `src/batondesk/prompts/registry.py`, `prompts/LOCK.json`, `.github/workflows/ci.yml`·`eval-live.yml`·`live-gate.yml`, `eval_results/gate.json`, `render.yaml`. 공개 주소 <https://batondesk.onrender.com> 에서 최신 커밋이 서빙되는 것을 `live-gate` 가 확인(`live-checks` 브랜치에 기록과 캡처). **배포본에는 모델 키가 없어 실시간 문의는 꺼져 있다** |
| 웹, 채팅 및 API 중심 환경에 에이전트를 임베드 | ✅ | 웹 채팅 화면, 한 줄 스크립트로 넣는 위젯, REST·SSE API, 외부 채팅 채널용 웹훅 | `web/chat.html`, `web/widget.js`, `/embed-demo`, `docs/api.md`, 화면 점검 154항목(`tests/e2e/`). 특정 메신저 플랫폼 연동은 안 함 |
| 실시간 성능 지표를 모니터링하고 로그, 대시보드 등의 관측 기능을 구현 | ✅ | 구조화 로그(한 줄 JSON), 이벤트 저장, 운영 화면(해결률·인계율·근거 보류·단계별 지연·모델 호출·도구 실패·검토 대기·프롬프트 버전별 평가 결과) | `src/batondesk/obs/`, `web/ops.html`, `GET /v1/metrics/summary`, `GET /v1/events`. 실제 모델로 만든 기록 8건의 지표가 화면에 집계된다. **실제 트래픽은 없다** |
| 시니어 엔지니어, 제품 관리자 및 UX 디자이너와 긴밀히 협력 | ❌ | 혼자(와 AI 에이전트로) 만든 프로젝트다. 협업 경험을 보여 주지 않는다 | — |

## 자격 요건 (이런 분과 함께 하고 싶어요)

| 공고 문장 | 상태 | 이 저장소에서 | 주의 |
|---|---|---|---|
| Python: 깔끔하고 유지 관리가 용이한 Python 코드 작성 경험 | ✅ (코드의 상태) | 파이썬 약 7,200줄(평가 코드 1,800줄 포함)과 테스트 1,800줄. `mypy --strict` 통과, `ruff` 통과, 모듈별 책임 분리, 테스트 149건 | 코드는 AI 에이전트가 썼다. **지원자의 작성 경험을 증명하지 않는다.** 읽고 설명할 수 있어야 한다 |
| Backend: Django, FastAPI, Express 등 서버 측 프레임워크를 사용한 백엔드 개발 실무 경험 | ✅ (FastAPI 코드) | FastAPI 엔드포인트 37개, SSE, 하위 앱 마운트, 의존성 주입, 오류 형식 통일, OpenAPI 문서 | "실무 경험"은 아니다. 개인 프로젝트다 |
| 협업: 뛰어난 의사소통 능력, 문서화 기술, 애자일한 팀에서의 협업 경험 | 문서화만 | 설계 결정, API 설명, 테스트 방법, 평가 방법과 결과, 한계, 변경 기록 | 팀 협업 경험은 없다 |

## 우대 사항 (이런 점이 있으시면 더 좋아요)

| 공고 문장 | 상태 | 내용 |
|---|---|---|
| 프롬프트 엔지니어링 프레임워크(예: LangChain) 사용 경험 | ✅ LangGraph 사용 / 🔎 LangChain 모델 래퍼 | LangGraph로 에이전트 그래프를 구성했다(노드, 조건 분기, 스트리밍). LangChain의 모델 래퍼·프롬프트 템플릿은 쓰지 않았다 — 이유는 `docs/design-decisions.md` D1 |
| 클라우드 플랫폼(AWS Lambda, Vercel, GCP Cloud Functions)에 대한 이해도 | 🔎 | 세 플랫폼을 이 서비스에 대어 보고 맞지 않는 이유를 정리했다(형태소 분석기의 메모리·콜드 스타트, SSE, 상태 저장). 실제로 올린 곳은 없다. 실제로 올린 곳은 Render(컨테이너 호스팅) 한 곳이다 |
| AI 제품 관련 UX 디자인 또는 제품 관리 경험 | 일부 | 제품 조사를 바탕으로 출처 표시·고객에게 보내지 않은 초안 표시·인계 요약·데이터 구분 표시·검토 대기열을 설계했다(`docs/research.md` 6절). 사용자 테스트는 하지 않았다 |
| Django 또는 유사한 Python 웹 프레임워크 사용 경험 | ✅ FastAPI / 🔎 Django | FastAPI를 썼다. Django는 검토만 했다 |

## 기술 스택 대조

| 공고에 나온 기술 | 이 저장소 |
|---|---|
| Python | 사용 (3.12) |
| FastAPI / Django / Express | FastAPI 사용. Django·Express 미사용 |
| LLM API | Claude API (Anthropic 파이썬 SDK 2.14). 실제 호출 6,500회 이상(평가 25건) |
| RAG, 지식 기반 | 사용 (형태소 BM25 + 문자 n-gram, 지식 문서 23건) |
| 툴체인(Tool Calling) | 사용 (조회·확인·실행 도구 7개와 응답 제출 도구, strict 스키마). 실제 모델로 실행함 |
| LangChain | LangGraph 사용 |
| AWS Lambda / Vercel / GCP Cloud Functions | 검토만 |
| CI/CD | GitHub Actions: 테스트·화면 점검·이미지 빌드, 프롬프트 변경 시 실제 모델 평가와 게이트, push 마다 Render 재배포와 배포본 검사 |
