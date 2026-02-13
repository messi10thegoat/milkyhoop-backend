"""
JSON schema validation for action_planner outputs.

Validates LLM responses against strict schemas to catch hallucinations
and malformed output before passing data downstream.
"""
import logging
from typing import Tuple

from jsonschema import validate, ValidationError

logger = logging.getLogger(__name__)


# =============================================================================
# SCHEMA: ActionPlan (final output of plan generation)
# =============================================================================
ACTION_PLAN_SCHEMA = {
    "type": "object",
    "required": ["action_id", "intent", "confidence"],
    "properties": {
        "action_id": {"type": "string"},
        "intent": {
            "type": "string",
            "enum": ["ACTION", "READ", "CONFIRM", "CANCEL", "UNCLEAR"],
        },
        "action_type": {"type": ["string", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "draft_payload": {"type": "object"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "missing_fields": {"type": "array", "items": {"type": "string"}},
        "clarification_needed": {"type": ["string", "null"]},
        "requires_confirmation": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "additionalProperties": False,
}


# =============================================================================
# SCHEMA: Classify Intent LLM response
# =============================================================================
CLASSIFY_RESULT_SCHEMA = {
    "type": "object",
    "required": ["intent", "confidence"],
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["ACTION", "READ", "CONFIRM", "CANCEL", "UNCLEAR"],
        },
        "action_type": {"type": ["string", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
}

VALID_ACTION_TYPES = {
    "CREATE_PURCHASE_INVOICE",
    "CREATE_SALES_INVOICE",
    "CREATE_VENDOR",
    "CREATE_CUSTOMER",
    "CREATE_PRODUCT",
    "MAKE_PAYMENT",
    "RECEIVE_PAYMENT",
    "UPDATE_VENDOR",
    "UPDATE_CUSTOMER",
    "UPDATE_PRODUCT",
    "CREATE_CREDIT_NOTE",
    "CREATE_EXPENSE",
    "BANK_TRANSFER",
    "CREATE_PURCHASE_ORDER",
}


# =============================================================================
# SCHEMA: Parse Invoice LLM response
# =============================================================================
PARSE_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {
            "type": "string",
            "enum": ["purchase_invoice", "sales_invoice"],
        },
        "counterparty_name": {"type": ["string", "null"]},
        "invoice_number": {"type": ["string", "null"]},
        "issue_date": {"type": ["string", "null"]},
        "due_date": {"type": ["string", "null"]},
        "tax_rate": {"type": "number"},
        "tax_inclusive": {"type": "boolean"},
        "notes": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "qty": {"type": "number", "minimum": 0},
                    "unit": {"type": "string"},
                    "price": {"type": "number", "minimum": 0},
                    "discount_percent": {"type": "number", "minimum": 0, "maximum": 100},
                },
            },
        },
        "missing_fields": {"type": "array", "items": {"type": "string"}},
        "clarification_needed": {"type": ["string", "null"]},
    },
}


# =============================================================================
# VALIDATION FUNCTIONS
# =============================================================================
def validate_action_plan(data: dict) -> Tuple[bool, str]:
    """
    Validate an ActionPlan dict against the schema.

    Returns:
        (is_valid, error_message) tuple.
    """
    try:
        validate(instance=data, schema=ACTION_PLAN_SCHEMA)
        return True, ""
    except ValidationError as e:
        msg = f"ActionPlan validation failed: {e.message}"
        logger.warning(msg)
        return False, msg


def validate_classify_result(data: dict) -> Tuple[bool, str]:
    """
    Validate a classify intent LLM response.

    Returns:
        (is_valid, error_message) tuple.
    """
    try:
        validate(instance=data, schema=CLASSIFY_RESULT_SCHEMA)

        # Additional business rule: ACTION intent must have valid action_type
        if data.get("intent") == "ACTION":
            action_type = data.get("action_type")
            if action_type not in VALID_ACTION_TYPES:
                return False, f"ACTION intent requires valid action_type, got: {action_type}"

        return True, ""
    except ValidationError as e:
        msg = f"Classify result validation failed: {e.message}"
        logger.warning(msg)
        return False, msg


def validate_parse_result(data: dict) -> Tuple[bool, str]:
    """
    Validate a parse invoice LLM response.

    Returns:
        (is_valid, error_message) tuple.
    """
    try:
        validate(instance=data, schema=PARSE_RESULT_SCHEMA)
        return True, ""
    except ValidationError as e:
        msg = f"Parse result validation failed: {e.message}"
        logger.warning(msg)
        return False, msg
