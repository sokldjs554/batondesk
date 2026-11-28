# 조사 기록 — 국내외 고객 상담 AI·상담원 도구

> 모든 주문·고객·정책 데이터는 가상의 예시 쇼핑몰 "바통마켓"의 **예시 데이터**입니다.

구현 전에 고객 상담 AI 제품과 상담원 업무 도구를 조사해, 무엇을 가져오고 무엇을 가져오지 않을지 정했다.
조사일은 2026-11-26이다.

## 이 문서를 읽는 법 (확인 수준)

조사에 쓴 환경에서는 벤더 사이트(intercom.com, zendesk.com, delight.ai, sendbird.com 등)를 직접 열 수 없었다.
그래서 벤더 제품에 관한 내용은 **검색 결과에 실린 해당 페이지의 발췌**에서 온 것이다.

| 표시 | 뜻 |
|---|---|
| `[발췌]` | 검색 결과의 발췌로 확인. 원문 페이지를 직접 열어 보지 못했다. 인용하기 전에 URL을 열어 다시 확인해야 한다. |
| `[원문]` | 원문을 직접 읽었다(arXiv, Hugging Face, GitHub). |

면접 등에서 벤더 제품의 동작을 근거로 말하려면 `[발췌]` 항목의 URL을 직접 열어 확인한 뒤에 말해야 한다.
이 저장소의 구현은 아래 제품을 **참고**했을 뿐, 그 제품들과 같은 수준이라는 뜻이 아니다.

## 1. 업무 선택: 왜 주문·배송·반품·환불인가

- 전자상거래 상담은 "정책을 묻는 질문(지식 문서)"과 "내 주문을 묻는 질문(도구 호출)"이 한 대화에 섞인다.
  RAG와 Tool Calling이 둘 다 필요한 이유를 면접관이 설명 없이 이해할 수 있다.
- 정답이 숫자로 떨어진다(7일, 3,000원, 3~7영업일). 답이 근거와 맞는지를 자동으로 채점하기 좋다.
- 사람에게 넘겨야 하는 경계가 분명하다(기간이 지난 예외 요청, 부분 환불 금액, 안전 사고).
- Sierra 연구진의 τ-bench도 소매(retail) 도메인을 쓰고, 최종 DB 상태로 성공을 채점한다 `[원문]`
  https://arxiv.org/abs/2406.12045 — 이 저장소의 "주문 상태 일치" 채점이 같은 생각이다.
- 국내 사례로 29CM의 AI 상담이 문의의 약 25%, 컬리의 AICS가 당일 1:1 문의의 약 40%를 처리한다는 보도가 있다 `[발췌, 언론 보도]`
  https://www.cio.com/article/4219031 · https://www.newsis.com/view/NISX20260610_0003664307
- 반품·환불 규칙은 실제 법이 있는 영역이라 예시 정책을 현실적으로 만들 수 있다(아래 5절).

## 2. 제품별로 본 것과 가져온 것

