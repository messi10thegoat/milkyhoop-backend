"""
Financial Queries Helper Functions
Date parsing and where clause building for reporting queries
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


def parse_periode_pelaporan(periode: str) -> Dict[str, int]:
    """
    Parse periode_pelaporan string to start/end timestamps.
    
    Formats supported:
    - "2025-10" → October 2025 (monthly)
    - "2025-Q3" → Q3 2025 (quarterly)
    - "2025" → Full year 2025 (yearly)
    
    Returns:
        Dict with 'start' and 'end' Unix timestamps (milliseconds)
    """
    try:
        if '-Q' in periode:  # Quarterly: "2025-Q3"
            year, quarter = periode.split('-Q')
            year = int(year)
            quarter = int(quarter)
            
            quarter_months = {
                1: (1, 3),   # Q1: Jan-Mar
                2: (4, 6),   # Q2: Apr-Jun
                3: (7, 9),   # Q3: Jul-Sep
                4: (10, 12)  # Q4: Oct-Dec
            }
            
            start_month, end_month = quarter_months[quarter]
            start = datetime(year, start_month, 1)
            
            # End of quarter (last day of end_month)
            if end_month == 12:
                end = datetime(year + 1, 1, 1) - timedelta(seconds=1)
            else:
                end = datetime(year, end_month + 1, 1) - timedelta(seconds=1)
                
        elif '-' in periode:  # Monthly: "2025-10"
            year, month = periode.split('-')
            year, month = int(year), int(month)
            start = datetime(year, month, 1)
            
            # End of month
            if month == 12:
                end = datetime(year + 1, 1, 1) - timedelta(seconds=1)
            else:
                end = datetime(year, month + 1, 1) - timedelta(seconds=1)
                
        else:  # Yearly: "2025"
            year = int(periode)
            start = datetime(year, 1, 1)
            end = datetime(year, 12, 31, 23, 59, 59)
        
        return {
            'start': int(start.timestamp() * 1000),
            'end': int(end.timestamp() * 1000)
        }
    except Exception as e:
        logger.error(f"Failed to parse periode_pelaporan '{periode}': {str(e)}")
        raise ValueError(f"Invalid periode_pelaporan format: {periode}")


def build_where_clause(tenant_id: str, periode: str, start_date: Optional[int], end_date: Optional[int]) -> Dict[str, Any]:
    """
    Build Prisma where clause for transaction queries.
    
    Args:
        tenant_id: Tenant ID for RLS
        periode: Period string (e.g., "2025-10")
        start_date: Override start timestamp (milliseconds)
        end_date: Override end timestamp (milliseconds)
    
    Returns:
        Prisma where clause dict
    """
    where = {
        'tenantId': tenant_id,
        'status': {'in': ['draft', 'approved']}  # Support both auto and manual approval modes
    }
    
    # Use explicit start/end dates if provided, otherwise parse periode
    if start_date and end_date:
        where['timestamp'] = {'gte': start_date, 'lte': end_date}
    elif periode:
        date_range = parse_periode_pelaporan(periode)
        where['timestamp'] = {'gte': date_range['start'], 'lte': date_range['end']}
    
    return where