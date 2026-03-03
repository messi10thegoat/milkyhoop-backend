"""
In-memory circuit breaker per tool.
Tracks failures in a sliding window, trips OPEN on threshold, recovers via HALF_OPEN.
Resets on process restart (acceptable for this use case).
"""
import time
import logging
import threading
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class ToolCircuit:
    """State for a single tool's circuit."""
    state: CircuitState = CircuitState.CLOSED
    failure_timestamps: List[float] = field(default_factory=list)
    opened_at: float = 0.0
    half_open_attempt_in_flight: bool = False


class CircuitBreaker:
    """
    Per-tool circuit breaker with sliding window failure tracking.
    
    - CLOSED: normal operation, track failures
    - OPEN: reject all requests, wait recovery_seconds
    - HALF_OPEN: allow 1 probe request
    """

    FRIENDLY_ERROR = "Layanan sedang mengalami gangguan. Coba lagi dalam beberapa saat."

    def __init__(
        self,
        failure_threshold: int = 5,
        window_seconds: float = 300.0,
        recovery_seconds: float = 30.0,
    ):
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.recovery_seconds = recovery_seconds
        self._circuits: Dict[str, ToolCircuit] = {}
        self._lock = threading.Lock()

    def _get_circuit(self, tool_name: str) -> ToolCircuit:
        if tool_name not in self._circuits:
            self._circuits[tool_name] = ToolCircuit()
        return self._circuits[tool_name]

    def _prune_old_failures(self, circuit: ToolCircuit, now: float):
        cutoff = now - self.window_seconds
        circuit.failure_timestamps = [t for t in circuit.failure_timestamps if t > cutoff]

    def get_state(self, tool_name: str) -> str:
        with self._lock:
            circuit = self._get_circuit(tool_name)
            now = time.time()

            if circuit.state == CircuitState.OPEN:
                if now - circuit.opened_at >= self.recovery_seconds:
                    circuit.state = CircuitState.HALF_OPEN
                    circuit.half_open_attempt_in_flight = False
                    logger.info(f"[CircuitBreaker] {tool_name}: OPEN -> HALF_OPEN (recovery elapsed)")

            return circuit.state.value

    def can_execute(self, tool_name: str) -> bool:
        with self._lock:
            circuit = self._get_circuit(tool_name)
            now = time.time()

            if circuit.state == CircuitState.CLOSED:
                return True

            if circuit.state == CircuitState.OPEN:
                if now - circuit.opened_at >= self.recovery_seconds:
                    circuit.state = CircuitState.HALF_OPEN
                    circuit.half_open_attempt_in_flight = False
                    logger.info(f"[CircuitBreaker] {tool_name}: OPEN -> HALF_OPEN")

            if circuit.state == CircuitState.HALF_OPEN:
                if not circuit.half_open_attempt_in_flight:
                    circuit.half_open_attempt_in_flight = True
                    logger.info(f"[CircuitBreaker] {tool_name}: HALF_OPEN probe allowed")
                    return True
                return False  # Another probe already in flight

            return False  # Still OPEN

    def record_success(self, tool_name: str):
        with self._lock:
            circuit = self._get_circuit(tool_name)
            if circuit.state == CircuitState.HALF_OPEN:
                circuit.state = CircuitState.CLOSED
                circuit.failure_timestamps.clear()
                circuit.half_open_attempt_in_flight = False
                logger.info(f"[CircuitBreaker] {tool_name}: HALF_OPEN -> CLOSED (probe succeeded)")
            # In CLOSED state, just leave as is

    def record_failure(self, tool_name: str):
        with self._lock:
            circuit = self._get_circuit(tool_name)
            now = time.time()

            if circuit.state == CircuitState.HALF_OPEN:
                circuit.state = CircuitState.OPEN
                circuit.opened_at = now
                circuit.half_open_attempt_in_flight = False
                logger.info(f"[CircuitBreaker] {tool_name}: HALF_OPEN -> OPEN (probe failed)")
                return

            # CLOSED state: track failure
            circuit.failure_timestamps.append(now)
            self._prune_old_failures(circuit, now)

            if len(circuit.failure_timestamps) >= self.failure_threshold:
                circuit.state = CircuitState.OPEN
                circuit.opened_at = now
                logger.warning(f"[CircuitBreaker] {tool_name}: CLOSED -> OPEN ({len(circuit.failure_timestamps)} failures in window)")

    def reset(self, tool_name: str):
        """Manual reset for testing."""
        with self._lock:
            if tool_name in self._circuits:
                del self._circuits[tool_name]


# Singleton instance -- shared across the process
circuit_breaker = CircuitBreaker()
