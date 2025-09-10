import json
from pydantic import ValidationError
from app.models.intent_output import IntentOutput

def validate_llm_output(llm_response: str) -> dict:
    """
    Validasi JSON output dari LLM.
    - Jika valid → return dict hasil parsing
    - Jika invalid → raise ValidationError
    """
    try:
        parsed = json.loads(llm_response)
        validated = IntentOutput(**parsed)  # Ini otomatis validasi struktur
        return validated.dict()
    except (json.JSONDecodeError, ValidationError) as e:
        raise ValueError(f"Invalid LLM output: {e}")
