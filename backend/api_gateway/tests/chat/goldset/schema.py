from dataclasses import dataclass, field
from enum import Enum


class Tier(str, Enum):
    A = "A"  # lookup / transactional / CRUD — fast deterministic
    B = "B"  # reasoning / analytical / what-if / why


class Category(str, Enum):
    LOOKUP = "lookup"
    CRUD = "crud"
    REASONING = "reasoning"
    WHATIF = "whatif"
    WHY = "why"
    FOLLOWUP = "followup"
    ADVERSARIAL = "adversarial"


# assertion kinds (string constants)
A_INTENT_IN = "intent_in"  # value: list[str] acceptable intents
A_TIER = "tier_equals"  # value: Tier
A_TEXT_CONTAINS = "text_contains"  # value: str (case-insensitive)
A_TEXT_CONTAINS_ANY = "text_contains_any"  # value: list[str]
A_TEXT_NOT_CONTAINS = "text_not_contains"  # value: str
A_HAS_TRACE = "has_trace"  # value: True
A_IS_CONFIRMATION = (
    "is_confirmation"  # value: True (message_type DIRECT_ACTION_PREVIEW)
)
A_ABSTAINS = "abstains"  # value: True (admits uncertainty)


@dataclass
class Turn:
    query: str
    asserts: list = field(default_factory=list)  # list[tuple(kind, value)]


@dataclass
class GoldCase:
    id: str
    category: Category
    turns: list  # list[Turn]
    why: str = ""