| 제품 | 본 것 | 이 저장소에 반영한 것 | 확인 |
|---|---|---|---|
| **delight.ai** (Sendbird의 AI 에이전트 플랫폼) | 걸린 답변을 사유(낮은 확신·정책 위반·근거 없음)와 함께 모아 보는 검토 대기열, 환영 메시지부터 도구 호출·인계까지의 단계별 활동 기록, 출시 전 멀티턴 대화 재생 테스트, 대화별 해결·인계·CSAT 분석 | 운영 화면의 "검토 대기" 탭(사유와 걸러진 초안), 대화별 실행 기록, 멀티턴 평가 문항 | `[발췌]` https://sendbird.com/ai-agent/product-release/ai-hallucination-detection-and-alerts · https://sendbird.com/blog/ai-transparency-activity-trails · https://sendbird.com/ai-agent/product-release/end-to-end-ai-agent-testing-build-trust-before-you-go-live · https://delight.ai/analytics |
| **Sendbird Desk** | 인계가 대화 기록과 함께 티켓을 만들고, 상담원 화면이 상태별 티켓·대화·고객 정보를 보여 줌. `#` 로 여는 빠른 답장 | 인계 티켓(대기/진행/종료), 상담원 화면의 빠른 답장 | `[발췌]` https://sendbird.com/docs/desk/guide/v1/integrations/delight-ai-agent · https://sendbird.com/docs/desk/guide/v1/overview/agent-view |
| **Intercom Fin** | 답변 안에 출처 링크. 인계 규칙에 걸리면 답하지 않고 짧은 연결 안내만. 상담원에게 AI 요약이 내부 메모로 전달 | 답변 아래 출처 칩, 인계 시 답변 대신 연결 안내, 인계 요약 | `[발췌]` https://www.intercom.com/help/en/articles/11433030-conversational-fin-experience · https://www.intercom.com/help/en/articles/12396892-manage-fin-ai-agent-s-escalation-guidance-and-rules |
| **Zendesk AI agents / Agent Workspace** | 인계 전 AI 대화를 포함한 티켓 요약(100단어 이내), 인계율과 "실패한 인계"(받을 상담원 없음) 추적 | 인계 요약을 짧게 유지, 운영 화면의 대기 중 인계 수 | `[발췌]` https://support.zendesk.com/hc/en-us/articles/8037565521946 · https://support.zendesk.com/hc/en-us/articles/11338937099034 |
| **Ada** | 인계 대화에 요약을 자동 첨부해 상담원이 인계 사유를 봄 | 인계 사유를 코드 값으로 저장하고 화면에 표시 | `[발췌]` https://docs.ada.cx/docs/handoffs/handoff-management/transcript-summaries |
| **Decagon** | 가드레일·절차별 인계 건수(Escalation Drivers), 상시 QA가 검토할 대화를 골라냄 | 인계 사유별 집계 | `[발췌]` https://decagon.ai/resources/diagnostic-tools |
| **Sierra** | 모든 인계에 AI 요약. 답변이 생성될 때 검토하는 "Supervisor" | 답변을 내보내기 전에 근거 검사를 거치는 구조 | `[발췌]` https://sierra.ai/blog/confidence-in-every-conversation |
| **Gorgias AI Agent** (전자상거래) | 인계 요약이 "무슨 일이 있었고, 무엇을 시도했고, 다음에 무엇을 할지"를 담음. `ai_failed_action` 같은 태그로 분류 | 인계 요약의 구성(고객 요청·확인한 사실·넘기는 이유·권하는 조치) | `[발췌]` https://www.gorgias.com/blog/introducing-helpdesk-2-0 · https://docs.gorgias.com/en-US/understand-ai-agent-tags-and-ticket-views-5414491 |
| **Salesforce Agentforce** | 고객이 요청한 인계와 "에이전트가 해결하지 못한" 인계를 구분 | 인계 출처를 model / system / customer 로 구분 | `[발췌]` https://www.salesforce.com/blog/support-metrics-agentforce/ |
| **채널톡 ALF** | 고객에게 참고한 지식 출처를 보여 주는 설정. 해결률 = 상담원에게 연결되지 않은 상담 ÷ ALF가 관여한 상담 | 출처 표시, "관여"의 정의(고객이 AI에게 한 번 이상 메시지) | `[발췌]` https://docs.channel.io/help/ko/articles/ALF-통계--932cae8a |
| **네이버 톡톡 챗봇 API** | 상담원이 대화를 가져가면 사용자 메시지에 `standby: true` 가 붙고 봇은 답하지 않음. 상담 완료 시 제어권 반환 | 인계 후 AI 대기(메시지는 저장하되 답하지 않음), 상담원의 "AI에게 돌려주기" | `[원문]` https://github.com/navertalk/chatbot-api/blob/master/handover_v1.md |
| **해피톡** | 규칙 챗봇과 AI 에이전트를 나눠 쓰고, AI 상담 요약을 제공 | — (참고만) | `[발췌]` https://blog.happytalk.io/ai-enhanced-happytalk-launch |

조사했지만 근거를 찾지 못해 뺀 것: 카카오 i 커넥트 톡의 인계 화면, 쿠팡·무신사의 상담 챗봇, Freshworks Freddy의 지표 정의.

### 가져오지 않은 것과 이유

- **답변 글자 스트리밍**: 여러 채팅 제품이 답을 글자 단위로 흘려보낸다. 이 저장소는 근거 검사를 통과한 답만 한 번에 내보낸다.
  검사 전의 글자가 고객 화면에 먼저 보이면 "걸러진 초안은 고객에게 보이지 않는다"는 보장이 깨지기 때문이다. 대신 진행 단계(검색·도구 호출·근거 대조)를 흘려보낸다.
- **CSAT 설문**: 실제 사용자가 없어 수집할 수 없다. "해결됐어요 / 아직이에요" 버튼만 두고 해결 판정의 입력으로 쓴다.
- **옴니채널(SMS, WhatsApp, 이메일)**: 웹 채팅·임베드 위젯·범용 웹훅까지만 구현했다.

## 3. 지표 정의 — 제품마다 다르다

"해결률"은 제품마다 정의가 달라서, 이 저장소의 정의를 화면과 문서에 적어 두었다.

