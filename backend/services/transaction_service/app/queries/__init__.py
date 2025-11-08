"""
Queries module for Transaction Service
Exports all query functions for database operations
"""

from .product_analytics import get_top_products, get_low_sell_products

__all__ = [
    'get_top_products',
    'get_low_sell_products'
]