# API 설명

> 모든 주문·고객·정책 데이터는 가상의 예시 쇼핑몰 "바통마켓"의 **예시 데이터**입니다. 데모 서버라 인증이 없습니다.

- 대화형 문서(Swagger): 서버의 `/docs`, 예시 주문 시스템은 `/example-commerce/docs`
- 아래 표는 `python scripts/gen_api_doc.py` 가 앱의 OpenAPI 스키마에서 만들어 넣는다. CI가 표와 코드가 같은지 확인한다.

## 엔드포인트

<!-- endpoints:begin -->

### 서비스 API

**대화**

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/v1/conversations` | 대화 시작 |
| `GET` | `/v1/conversations` | 대화 목록 |
| `GET` | `/v1/conversations/{conversation_id}` | 대화 조회 |
| `POST` | `/v1/conversations/{conversation_id}/messages` | 고객 메시지 보내기(응답을 한 번에 받음) |
| `POST` | `/v1/conversations/{conversation_id}/messages/stream` | 고객 메시지 보내기(진행 상황을 SSE로 받음) |
| `POST` | `/v1/conversations/{conversation_id}/handoff` | 고객이 상담원 연결을 요청 |
| `POST` | `/v1/conversations/{conversation_id}/feedback` | 해결 여부 피드백 |

**상담원 인계**

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/v1/handoffs` | 인계 목록 |
| `GET` | `/v1/handoffs/{handoff_id}` | 인계 건 상세(대화·근거·도구 결과·미해결 사유) |
| `POST` | `/v1/handoffs/{handoff_id}/claim` | 상담원이 맡기 |
| `POST` | `/v1/handoffs/{handoff_id}/reply` | 상담원이 고객에게 답하기 |
| `POST` | `/v1/handoffs/{handoff_id}/resolve` | 인계 건 종료 |

**지식 검색·판정**

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/v1/kb/search` | 지식 문서 검색 |
| `GET` | `/v1/kb/documents` | 지식 문서 목록 |
| `GET` | `/v1/kb/documents/{doc_id}` | 지식 문서 한 건 |
| `POST` | `/v1/checks/grounding` | 근거 일치 판정(환각 탐지) |
| `POST` | `/v1/checks/resolution` | 대화 해결 여부 판정 |

**운영**

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/healthz` | 상태 확인 |
| `GET` | `/v1/release` | 배포된 코드·프롬프트·모델 정보 |
| `GET` | `/v1/prompts` | 프롬프트 버전 목록 |
| `GET` | `/v1/prompts/{version_id}` | 프롬프트 버전 본문 |
| `GET` | `/v1/evals` | 저장소에 커밋된 평가 결과 요약 |
| `GET` | `/v1/metrics/summary` | 운영 지표 |
| `GET` | `/v1/events` | 최근 운영 이벤트(로그) |

**데모 제어**

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/v1/demo/customers` | 예시 고객과 주문 |
| `GET` | `/v1/demo/sandbox` | 예시 주문 시스템 상태 |
| `PUT` | `/v1/demo/fault` | 예시 주문 시스템 장애 재현 켜기/끄기 |
| `POST` | `/v1/demo/reset` | 예시 주문 데이터 초기화 |

**채널 연동**

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/v1/channels/webhook` | 외부 채팅 채널용 웹훅 |

### 예시 주문 시스템 API (`/example-commerce`)

가상의 예시 시스템입니다. 에이전트의 도구가 호출하며, 실제 주문·결제와 연결되어 있지 않습니다.

**기타**

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/example-commerce/customers/{customer_id}/orders` | List Orders |
| `GET` | `/example-commerce/orders/{order_id}` | Read Order |
| `GET` | `/example-commerce/orders/{order_id}/shipment` | Read Shipment |
| `POST` | `/example-commerce/orders/{order_id}/cancel` | Cancel Order |
| `POST` | `/example-commerce/orders/{order_id}/returns` | Request Return |
| `GET` | `/example-commerce/sandbox` | Read Sandbox |
| `PUT` | `/example-commerce/sandbox/fault` | Set Fault |
| `POST` | `/example-commerce/sandbox/reset` | Reset |

<!-- endpoints:end -->

## 오류 형식

서비스 API의 오류는 모두 같은 모양이다.

```json
{"error": {"code": "conversation_closed", "message": "종료된 대화입니다. 새 대화를 시작해 주세요."}}
```

| HTTP | code | 언제 |
|---|---|---|
| 404 | `conversation_not_found`, `handoff_not_found`, `customer_not_found`, `document_not_found`, `prompt_not_found` | 대상이 없음 |
| 409 | `conversation_closed` | 상담원이 종료한 대화에 메시지를 보냄 |
| 409 | `conversation_too_long` | 한 대화의 고객 메시지 수 상한을 넘김 |
| 409 | `recorded_conversation` | 저장된 상담 기록(읽기 전용)을 바꾸려 함 |
| 409 | `handoff_resolved` | 종료된 인계 건에 답하려 함 |
| 422 | `empty_message`, `message_too_long` | 메시지가 비었거나 600자를 넘음 |
| 429 | `budget_exhausted` | 공개 데모의 하루 모델 호출 추정 비용 상한에 닿음 |
| 429 | `rate_limited` | 같은 주소에서 시간당 요청 수 상한을 넘김 |
| 503 | `llm_unavailable` | 서버에 모델 키가 없어 모델 호출이 꺼져 있음 |

## 사용 예

### 대화 한 번

```bash
BASE=http://localhost:8000