| 지표 | 벤더의 정의 | 확인 |
|---|---|---|
| 해결(Intercom) | 확인된 해결 = 고객이 긍정적으로 답함. 가정된 해결 = 고객이 피드백 없이 떠났고 인계되지 않음 | `[발췌]` https://www.intercom.com/help/en/articles/8205718-fin-ai-agent-resolutions |
| 자동 해결(Zendesk) | LLM 검증에서 사람의 개입이 필요 없다고 판정된 대화. 인계되면 셀 수 없음 | `[발췌]` https://support.zendesk.com/hc/en-us/articles/8357756668186 |
| 자동 해결(Ada) | 관련 있고·정확하고·안전하고·인계되지 않은 대화. 해결 ÷ (해결 + 미해결), 스팸·인사뿐인 대화는 제외 | `[발췌]` https://docs.ada.cx/docs/generative/measure-success/understand-and-improve-your-ai-agent-s-automated-resolution-rate |
| 해결률(채널톡) | 상담원에게 연결되지 않은 상담 ÷ ALF가 관여한 상담 | `[발췌]` 위 채널톡 문서 |
| 인계율(delight.ai) | AI에서 사람에게 넘어간 비율 | `[발췌]` https://delight.ai/blog/ai-for-cx/guide-to-customer-experience-analytics |

**이 저장소의 정의** (`src/batondesk/obs/metrics.py`):

- 해결률 = (확인된 해결 + 가정된 해결) ÷ (해결 + 미해결 + 상담원 인계)
- 인사뿐인 대화, 고객의 답을 기다리는 대화, 판정하지 못한 대화는 분모에서 뺀다(Ada의 제외 규칙).
- 인계된 대화는 AI가 해결한 것으로 세지 않는다(Zendesk).
- 확인된 해결과 가정된 해결을 따로 보여 준다(Intercom). "고객이 말없이 떠났다"를 만족으로 읽지 않기 위해서다.

**대화당 전달 응답 수** = 집계 대상 대화에 저장된 고객용 AI 메시지 수 ÷ AI 관여 대화 수. AI 관여 대화는 기존 집계와 같이 `turn_count >= 1`이거나 상담원 인계 이력이 있는 대화다. 고객이 바로 상담원에게 넘긴 대화도 분모에 포함하며, 턴과 인계가 없는 빈 대화는 제외한다. 해결률의 분모와는 다르다.

분자는 `messages`의 `role = ai`인 최종 저장 메시지를 센다(답변·되묻기·AI의 인계 안내 포함). 화면에만 보이는 초기 자동 안내, 근거 검사에 걸린 초안, 모델 호출, 고객·상담원·시스템 메시지는 세지 않는다. 서버에 저장한 고객용 응답을 기준으로 하며 고객의 실제 열람 여부는 측정하지 않는다. 출처 필터(`source`)는 분자와 분모에 같은 대화 집합으로 적용한다.

`GET /v1/metrics/summary`는 `responses.delivered`(분자), `responses.denominator`(분모), `responses.per_conversation`(소수 넷째 자리로 반올림)을 돌려준다. 분모가 0이면 값은 `null`이고 운영 화면의 **대화와 응답 → 대화당 응답 수**에는 `—`로 표시한다. 직접 인계만 있어 전달 응답이 0개인 경우에는 `0.00개`로 표시한다.

## 4. 근거 일치 판정과 해결 판정의 방법

| 방법 | 내용 | 이 저장소 | 확인 |
|---|---|---|---|
| 주장 단위 검증(RAGAS faithfulness) | 답변을 문장(주장)으로 나누고 주장마다 근거가 뒷받침하는지 확인 | 채택: 주장별 라벨을 받고 최종 판정은 코드가 합침 | `[원문]` https://arxiv.org/abs/2309.15217 |
| NLI 분류기(Vectara HHEM-2.1-Open) | 전제·가설 점수. 공개 모델은 영어 전용으로 표시됨 | 미채택: 한국어 지식 문서라 맞지 않음 | `[원문]` https://huggingface.co/vectara/hallucination_evaluation_model |
| 작은 전용 검증 모델(MiniCheck) | 훨씬 낮은 비용으로 GPT-4 수준이라고 보고 | 미채택(검토만): 한국어 성능 근거가 없고 모델을 올릴 호스팅 여유가 없음 | `[원문]` https://arxiv.org/abs/2404.10774 |
| 표본 일관성(SelfCheckGPT) | 외부 근거 없이 여러 번 생성해 일치하는지 확인 | 미채택: 근거 문서가 있는 상황이라 근거 대조가 더 직접적 | `[원문]` https://arxiv.org/abs/2303.08896 |
| 사후 LLM 분류로 해결 판정 | Ada·Zendesk·Salesforce가 대화가 끝난 뒤 LLM으로 분류 | 채택: 5분류 + 규칙(인계·고객 피드백 우선) | 3절의 `[발췌]` |

**LLM 판정의 알려진 약점**과 이 저장소의 대응:

