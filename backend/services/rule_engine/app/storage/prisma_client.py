"""
rule_engine/app/storage/prisma_client.py

Prisma client helper for rule_engine

Author: MilkyHoop Team
Version: 1.0.0
"""

import logging
from milkyhoop_prisma import Prisma

logger = logging.getLogger(__name__)

# Global singleton instance
_prisma_client: Prisma = None


async def get_prisma() -> Prisma:
    """
    Get global Prisma client instance

    Returns:
        Prisma client instance
    """
    global _prisma_client

    if _prisma_client is None:
        _prisma_client = Prisma()
        await _prisma_client.connect()
        logger.info("Global Prisma client connected")

    return _prisma_client


async def disconnect_prisma():
    """Disconnect global Prisma client"""
    global _prisma_client

    if _prisma_client is not None:
        await _prisma_client.disconnect()
        _prisma_client = None
        logger.info("Global Prisma client disconnected")
