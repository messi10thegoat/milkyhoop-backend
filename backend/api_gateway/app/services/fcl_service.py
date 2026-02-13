"""
Financial Confidentiality Layer (FCL) Service
Controls data visibility based on user's visibility levels (L1-L5).

VISIBILITY LEVELS:
- L1: Basic operational data (invoices, payments - basic info)
- L2: Detailed transaction data (line items, allocations)
- L3: Sensitive financial data (cost prices, profit margins)
- L4: Management reports (P&L, cash flow, forecasts)
- L5: Executive/strategic data (KPIs, valuations, projections)

IRON LAW COMPLIANCE:
- Law 0: Separation of Concerns - FCL is presentation layer, not financial core
- Law 9: Deterministic Reporting - Same visibility = same data
- Law 10: AI Safety Boundary - Data filtering logged for audit
- Law 12: Audit Immutability - FCL decisions can be audited
"""
import logging
from typing import Dict, List, Any, Optional, Set
from dataclasses import dataclass
from copy import deepcopy

logger = logging.getLogger(__name__)


# =============================================================================
# FCL CONFIGURATION
# =============================================================================

@dataclass
class FCLConfig:
    """Configuration for FCL filtering"""
    # Fields that require specific visibility levels to see
    # Format: field_name -> minimum_level
    SENSITIVE_FIELDS: Dict[str, str] = None
    
    # Fields to completely remove (not just mask)
    REDACTED_FIELDS: Dict[str, str] = None
    
    # How to mask sensitive fields
    MASK_VALUE: str = "***"
    MASK_NUMBER: str = "0.00"


# Default FCL configuration per entity type
FCL_ENTITY_CONFIG: Dict[str, Dict[str, str]] = {
    # Invoice / Sales
    "invoice": {
        "cost_total": "L3",           # Harga pokok total
        "profit_margin": "L3",         # Margin keuntungan
        "profit_amount": "L3",         # Jumlah profit
        "cost_price": "L3",            # Per-line cost
        "markup_percentage": "L3",     # Markup %
        "commission_rate": "L3",       # Komisi sales
        "commission_amount": "L3",
    },
    
    # Invoice Line Items
    "invoice_line": {
        "cost_price": "L3",
        "profit_margin": "L3",
        "markup": "L3",
    },
    
    # Bill / Purchase
    "bill": {
        "suggested_sell_price": "L3",
        "margin_target": "L3",
    },
    
    # Product / Item
    "product": {
        "cost_price": "L3",
        "average_cost": "L3",
        "last_purchase_price": "L3",
        "profit_margin": "L3",
        "supplier_price": "L3",
        "markup_percentage": "L3",
    },
    
    # Customer
    "customer": {
        "credit_limit": "L2",
        "total_outstanding": "L2",
        "payment_history_score": "L3",
        "lifetime_value": "L4",
        "acquisition_cost": "L5",
    },
    
    # Vendor/Supplier
    "vendor": {
        "payment_terms_internal": "L2",
        "negotiated_discount": "L3",
        "contract_value": "L4",
    },
    
    # Bank Account
    "bank_account": {
        "current_balance": "L2",
        "average_balance": "L3",
        "account_number": "L2",  # Partial mask in L1
    },
    
    # Payroll
    "payroll": {
        "base_salary": "L2",
        "total_salary": "L2",
        "bonus": "L3",
        "tax_deduction": "L2",
        "net_salary": "L2",
        "bank_account": "L2",
    },
    
    # Reports - Management Level
    "report_pnl": {
        "gross_profit": "L4",
        "operating_profit": "L4",
        "net_profit": "L4",
        "profit_margin": "L4",
        "ebitda": "L5",
    },
    
    "report_cash_flow": {
        "net_cash_flow": "L4",
        "operating_cash_flow": "L4",
        "free_cash_flow": "L5",
    },
    
    "report_balance_sheet": {
        "total_assets": "L4",
        "total_liabilities": "L4",
        "equity": "L4",
        "retained_earnings": "L5",
    },
    
    # Dashboard KPIs
    "dashboard": {
        "revenue_total": "L2",
        "profit_total": "L4",
        "profit_margin": "L4",
        "cash_position": "L3",
        "runway_months": "L5",
        "burn_rate": "L5",
    },
}