| 약점 | 근거 | 대응 |
|---|---|---|
| "괜찮다"고 너무 쉽게 말한다(참 양성률 96%, 참 음성률 25% 미만이라는 보고) | `[원문]` https://arxiv.org/abs/2510.11822 | 정확도만 보지 않고 "어긋난 답을 어긋났다고 잡는 비율", "미해결을 미해결로 맞히는 비율"을 따로 잰다 |
| 자기가 만든 출력을 더 좋게 본다 | `[원문]` https://arxiv.org/abs/2404.13076 | 판정 모델을 에이전트 모델과 다르게 둘 수 있게 설정을 분리했다. 같은 모델을 쓴 실행은 그 사실을 결과에 적는다 |
| 어려운 사례에서 탐지기 정확도가 50% 근처 | `[원문]` https://arxiv.org/abs/2410.13210 | 판정기 평가 자료에 "한 구절만 틀린 답", "공손한 말투의 근거 없는 약속"을 일부러 넣었다 |
| 조용히 떠난 고객을 해결로 세는 문제 | `[발췌]` https://community.intercom.com/ask-the-intercom-team-about-fin-54/fin-s-flawed-assumed-resolved-pricing-design-8929 | 가정된 해결을 확인된 해결과 나눠 보여 준다 |

## 5. 예시 정책의 바탕이 된 국내 법

바통마켓의 반품·환불 정책은 「전자상거래 등에서의 소비자보호에 관한 법률」의 틀을 따르도록 썼다.
조문은 법제처 원문 페이지가 본문을 보여 주지 않아 **GitHub 미러**로 읽었다(메타데이터가 법제처 표기와 일치). 인용하려면 법제처 원문을 다시 확인해야 한다.

- 원문: https://www.law.go.kr/법령/전자상거래등에서의소비자보호에관한법률
- 읽은 미러: https://github.com/legalize-kr/legalize-kr/blob/main/kr/전자상거래등에서의소비자보호에관한법률/법률.md

| 조문 | 내용 | 예시 정책에 반영한 곳 |
|---|---|---|
| 제17조 ① | 계약서를 받은 날(공급이 늦으면 공급받은 날)부터 7일 이내 청약철회 | `kb/return-policy.md` 반품 신청 기간 7일 |
| 제17조 ② | 소비자 책임으로 훼손(내용 확인을 위한 포장 개봉은 제외), 사용으로 가치가 현저히 감소한 경우 등은 철회 제한 | `kb/return-exclusions.md` |
| 제17조 ③ | 표시·광고·계약과 다르면 공급받은 날부터 3개월, 안 날부터 30일 이내 | `kb/return-policy.md` 하자·오배송 |
| 제18조 ② | 반환받은 날부터 3영업일 이내 환급 | `kb/refund-policy.md` 환불 처리 시점 |
| 제18조 ⑨ ⑩ | 단순 철회의 반환 비용은 소비자, 제17조 ③의 경우는 사업자 부담 | `kb/return-policy.md` 반품 배송비 |
| 시행령 제21조 | 주문제작 상품은 사전 고지와 동의가 있어야 철회 제한 | `kb/custom-made.md` |

예시 정책은 법률 자문을 거친 것이 아니다. 법의 틀과 어긋나지 않게 쓴 연습용 문서다.

## 6. 화면 설계에 반영한 참고 사항

| 화면 | 반영한 것 | 참고 |
|---|---|---|
| 고객 채팅 | 답변 아래 출처 칩, 누르면 근거 원문과 적용 기간 | Intercom Fin, 채널톡 ALF |
| 고객 채팅 | 인계 시 답 대신 짧은 연결 안내 + 상담원에게 전달된 요약을 고객도 볼 수 있음 | Intercom Fin |
| 고객 채팅 | 인계 뒤 AI는 대기하고 메시지는 상담원에게 전달 | 네이버 톡톡 |
| 고객 채팅 | "해결됐어요 / 아직이에요"를 확인된 해결의 입력으로 | Intercom Fin |
| 상담원 화면 | 맨 위에 인계 요약(고객 요청·확인한 사실·넘기는 이유·권하는 조치) | Gorgias, Zendesk, Sierra |
| 상담원 화면 | 상태별 티켓, 빠른 답장 | Sendbird Desk |
| 상담원 화면 | 인계 사유를 값으로 저장해 분류 | Gorgias, Salesforce |
| 운영 화면 | 걸린 답변의 검토 대기열(사유 포함) | delight.ai |
| 운영 화면 | 대화별 단계 기록(검색 → 도구 → 근거 판정 → 인계) | delight.ai |
| 운영 화면 | 해결률을 확인·가정·미해결·인계·제외로 나눠 표시 | Ada, Intercom, 채널톡 |
