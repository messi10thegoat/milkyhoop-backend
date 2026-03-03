"""
Finite State Machine for MilkyHoop chat action lifecycle.

States represent where we are in the action flow:
  IDLE → PLANNING → AWAITING_CONFIRMATION → EXECUTING → COMPLETED/FAILED → IDLE

This is a deterministic state machine — no LLM involvement in transitions.
"""
import logging
from enum import Enum
from typing import Optional, Dict, Set, Tuple

logger = logging.getLogger("unified_agent.fsm")


class FSMState(str, Enum):
    """Chat action lifecycle states."""
    IDLE = "IDLE"                                    # Normal chat, no active action
    PLANNING = "PLANNING"                            # Agent is using tools to build action
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"  # Action proposed, waiting user confirm/reject
    EXECUTING = "EXECUTING"                          # User confirmed, executing action
    COMPLETED = "COMPLETED"                          # Execution succeeded (auto-resets to IDLE)
    FAILED = "FAILED"                                # Execution failed (auto-resets to IDLE)


# Valid transitions: (from_state, to_state) -> True
VALID_TRANSITIONS: Set[Tuple[FSMState, FSMState]] = {
    # Normal flow
    (FSMState.IDLE, FSMState.PLANNING),                          # Agent starts tool calls
    (FSMState.PLANNING, FSMState.AWAITING_CONFIRMATION),         # propose_action called
    (FSMState.PLANNING, FSMState.IDLE),                          # Agent returns TEXT (no action needed)
    (FSMState.AWAITING_CONFIRMATION, FSMState.EXECUTING),        # User confirms
    (FSMState.AWAITING_CONFIRMATION, FSMState.IDLE),             # User cancels or timeout
    (FSMState.EXECUTING, FSMState.COMPLETED),                    # Execution success
    (FSMState.EXECUTING, FSMState.FAILED),                       # Execution error
    (FSMState.COMPLETED, FSMState.IDLE),                         # Auto-reset after response
    (FSMState.FAILED, FSMState.IDLE),                            # Auto-reset after response
    # Edge cases
    (FSMState.IDLE, FSMState.IDLE),                              # No-op (e.g. pure text chat)
    (FSMState.PLANNING, FSMState.PLANNING),                      # Multiple tool calls in same turn
}


class InvalidTransitionError(Exception):
    """Raised when an invalid FSM transition is attempted."""
    def __init__(self, current: FSMState, target: FSMState):
        self.current = current
        self.target = target
        super().__init__(f"Invalid FSM transition: {current.value} → {target.value}")


class FSMEngine:
    """Deterministic state machine for chat action lifecycle.
    
    Usage:
        fsm = FSMEngine(current_state=FSMState.IDLE)
        fsm.transition(FSMState.PLANNING)   # OK
        fsm.transition(FSMState.EXECUTING)  # InvalidTransitionError!
    """

    def __init__(self, current_state: FSMState = FSMState.IDLE):
        self._state = current_state
        self._trace: list = []  # Track transitions for observability

    @property
    def state(self) -> FSMState:
        return self._state

    @property
    def trace(self) -> list:
        return self._trace.copy()

    def can_transition(self, target: FSMState) -> bool:
        """Check if transition is valid without performing it."""
        return (self._state, target) in VALID_TRANSITIONS

    def transition(self, target: FSMState) -> FSMState:
        """Perform state transition. Raises InvalidTransitionError if invalid."""
        if not self.can_transition(target):
            raise InvalidTransitionError(self._state, target)
        
        old = self._state
        self._state = target
        self._trace.append(f"{old.value}→{target.value}")
        
        if old != target:  # Don't log no-ops
            logger.info(f"[FSM] {old.value} → {target.value}")
        
        return self._state

    def transition_safe(self, target: FSMState) -> FSMState:
        """Perform transition, logging warning instead of raising on invalid."""
        if not self.can_transition(target):
            logger.warning(
                f"[FSM] Invalid transition {self._state.value} → {target.value}, staying at {self._state.value}"
            )
            return self._state
        return self.transition(target)

    def reset(self) -> FSMState:
        """Force reset to IDLE (for error recovery)."""
        old = self._state
        self._state = FSMState.IDLE
        if old != FSMState.IDLE:
            self._trace.append(f"{old.value}→IDLE(reset)")
            logger.info(f"[FSM] Reset: {old.value} → IDLE")
        return self._state

    @property
    def is_idle(self) -> bool:
        return self._state == FSMState.IDLE

    @property
    def is_awaiting(self) -> bool:
        return self._state == FSMState.AWAITING_CONFIRMATION

    @property 
    def is_terminal(self) -> bool:
        """True if in COMPLETED or FAILED (should auto-reset)."""
        return self._state in (FSMState.COMPLETED, FSMState.FAILED)

    def to_dict(self) -> Dict:
        return {
            "state": self._state.value,
            "trace": self._trace,
        }