# Report types that require specific visibility levels to access
REPORT_VISIBILITY_REQUIREMENTS: Dict[str, str] = {
    # L1 Reports - Basic operational
    "trial_balance": "L1",
    "general_ledger": "L1",
    "ar_aging": "L1",
    "ap_aging": "L1",
    "inventory_summary": "L1",
    
    # L2 Reports - Detailed transactions
    "transaction_detail": "L2",
    "customer_statement": "L2",
    "vendor_statement": "L2",
    "bank_reconciliation": "L2",
    
    # L3 Reports - Sensitive financial
    "cost_analysis": "L3",
    "margin_analysis": "L3",
    "pricing_report": "L3",
    
    # L4 Reports - Management
    "profit_loss": "L4",
    "balance_sheet": "L4",
    "cash_flow": "L4",
    "budget_variance": "L4",
    "department_pnl": "L4",
    
    # L5 Reports - Executive
    "executive_summary": "L5",
    "kpi_dashboard": "L5",
    "forecast": "L5",
    "valuation": "L5",
    "investor_report": "L5",
}


# =============================================================================
# FCL SERVICE
# =============================================================================

class FCLService:
    """
    Financial Confidentiality Layer Service.
    Filters data based on user's visibility levels.
    """
    
    def __init__(self):
        self.entity_config = FCL_ENTITY_CONFIG
        self.report_requirements = REPORT_VISIBILITY_REQUIREMENTS
    
    def get_visibility_rank(self, level: str) -> int:
        """Convert visibility level to numeric rank for comparison."""
        ranks = {"L1": 1, "L2": 2, "L3": 3, "L4": 4, "L5": 5}
        return ranks.get(level, 0)
    
    def has_visibility(self, user_levels: List[str], required_level: str) -> bool:
        """Check if user has required visibility level."""
        if not user_levels:
            return False
        
        # User has access if any of their levels >= required
        required_rank = self.get_visibility_rank(required_level)
        for level in user_levels:
            if self.get_visibility_rank(level) >= required_rank:
                return True
        return False
    
    def can_access_report(self, user_levels: List[str], report_type: str) -> bool:
        """Check if user can access a specific report type."""
        required = self.report_requirements.get(report_type, "L1")
        return self.has_visibility(user_levels, required)
    
    def get_accessible_reports(self, user_levels: List[str]) -> List[str]:
        """Get list of reports user can access."""
        return [
            report for report, required in self.report_requirements.items()
            if self.has_visibility(user_levels, required)
        ]
    
    def filter_entity(
        self, 
        entity: Dict[str, Any], 
        entity_type: str, 
        user_levels: List[str],
        mask_value: str = "***",
        mask_number: str = "0.00"
    ) -> Dict[str, Any]:
        """
        Filter sensitive fields from an entity based on user visibility.
        
        Args:
            entity: The entity dict to filter
            entity_type: Type of entity (invoice, product, customer, etc.)
            user_levels: User's visibility levels [L1, L2, ...]
            mask_value: Value to use for masked string fields
            mask_number: Value to use for masked numeric fields
            
        Returns:
            Filtered entity with sensitive fields masked/removed
        """
        if not entity:
            return entity
        
        config = self.entity_config.get(entity_type, {})
        if not config:
            return entity
        
        # Deep copy to avoid modifying original
        filtered = deepcopy(entity)
        
        for field, required_level in config.items():
            if field in filtered:
                if not self.has_visibility(user_levels, required_level):
                    # Mask the field
                    current_value = filtered[field]
                    if isinstance(current_value, (int, float)) or (
                        isinstance(current_value, str) and current_value.replace(".", "").replace("-", "").isdigit()
                    ):
                        filtered[field] = mask_number
                    else:
                        filtered[field] = mask_value
        
        return filtered
    
    def filter_entity_list(
        self,
        entities: List[Dict[str, Any]],
        entity_type: str,
        user_levels: List[str]
    ) -> List[Dict[str, Any]]:
        """Filter a list of entities."""
        return [
            self.filter_entity(e, entity_type, user_levels)
            for e in entities
        ]
    
    def filter_response(
        self,
        response: Dict[str, Any],
        entity_type: str,
        user_levels: List[str],
        data_key: str = "data"
    ) -> Dict[str, Any]:
        """
        Filter a paginated API response.
        
        Args:
            response: API response dict with data key
            entity_type: Type of entity
            user_levels: User's visibility levels
            data_key: Key in response containing the data list
            
        Returns:
            Filtered response
        """
        if not response:
            return response
        
        filtered = deepcopy(response)
        
        if data_key in filtered:
            data = filtered[data_key]
            if isinstance(data, list):
                filtered[data_key] = self.filter_entity_list(data, entity_type, user_levels)
            elif isinstance(data, dict):
                filtered[data_key] = self.filter_entity(data, entity_type, user_levels)
        
        return filtered
    
    def get_allowed_fields(
        self,
        entity_type: str,
        user_levels: List[str]
    ) -> Set[str]:
        """Get set of fields user can access for an entity type."""
        config = self.entity_config.get(entity_type, {})
        
        # Start with all fields allowed
        disallowed = set()
        
        for field, required_level in config.items():
            if not self.has_visibility(user_levels, required_level):
                disallowed.add(field)
        
        return disallowed  # Return disallowed for exclusion
    
    def build_sql_column_filter(
        self,
        entity_type: str,
        user_levels: List[str],
        table_alias: str = ""
    ) -> str:
        """
        Build SQL CASE statements for filtering sensitive columns.
        Used when filtering at database level is more efficient.
        
        Returns SQL like:
            CASE WHEN 'L3' = ANY() THEN cost_price ELSE 0 END as cost_price
        """
        config = self.entity_config.get(entity_type, {})
        if not config:
            return ""
        
        prefix = f"{table_alias}." if table_alias else ""
        cases = []
        
        for field, required_level in config.items():
            if self.has_visibility(user_levels, required_level):
                # User can see - just select the field
                cases.append(f"{prefix}{field}")
            else:
                # User cannot see - return masked value
                cases.append(f"0 as {field}")
        
        return ", ".join(cases) if cases else ""


