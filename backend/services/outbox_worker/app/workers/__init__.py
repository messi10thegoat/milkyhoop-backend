"""
outbox_worker/app/workers/__init__.py

Workers module for outbox processing
"""

from .outbox_processor import OutboxProcessor

__all__ = ["OutboxProcessor"]