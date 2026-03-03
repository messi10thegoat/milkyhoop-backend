"""
Tutorial Registry — Structured tutorial definitions for LLM-narrated walkthroughs.

Single source of truth for:
- Tutorial step sequences (prerequisites, linked actions, completion triggers)
- Signal words for intent detection
- Auto-trigger conditions for new users

The LLM reads these definitions and narrates each step conversationally.
Tutorial state (current step, completed steps) is tracked in chat_session_state.

Pattern mirrors DirectAction registry: structured config, signal words, auto-wiring.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TutorialStep:
    step_key: str                         # LLM narration key, e.g. "create_first_customer"
    step_index: int                       # 1-based for display ("Step 2 of 5")
    prerequisites_check: str = "none"     # Existing GET endpoint, e.g. "/api/customers?limit=1"
    prerequisite_condition: str = "none"  # "empty" | "non_empty" | "none"
    linked_action: Optional[str] = None   # DirectAction key, e.g. "create_customer"
    completion_trigger: str = "auto"      # "data_changed:<entity>" | "user_confirm" | "auto"
    skippable: bool = True


@dataclass
class TutorialConfig:
    tutorial_key: str                     # "onboarding"
    display_key: str                      # "getting_started" — LLM knows how to introduce
    total_steps: int
    steps: list[TutorialStep] = field(default_factory=list)
    signal_words: list[str] = field(default_factory=list)
    auto_trigger: bool = False
    auto_trigger_condition: str = ""
    cooldown_hours: int = 72


# ---------------------------------------------------------------------------
# Registry — 6 tutorials (1 onboarding + 5 per-module)
# ---------------------------------------------------------------------------

TUTORIAL_REGISTRY: dict[str, TutorialConfig] = {
    "onboarding": TutorialConfig(
        tutorial_key="onboarding",
        display_key="getting_started",
        total_steps=5,
        auto_trigger=True,
        auto_trigger_condition="all_empty",
        cooldown_hours=72,
        signal_words=[
            "mulai", "getting started", "baru pertama", "tutorial",
            "ajarin", "teach me", "how to start", "onboarding",
            "cara pakai", "how to use", "baru daftar", "pemula",
        ],
        steps=[
            TutorialStep(
                step_key="welcome",
                step_index=1,
                completion_trigger="auto",
            ),
            TutorialStep(
                step_key="create_first_customer",
                step_index=2,
                prerequisites_check="/api/customers?limit=1",
                prerequisite_condition="empty",
                linked_action="create_customer",
                completion_trigger="data_changed:customer",
            ),
            TutorialStep(
                step_key="create_first_item",
                step_index=3,
                prerequisites_check="/api/items?limit=1",
                prerequisite_condition="empty",
                linked_action="create_item",
                completion_trigger="data_changed:item",
            ),
            TutorialStep(
                step_key="create_first_invoice",
                step_index=4,
                prerequisites_check="/api/sales-invoices?limit=1",
                prerequisite_condition="empty",
                linked_action=None,
                completion_trigger="data_changed:sales_invoice",
            ),
            TutorialStep(
                step_key="dashboard_overview",
                step_index=5,
                completion_trigger="user_confirm",
            ),
        ],
    ),

    "tutorial_invoicing": TutorialConfig(
        tutorial_key="tutorial_invoicing",
        display_key="invoicing_guide",
        total_steps=3,
        signal_words=[
            "gimana invoice", "cara bikin faktur", "how to invoice",
            "ajarin invoice", "tutorial faktur", "bikin faktur",
        ],
        steps=[
            TutorialStep(
                step_key="invoice_what_is",
                step_index=1,
                completion_trigger="auto",
            ),
            TutorialStep(
                step_key="invoice_create_flow",
                step_index=2,
                completion_trigger="user_confirm",
            ),
            TutorialStep(
                step_key="invoice_payment_tracking",
                step_index=3,
                completion_trigger="user_confirm",
            ),
        ],
    ),

    "tutorial_bank_recon": TutorialConfig(
        tutorial_key="tutorial_bank_recon",
        display_key="bank_recon_guide",
        total_steps=3,
        signal_words=[
            "gimana rekonsiliasi", "cara rekon", "how to reconcile",
            "ajarin rekon", "tutorial rekon", "bank reconciliation",
        ],
        steps=[
            TutorialStep(
                step_key="rekon_what_is",
                step_index=1,
                completion_trigger="auto",
            ),
            TutorialStep(
                step_key="rekon_upload_statement",
                step_index=2,
                completion_trigger="user_confirm",
            ),
            TutorialStep(
                step_key="rekon_review_matches",
                step_index=3,
                completion_trigger="user_confirm",
            ),
        ],
    ),

    "tutorial_expenses": TutorialConfig(
        tutorial_key="tutorial_expenses",
        display_key="expense_guide",
        total_steps=2,
        signal_words=[
            "cara input biaya", "gimana expense", "how to expense",
            "ajarin biaya", "tutorial pengeluaran", "catat biaya",
        ],
        steps=[
            TutorialStep(
                step_key="expense_what_is",
                step_index=1,
                completion_trigger="auto",
            ),
            TutorialStep(
                step_key="expense_create_flow",
                step_index=2,
                completion_trigger="user_confirm",
            ),
        ],
    ),

    "tutorial_reports": TutorialConfig(
        tutorial_key="tutorial_reports",
        display_key="reports_guide",
        total_steps=3,
        signal_words=[
            "cara baca neraca", "gimana laporan", "how to read reports",
            "ajarin laporan", "tutorial neraca", "baca laba rugi",
        ],
        steps=[
            TutorialStep(
                step_key="reports_overview",
                step_index=1,
                completion_trigger="auto",
            ),
            TutorialStep(
                step_key="reports_balance_sheet",
                step_index=2,
                completion_trigger="user_confirm",
            ),
            TutorialStep(
                step_key="reports_profit_loss",
                step_index=3,
                completion_trigger="user_confirm",
            ),
        ],
    ),

    "tutorial_payments": TutorialConfig(
        tutorial_key="tutorial_payments",
        display_key="payments_guide",
        total_steps=2,
        signal_words=[
            "cara terima pembayaran", "bayar tagihan", "how to receive payment",
            "ajarin pembayaran", "tutorial payment", "terima uang",
        ],
        steps=[
            TutorialStep(
                step_key="payments_receive",
                step_index=1,
                completion_trigger="user_confirm",
            ),
            TutorialStep(
                step_key="payments_send",
                step_index=2,
                completion_trigger="user_confirm",
            ),
        ],
    ),
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def get_tutorial(tutorial_key: str) -> Optional[TutorialConfig]:
    """Return a TutorialConfig by key, or None if not found."""
    return TUTORIAL_REGISTRY.get(tutorial_key)


def get_tutorial_step(tutorial_key: str, step_index: int) -> Optional[TutorialStep]:
    """Return a specific TutorialStep by tutorial key and 1-based step index, or None."""
    config = TUTORIAL_REGISTRY.get(tutorial_key)
    if not config:
        return None
    for step in config.steps:
        if step.step_index == step_index:
            return step
    return None


def get_all_tutorial_signal_words() -> dict[str, str]:
    """
    Return a flat mapping of signal word -> "tutorial:<key>" for intent detection.

    Example: {"mulai": "tutorial:onboarding", "how to invoice": "tutorial:tutorial_invoicing"}
    """
    result: dict[str, str] = {}
    for key, config in TUTORIAL_REGISTRY.items():
        for word in config.signal_words:
            result[word] = f"tutorial:{key}"
    return result


def list_available_tutorials() -> list[dict]:
    """
    Return a summary list of all tutorials for display/selection.

    Each entry: {key, display_key, total_steps, auto_trigger, signal_words_sample}
    """
    result = []
    for key, config in TUTORIAL_REGISTRY.items():
        result.append({
            "key": config.tutorial_key,
            "display_key": config.display_key,
            "total_steps": config.total_steps,
            "auto_trigger": config.auto_trigger,
            "signal_words_sample": config.signal_words[:3],
        })
    return result