# 1) 예시 고객으로 대화 시작
CID=$(curl -s -X POST $BASE/v1/conversations -H 'content-type: application/json' \
  -d '{"customer_id": "C-1001"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')

# 2) 메시지 보내기 — 응답에 답변, 출처, 턴 요약이 온다
curl -s -X POST $BASE/v1/conversations/$CID/messages -H 'content-type: application/json' \
  -d '{"text": "BM-24011 가습기를 단순변심으로 반품하면 얼마 돌려받아요?"}'
```

응답의 주요 필드:

```jsonc
{
  "standby": false,                       // true 면 상담원이 응대 중이라 AI가 답하지 않았다
  "message": {
    "role": "ai",
    "text": "…",
    "payload": {
      "action": "answer",                 // answer | ask | handoff
      "citations": [                      // 답변이 인용한 근거. kind 는 kb(지식 문서) 또는 tool(예시 주문 시스템 조회 결과)
        {"id": "T1", "kind": "tool", "title": "예시 주문 시스템 · check_return(…)", "text": "{…}", "meta": {…}}
      ],
      "grounding": "grounded",
      "engine": "anthropic"               // anthropic(실제 모델) | rule-engine(LLM 아님)
    }
  },
  "handoff": null,                        // 인계가 열렸으면 사유·요약·상태
  "turn": {"latency_ms": 0, "llm_calls": 0, "tool_calls": 0, "grounding_verdict": "grounded", "revisions": 0, "blocked_count": 0, "stage_latency_ms": {…}}
}
```

### 진행 상황을 받으며 보내기 (SSE)

```bash
curl -N -X POST $BASE/v1/conversations/$CID/messages/stream -H 'content-type: application/json' \
  -d '{"text": "그럼 접수해 주세요."}'
# event: progress  data: {"stage": "prepare", "label": "지식 문서를 검색했습니다"}
# event: progress  data: {"stage": "tool_call", "label": "확인 중: execute_confirmed_action"}
# event: progress  data: {"stage": "verify", "label": "답변을 근거와 대조했습니다"}
# event: result    data: {…위와 같은 응답…}
```

답변 글자는 흘려보내지 않는다. 근거 검사를 통과한 답만 한 번에 내보내기 때문이다(`docs/design-decisions.md` D2).

### 상담원 쪽

```bash
curl -s "$BASE/v1/handoffs?status=waiting"                        # 대기 중인 인계
curl -s $BASE/v1/handoffs/$HID                                     # 요약·대화·근거·도구 호출·보내지 않은 AI 초안
curl -s -X POST $BASE/v1/handoffs/$HID/reply -H 'content-type: application/json' \
  -d '{"agent_name": "민지", "text": "확인해 보겠습니다."}'
curl -s -X POST $BASE/v1/handoffs/$HID/resolve -H 'content-type: application/json' \
  -d '{"agent_name": "민지", "note": "안내 완료", "return_to_ai": false}'
```

### 판정 API만 따로 쓰기

에이전트와 무관하게 부를 수 있다.

```bash
# 근거 일치 판정: 답변을 주장 단위로 나눠 근거가 뒷받침하는지 본다
curl -s -X POST $BASE/v1/checks/grounding -H 'content-type: application/json' -d '{
  "answer": "반품은 14일 이내에 가능하고 배송비는 3,000원입니다.",
  "evidence": [{"id": "K1", "text": "받은 날부터 7일 이내에 반품을 신청할 수 있습니다. 반품 배송비는 3,000원입니다."}],
  "today": "2026-11-20"
}'
# → {"verdict": "ungrounded", "mode": "llm+rules", "claims": [{"claim": "…", "label": "contradicted", …}], "fact_flags": […], "checker": {…}}
#   모델을 쓸 수 없거나 "use_llm": false 면 mode 가 "rules_only" 로 온다(금액·날짜 등 규칙 대조만).

# 해결 여부 판정
curl -s -X POST $BASE/v1/checks/resolution -H 'content-type: application/json' -d '{
  "messages": [{"role": "고객", "text": "반품 기한이 며칠이에요?"}, {"role": "AI", "text": "받은 날부터 7일 이내입니다."}, {"role": "고객", "text": "감사합니다"}]
}'
# → {"status": "resolved_confirmed", "customer_signal": "positive", "reason": "…", "resolved": true, "counted_in_rate": true}
```

### 다른 채팅 채널에 붙이기 (웹훅)

```bash
curl -s -X POST $BASE/v1/channels/webhook -H 'content-type: application/json' \
  -d '{"channel": "kakao", "external_user_key": "u-123", "customer_id": "C-1002", "text": "BM-24021 반품하고 싶어요"}'
# → {"conversation_id": "…", "reply": "…", "action": "ask", "sources": […], "handoff": false}
```

같은 `(channel, external_user_key)` 의 메시지는 같은 대화로 이어진다. 특정 메신저 플랫폼의 서명 검증·메시지 형식 변환은 구현하지 않았다.

### 웹 페이지에 넣기 (위젯)

```html
<script src="https://<서버 주소>/static/widget.js" data-customer="C-1001"></script>
```

`/embed-demo` 에서 동작하는 모습을 볼 수 있다.
