"""
Money Type Utilities - Deterministic Financial Calculations
Follows Iron Law 9: No floating-point approximation

Note: This system stores money in full IDR (no decimal places).
Database stores 111000 meaning IDR 111,000.00
"""
from decimal import Decimal, ROUND_HALF_UP
from typing import Union, Optional


def cents_to_decimal_string(amount: Optional[int]) -> Optional[str]:
    """Convert integer amount to decimal string with 2 decimal places.
    
    Note: Despite the name 'cents', our database stores full IDR amounts.
    This function adds .00 for OpenAPI v2 compliance.
    
    Examples:
        cents_to_decimal_string(416250) -> "416250.00"
        cents_to_decimal_string(100) -> "100.00"
        cents_to_decimal_string(0) -> "0.00"
        cents_to_decimal_string(None) -> None
    """
    if amount is None:
        return None
    # IDR has no decimal places - just add .00 for spec compliance
    return f"{amount}.00"


def decimal_string_to_cents(value: Union[str, int, float, None]) -> Optional[int]:
    """Convert decimal string to integer amount.
    
    For backward compatibility, accepts int and float as well.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value  # Already full amount (backward compatibility)
    if isinstance(value, float):
        # Truncate decimals for IDR
        return int(value)
    if isinstance(value, str):
        decimal_value = Decimal(value)
        # Truncate decimals for IDR
        return int(decimal_value.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    raise ValueError(f"Cannot convert {type(value)} to amount")


def format_money(amount: Optional[int], include_currency: bool = False, currency: str = "IDR") -> str:
    """Format money for display."""
    if amount is None:
        return "0.00" if not include_currency else f"{currency} 0.00"
    result = cents_to_decimal_string(amount)
    return result if not include_currency else f"{currency} {result}"
