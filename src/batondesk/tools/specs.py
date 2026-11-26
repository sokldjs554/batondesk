"""에이전트에게 허용된 도구 목록(Claude tool 정의).

허용 목록 방식이다: 여기 없는 이름의 호출은 실행기가 거부한다.
설명문은 "언제 부르는가"를 포함한다. 프롬프트 버전이 설명문을 덮어쓸 수 있으므로
(prompts/agent/*.yaml 의 tool_descriptions) 설명문도 프롬프트 버전의 일부로 본다.
"""

from __future__ import annotations

from typing import Any

RETURN_REASONS = ["change_of_mind", "defective", "wrong_item", "damaged_in_transit"]

_ORDER_ID = {"type": "string", "description": "주문번호. 예: BM-24011"}


def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


DEFAULT_DESCRIPTIONS: dict[str, str] = {
    "search_knowledge": (
        "바통마켓 지식 문서(정책·공지·안내·FAQ)를 검색한다. 정책·절차·비용·기간에 관한 질문에는 "
        "답하기 전에 반드시 근거 문서를 확보한다. 처음 제공된 검색 결과에 답이 없거나, 이전 대화를 "
        "이어받은 질문이라 검색어를 바꿔야 할 때 부른다. 질의는 고객 표현 그대로가 아니라 문서에 "
        "쓰였을 법한 핵심어로 쓴다(예: '물건이 안 와요' → '배송 지연 출고 지연 기준')."
    ),
    "list_my_orders": (
        "지금 대화 중인 고객의 최근 주문 목록을 가져온다. 고객이 주문번호를 말하지 않았고 어느 주문인지 알아야 할 때 부른다."
    ),
    "get_order": (
        "주문 한 건의 상태·상품·결제 금액·환불·반품 접수 정보를 가져온다. 특정 주문에 관한 질문에는 "
        "기억이나 추측이 아니라 이 결과로 답한다. 이 고객의 주문이 아니거나 없는 번호면 not_found 가 온다."
    ),
    "track_shipment": ("주문의 운송장 이동 기록을 가져온다. 배송 위치, 도착 예정, 배송이 멈춘 것 같다는 문의에 부른다."),
    "check_cancellation": (
        "주문을 취소할 수 있는지와 환불 금액을 미리 확인한다(실제 취소는 하지 않는다). 취소가 가능하면 confirmation_code 가 함께 온다."
    ),
    "check_return": (
        "주문 상품을 반품할 수 있는지, 반품 배송비와 환불 예상 금액을 미리 확인한다(실제 접수는 하지 "
        "않는다). reason 은 고객이 말한 사유에 맞춘다: change_of_mind(단순변심), defective(불량), "
        "wrong_item(오배송), damaged_in_transit(배송 중 파손). 접수할 수 있으면 confirmation_code 가 온다."
    ),
    "execute_confirmed_action": (
        "check_cancellation / check_return 이 준 confirmation_code 로 취소나 반품 접수를 실제로 실행한다. "
        "고객에게 금액과 내용을 안내하고 고객이 다음 메시지에서 분명히 동의한 뒤에만 부른다. "
        "같은 턴에 받은 코드는 실행되지 않는다."
    ),
}


def tool_definitions(descriptions: dict[str, str] | None = None) -> list[dict[str, Any]]:
    desc = {**DEFAULT_DESCRIPTIONS, **(descriptions or {})}
    schemas: dict[str, dict[str, Any]] = {
        "search_knowledge": _obj({"query": {"type": "string", "description": "검색 질의(한국어 핵심어)"}}),
        "list_my_orders": _obj({}),
        "get_order": _obj({"order_id": _ORDER_ID}),
        "track_shipment": _obj({"order_id": _ORDER_ID}),
        "check_cancellation": _obj({"order_id": _ORDER_ID}),
        "check_return": _obj(
            {
                "order_id": _ORDER_ID,
                "item_id": {"type": "string", "description": "get_order 결과의 item_id. 예: I-1"},
                "reason": {"type": "string", "enum": RETURN_REASONS},
            }
        ),
        "execute_confirmed_action": _obj({"confirmation_code": {"type": "string", "description": "이전 턴의 확인 결과로 받은 코드"}}),
    }
    return [{"name": name, "description": desc[name], "strict": True, "input_schema": schema} for name, schema in schemas.items()]


TOOL_NAMES: tuple[str, ...] = tuple(DEFAULT_DESCRIPTIONS)
WRITE_TOOLS: tuple[str, ...] = ("execute_confirmed_action",)
