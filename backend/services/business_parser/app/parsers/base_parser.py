"""
Base parser abstract class

Defines the interface for all parser implementations.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class BaseParser(ABC):
    """
    Abstract base class for intent parsers.

    All parsers must implement the parse() method.
    """

    @abstractmethod
    def parse(self, text: str, context: Optional[str] = None, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Parse text and extract intent + entities.

        Args:
            text: User input text
            context: Optional conversation context
            tenant_id: Optional tenant identifier

        Returns:
            Dict with keys: intent, entities, confidence, model_used
            Returns None if parser cannot handle this input
        """
        pass