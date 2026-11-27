# 기존 저장소 조사와 재사용 범위

새 저장소를 만들기 전에 같은 계정(`sokldjs554`)의 기존 저장소 13개와 `chartwire`의 문서 관례를 조사했다(2026-11-26, 각 저장소의 기본 브랜치 최신 커밋 기준).

## 결론

- 고객 상담·상담원 인계 제품에 해당하는 기존 저장소는 없었다. 가장 가까운 `careflow-backend`와 `chartwire`는 AI 초안을 사람이 검토하는 구조를 갖고 있지만 진료 기록 도구이고, 고객 채팅·주문 도구·상담원 화면이 없다.
- 그래서 이름만 바꾸지 않고 새 프로젝트로 만들었고, 검색의 토크나이저와 BM25만 코드로 가져왔다. 나머지는 구조와 관례를 참고해 새로 썼다.

## 코드로 가져온 것

| 가져온 곳 | 이 저장소의 파일 | 바꾼 점 |
|---|---|---|
| `kodoc-ai` @ `d7d332c` `src/kodoc/rag/tokenizer.py` | `src/batondesk/kb/tokenizer.py` | 상담 도메인 복합어(반품비·무료배송·주문제작 등 24개)를 사용자 사전으로 등록. 사전 구성을 줄여 메모리 사용을 낮춤 |
| `kodoc-ai` @ `d7d332c` `src/kodoc/rag/bm25.py` | `src/batondesk/kb/bm25.py` | 없음(타입 표기 정리) |
| `kodoc-ai` @ `d7d332c` `src/kodoc/rag/retriever.py` 의 RRF 융합과 구조 | `src/batondesk/kb/retriever.py` | 임베딩 랭커를 문자 n-gram 랭커로 교체, 증분 색인·영속화·락 제거, 청크에 문서 종류와 적용 기간을 실음, 유의어 확장 추가 |

각 파일 맨 위에 출처 저장소·커밋·바꾼 점을 적어 두었다. `kodoc-ai`에는 라이선스 파일이 없지만 같은 작성자의 저장소다.

## 구조·관례만 참고하고 새로 쓴 것

| 참고한 곳 | 참고한 내용 | 이 저장소에서 |
|---|---|---|
| `five-whys-agent` `agent/five_whys.py`, `agent/llm.py` | 직접 짠 도구 호출 루프, LLM을 Protocol 뒤에 두어 대역으로 바꿔 끼우는 구조 | `llm/base.py` 의 `LLMClient` Protocol과 `llm/scripted.py` 의 대역. 루프 자체는 LangGraph 그래프로 다시 구성 |
| `five-whys-agent` `agent/tools.py` | 도구 실행기가 `(본문, 오류 여부)`를 돌려주는 방식 | `tools/executor.py` 의 `ToolResult`. 고객 범위 고정과 쓰기 전 확인 코드를 새로 넣음 |
| `quarterguard` `provider_controls.py`, `public_providers.py` | 공개 데모의 모델 호출 비용 장부와 한도 | `store/repo.py` 의 `spend_today`/`add_spend`, 하루 추정 비용 상한과 시간당 요청 한도 |
| `quarterguard` `orchestrator.py` | 단계별 실행 기록(도구, 인자, 성공 여부, 지연 시간) | 턴 실행 기록(`turns.detail_json.steps`) |
| `chartwire` `notes/verifier.py` | 문장 단위로 근거와 대조하고, 확인할 수 없으면 통과시키지 않는 원칙 | `checks/facts.py`(숫자 대조)와 `checks/grounding.py`(판정 실패 시 닫힌 쪽으로) |
| `chartwire` `api/routers/release.py`, `live-gate.yml` | 배포된 서버가 어느 커밋인지 알려 주는 엔드포인트와, 공개 주소가 그 커밋을 서빙할 때까지 기다린 뒤 검사하는 워크플로 | `GET /v1/release` |
| `chartwire` `scripts/readme_numbers.py`, CI의 `FROZEN.txt` 대조 | 평가 자료를 해시로 동결하고 CI에서 대조 | `data/evals/FROZEN.json`, `python -m batondesk.evals check-frozen` |
| `minwon-rag` `rag.py` | 후속 질문을 독립 질의로 바꾸는 단계 | 모델 호출을 하나 더 쓰는 대신, 에이전트가 검색 도구를 직접 부르며 질의를 다시 쓰게 함 |
| `chartwire` `docs/how-this-was-built.md`, `docs/limitations.md` | 사람이 한 일·AI가 한 일·확인한 일을 나눠 적고, 못 한 것을 한계 문서에 적는 관례 | `docs/how-this-was-built.md`, `docs/limitations.md` |

## 일부러 따라 하지 않은 것

| 기존 저장소의 방식 | 따라 하지 않은 이유 |
|---|---|
| `minwon-rag`의 벡터 거리 문턱값으로 "답이 있는 질문인지" 가리기 | 그 저장소의 README가 스스로 보였듯, 거리는 주제의 가까움을 재지 답의 존재를 재지 못한다. 여기서는 모델이 근거를 보고 판단하고, 그 판단을 평가 문항으로 잰다 |
| `minwon-rag`·`five-whys-agent`의 키워드 일치 hit@k, 개발용/평가용 미분리 | 평가 자료를 개발용과 평가용으로 나누고 평가용을 동결했다 |
| `five-whys-agent`의 SQLite FTS5 기본 토크나이저와 해싱 임베딩 | 한국어 조사·어미를 처리하지 못한다. Kiwi 형태소 분석을 쓴다 |
| `quarterguard`의 프롬프트 버전 관리(평가 스크립트를 `_v3 … _v9` 로 복사) | 프롬프트는 데이터 파일로 두고 버전마다 해시를 잠근다(`prompts/`) |
| `five-whys-agent`의 메모리 dict 저장 | SQLite에 대화·실행 기록·인계·이벤트를 저장한다 |
| `chartwire`·`quarterguard`의 초록 계열 색과 세로 내비게이션 | 요청에 따라 쓰지 않았다(상단 내비게이션, 아이보리·남색·코랄) |

## 조사한 저장소 목록

`minwon-rag`, `five-whys-agent`, `quarterguard`(상세 조사), `kodoc-ai`, `ko-search-clinic`, `medical-rag-qa`, `meeting-todo-agent`, `llm-data-analyst-agent`, `datatalk`, `PolicyGate`, `aiops-sentinel`, `careflow-backend`, `fitwitness`(README와 핵심 모듈), `chartwire`(문서 관례와 화면).
조사는 얕은 복제(최신 커밋 1개)로 했기 때문에 각 저장소의 과거 기록은 보지 않았다.

## 글꼴

`web/fonts/pretendard/` 는 Pretendard Variable 1.3.9(npm `pretendard`, 길형진, SIL Open Font License 1.1)의 동적 부분집합(woff2 92조각)과 CSS, 라이선스 전문이다. 수정하지 않고 넣었다. 방문자는 화면에 쓰이는 글자의 조각만 내려받는다.
