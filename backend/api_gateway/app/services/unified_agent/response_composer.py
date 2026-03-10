"""
Response Composer — Minimal, template-first response generation.

PRINCIPLE: Default minimal. Suggestions as CTA buttons, not text narration.
Bot MUST NOT be talkative. Suggest via buttons, user decides.

3 modes:
1. Template (DEFAULT, no LLM) — success/error/clarification messages
2. CTA Buttons (code-driven, no LLM) — suggest next actions after confirm
3. LLM-composed (RARE) — only for workflow narration
"""

import logging
from typing import Optional

logger = logging.getLogger("unified_agent.response_composer")


def compose_confirm_response(
    action_key: str,
    success_message: str,
    payload: dict,
    action_result: dict = None,
) -> dict:
    """
    Build confirm response with optional CTA buttons.
    
    Returns dict with:
    - text: success message (from registry, already template-formatted)
    - next_actions: optional list of CTA buttons [{key, label, hint}]
    
    CTA buttons are CONTEXT-DRIVEN:
    - After invoice -> [Catat pembayaran]
    - After bill -> [Bayar tagihan ini]
    - After payment -> [Bayar invoice lain] (if customer has more outstanding)
    - After expense -> (no CTA)
    - After master data -> (no CTA)
    """
    next_actions = []

    if action_key == "create_sales_invoice":
        next_actions.append({
            "key": "receive_payment",
            "label": "Catat pembayaran",
            "hint": "create_receive_payment",
        })

    elif action_key == "create_bill":
        next_actions.append({
            "key": "bill_payment",
            "label": "Bayar tagihan ini",
            "hint": "create_bill_payment",
        })

    elif action_key == "create_receive_payment":
        if action_result and action_result.get("remaining_outstanding", 0) > 0:
            next_actions.append({
                "key": "receive_more",
                "label": "Bayar invoice lain",
                "hint": "create_receive_payment",
            })

    elif action_key == "create_bill_payment":
        if action_result and action_result.get("remaining_outstanding", 0) > 0:
            next_actions.append({
                "key": "pay_more",
                "label": "Bayar tagihan lain",
                "hint": "create_bill_payment",
            })

    return {
        "text": success_message,
        "next_actions": next_actions if next_actions else None,
    }


def compose_clarification(
    clarifications: list,
    intent: str = "",
) -> str:
    """Build clarification text from resolver output. Concise, no LLM."""
    if not clarifications:
        return "Mohon lengkapi informasi yang diperlukan."
    return "\n".join(clarifications)


def compose_validation_error(
    missing_fields: list,
    error_message: str = "",
) -> str:
    """Build validation error text. Concise."""
    if error_message:
        return error_message
    if missing_fields:
        joined = ", ".join(missing_fields)
        return f"Mohon lengkapi: {joined}"
    return "Data belum lengkap. Mohon lengkapi."