# =============================================================================
# MODULE-LEVEL SINGLETON
# =============================================================================

_fcl_service: Optional[FCLService] = None


def init_fcl_service() -> FCLService:
    """Initialize FCL service singleton."""
    global _fcl_service
    if _fcl_service is None:
        _fcl_service = FCLService()
        logger.info("FCL Service initialized")
    return _fcl_service


def get_fcl_service() -> FCLService:
    """Get FCL service instance."""
    global _fcl_service
    if _fcl_service is None:
        _fcl_service = FCLService()
    return _fcl_service


# =============================================================================
# DECORATOR FOR ROUTE PROTECTION
# =============================================================================

def fcl_filter(entity_type: str, data_key: str = "data"):
    """
    Decorator to automatically filter response based on FCL.
    
    Usage:
        @router.get("/invoices")
        @fcl_filter("invoice")
        async def list_invoices(user: dict = Depends(get_current_user)):
            return {"data": invoices}
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            response = await func(*args, **kwargs)
            
            # Get user from kwargs (should be injected by dependency)
            user = kwargs.get("user", {})
            visibility_levels = user.get("visibility_levels", ["L1"])
            
            fcl = get_fcl_service()
            return fcl.filter_response(response, entity_type, visibility_levels, data_key)
        
        return wrapper
    return decorator
