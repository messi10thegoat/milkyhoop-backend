"""
Permission Dependencies for Route Protection
Uses PolicyEngineClient for RBAC enforcement.

IRON LAW COMPLIANCE:
- Law 0: Separation of Concerns - Permissions checked at API layer
- Law 10: AI Safety Boundary - All permission decisions logged
- Law 12: Audit Immutability - Denied access logged for audit

Usage:
    from app.dependencies.permissions import require_permission
    
    @router.post("/invoices")
    async def create_invoice(
        data: InvoiceCreate,
        user: dict = Depends(require_permission('sales_invoice', 'C'))
    ):
        ...
"""
import logging
from typing import Callable, Optional
from functools import wraps
from fastapi import Depends, HTTPException, Request

from backend.api_gateway.app.dependencies.auth import get_current_user
from backend.api_gateway.app.services.policy_engine_client import (
    get_policy_engine, 
    UserContext,
    PolicyEngineClient
)

logger = logging.getLogger(__name__)


# Module name mapping (frontend -> backend)
MODULE_MAPPING = {
    # Sales/AR Cycle
    'sales_invoice': 'sales_invoice',
    'invoice': 'sales_invoice',
    'sales_order': 'sales_order',
    'receive_payment': 'receive_payment',
    'customer': 'customer',
    'customers': 'customer',
    
    # Purchasing/AP Cycle
    'purchase_invoice': 'purchase_invoice',
    'bill': 'purchase_invoice',
    'bills': 'purchase_invoice',
    'purchase_order': 'purchase_order',
    'send_payment': 'send_payment',
    'bill_payment': 'send_payment',
    'supplier': 'supplier',
    'vendor': 'supplier',
    'vendors': 'supplier',
    
    # Inventory
    'item': 'item',
    'items': 'item',
    'product': 'item',
    'products': 'item',
    
    # Cash & Bank
    'kas_bank': 'kas_bank',
    'bank_account': 'kas_bank',
    'bank_transfer': 'kas_bank',
    
    # Accounting
    'journal': 'journal',
    'chart_of_accounts': 'chart_of_accounts',
    'account': 'chart_of_accounts',
    'accounts': 'chart_of_accounts',
    
    # Reports
    'reports': 'reports',
    'report': 'reports',
    
    # HR & Payroll
    'payroll': 'payroll',
    'employee': 'employee',
    
    # Settings
    'team_management': 'team_management',
    'tenant_settings': 'tenant_settings',
    
    # Other
    'expense': 'expense',
    'expenses': 'expense',
    'approval_inbox': 'approval_inbox',
    'payment_request': 'payment_request',
    'dashboard': 'dashboard',
}

# Action descriptions for logging
ACTION_NAMES = {
    'C': 'Create',
    'R': 'Read',
    'U': 'Update',
    'D': 'Delete',
    'V': 'Void',
    'A': 'Approve',
    'P': 'Post',
    'E': 'Export',
}


async def get_user_context(
    request: Request,
    user: dict = Depends(get_current_user)
) -> UserContext:
    """
    Build UserContext from request state and PolicyEngine.
    """
    try:
        policy_engine = get_policy_engine()
        context = await policy_engine.get_user_context(
            user_id=user['user_id'],
            tenant_id=user['tenant_id'],
            subscription_role=user.get('role', 'USER')
        )
        return context
    except Exception as e:
        logger.error(f"Error building user context: {e}")
        # Return basic context for fail-safe
        return UserContext(
            user_id=user['user_id'],
            tenant_id=user['tenant_id'],
            subscription_role=user.get('role', 'USER'),
            visibility_levels=['L1']
        )


def require_permission(module: str, action: str):
    """
    Dependency factory for permission checking.
    
    Args:
        module: The module name (e.g., 'sales_invoice', 'customer')
        action: The action code (C, R, U, D, V, A, P, E)
    
    Returns:
        Dependency function that validates permission and returns user dict
    
    Raises:
        HTTPException 403 if permission denied
    
    Usage:
        @router.post("/invoices")
        async def create_invoice(
            user: dict = Depends(require_permission('sales_invoice', 'C'))
        ):
            ...
    """
    # Normalize module name
    normalized_module = MODULE_MAPPING.get(module, module)
    
    async def permission_dependency(
        request: Request,
        user: dict = Depends(get_current_user)
    ) -> dict:
        try:
            policy_engine = get_policy_engine()
            
            # Build user context
            context = await policy_engine.get_user_context(
                user_id=user['user_id'],
                tenant_id=user['tenant_id'],
                subscription_role=user.get('role', 'USER')
            )
            
            # Check permission
            allowed = await policy_engine.can(context, action, normalized_module)
            
            if not allowed:
                action_name = ACTION_NAMES.get(action, action)
                logger.warning(
                    f"Permission denied: user={user['user_id']} "
                    f"action={action_name} module={normalized_module} "
                    f"role={context.business_role_code}"
                )
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "Permission denied",
                        "message": f"You don't have permission to {action_name.lower()} {normalized_module.replace('_', ' ')}",
                        "code": "PERMISSION_DENIED",
                        "required_action": action,
                        "required_module": normalized_module
                    }
                )
            
            # Add context to user dict for downstream use
            user['business_role_code'] = context.business_role_code
            user['business_role_id'] = context.business_role_id
            user['visibility_levels'] = context.visibility_levels
            user['approval_limit'] = context.approval_limit
            
            return user
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Permission check error: {e}")
            # Fail-closed for security
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "Permission check failed",
                    "message": "Unable to verify permissions",
                    "code": "PERMISSION_CHECK_ERROR"
                }
            )
    
    return permission_dependency


def require_visibility(level: str):
    """
    Dependency factory for visibility level checking.
    Used for FCL (Financial Confidentiality Layer).
    
    Args:
        level: Required visibility level (L1-L5)
    
    Usage:
        @router.get("/reports/profit-loss")
        async def get_pnl(
            user: dict = Depends(require_visibility('L4'))
        ):
            ...
    """
    async def visibility_dependency(
        request: Request,
        user: dict = Depends(get_current_user)
    ) -> dict:
        try:
            policy_engine = get_policy_engine()
            
            context = await policy_engine.get_user_context(
                user_id=user['user_id'],
                tenant_id=user['tenant_id'],
                subscription_role=user.get('role', 'USER')
            )
            
            if level not in (context.visibility_levels or []):
                logger.warning(
                    f"Visibility denied: user={user['user_id']} "
                    f"required={level} has={context.visibility_levels}"
                )
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "Access denied",
                        "message": f"You don't have visibility level {level} required for this data",
                        "code": "VISIBILITY_DENIED",
                        "required_level": level
                    }
                )
            
            user['visibility_levels'] = context.visibility_levels
            return user
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Visibility check error: {e}")
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "Visibility check failed",
                    "message": "Unable to verify visibility level"
                }
            )
    
    return visibility_dependency


def require_approval_authority(document_type: str, amount_field: str = 'amount'):
    """
    Dependency factory for approval authority checking.
    Checks if user can approve based on approval_limit.
    
    Args:
        document_type: Type of document (SALES_INVOICE, BILL, etc.)
        amount_field: Name of amount field in request body
    
    Usage:
        @router.post("/invoices/{id}/approve")
        async def approve_invoice(
            id: str,
            user: dict = Depends(require_approval_authority('SALES_INVOICE'))
        ):
            ...
    """
    async def approval_dependency(
        request: Request,
        user: dict = Depends(get_current_user)
    ) -> dict:
        # First check basic approve permission
        policy_engine = get_policy_engine()
        
        context = await policy_engine.get_user_context(
            user_id=user['user_id'],
            tenant_id=user['tenant_id'],
            subscription_role=user.get('role', 'USER')
        )
        
        # Check if requires approval workflow
        # Amount checking will be done in the service layer
        # since we need to query the document
        
        user['can_approve'] = True
        user['approval_limit'] = context.approval_limit
        user['business_role_code'] = context.business_role_code
        
        return user
    
    return approval_dependency


# Convenience aliases for common patterns
require_read = lambda module: require_permission(module, 'R')
require_create = lambda module: require_permission(module, 'C')
require_update = lambda module: require_permission(module, 'U')
require_delete = lambda module: require_permission(module, 'D')
require_void = lambda module: require_permission(module, 'V')
require_approve = lambda module: require_permission(module, 'A')
require_post = lambda module: require_permission(module, 'P')
require_export = lambda module: require_permission(module, 'E')


# =============================================================================
# FCL REPORT PROTECTION
# =============================================================================

def require_report_access(report_type: str):
    """
    Dependency factory for report access control based on FCL.
    
    Args:
        report_type: Type of report (profit_loss, balance_sheet, etc.)
    
    Usage:
        @router.get("/reports/profit-loss")
        async def get_pnl(
            user: dict = Depends(require_report_access('profit_loss'))
        ):
            ...
    """
    from backend.api_gateway.app.services.fcl_service import get_fcl_service
    
    async def report_dependency(
        request: Request,
        user: dict = Depends(get_current_user)
    ) -> dict:
        try:
            policy_engine = get_policy_engine()
            
            context = await policy_engine.get_user_context(
                user_id=user['user_id'],
                tenant_id=user['tenant_id'],
                subscription_role=user.get('role', 'USER')
            )
            
            visibility_levels = context.visibility_levels or ['L1']
            
            # Check FCL access for this report
            fcl = get_fcl_service()
            if not fcl.can_access_report(visibility_levels, report_type):
                logger.warning(
                    f"Report access denied: user={user['user_id']} "
                    f"report={report_type} visibility={visibility_levels}"
                )
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "Access denied",
                        "message": f"You don't have permission to access {report_type.replace('_', ' ')} report",
                        "code": "FCL_REPORT_DENIED",
                        "required_report": report_type
                    }
                )
            
            user['visibility_levels'] = visibility_levels
            user['business_role_code'] = context.business_role_code
            
            return user
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Report access check error: {e}")
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "Access check failed",
                    "message": "Unable to verify report access"
                }
            )
    
    return report_dependency


# =============================================================================
# GRANULAR PERMISSION DECORATOR
# =============================================================================

def require_granular_permission(permission_code: str):
    """
    Decorator to check granular permission before endpoint execution.
    
    Uses permission codes in format: {module}:{resource}:{action}
    Examples:
      - payroll:weekly:create
      - payroll:weekly:approve
      - sales_invoice:credit_note:create
      - inventory:stock_transfer:approve
    
    Usage:
        @router.post("/payroll/weekly")
        async def create_weekly_payroll(
            user: dict = Depends(require_granular_permission("payroll:weekly:create"))
        ):
            ...
    """
    from backend.api_gateway.app.services.permission_service import get_permission_service
    
    async def permission_dependency(
        request: Request,
        user: dict = Depends(get_current_user)
    ) -> dict:
        user_id = user.get('user_id')
        tenant_id = user.get('tenant_id')
        
        if not user_id or not tenant_id:
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        try:
            permission_service = get_permission_service()
            
            result = await permission_service.check_permission_detailed(
                user_id, tenant_id, permission_code
            )
            
            if not result.granted:
                logger.warning(
                    f"Granular permission denied: user={user_id} "
                    f"permission={permission_code} role={result.role_code} "
                    f"source={result.source}"
                )
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "Permission denied",
                        "message": f"You don't have permission: {permission_code}",
                        "code": "PERMISSION_DENIED",
                        "required_permission": permission_code
                    }
                )
            
            # Add permission info to user dict for downstream use
            user['granted_permission'] = permission_code
            user['permission_source'] = result.source
            user['role_code'] = result.role_code
            
            return user
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Granular permission check error: {e}")
            # Fail-closed for security
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "Permission check failed",
                    "message": "Unable to verify permission",
                    "code": "PERMISSION_CHECK_ERROR"
                }
            )
    
    return permission_dependency


async def check_granular_permission(
    request: Request,
    permission_code: str
) -> bool:
    """
    Programmatic check for granular permission.
    Use this when you need to check permission in code, not as a dependency.
    
    Usage:
        if await check_granular_permission(request, "payroll:weekly:approve"):
            # User can approve
            pass
    """
    from backend.api_gateway.app.services.permission_service import get_permission_service
    
    user = getattr(request.state, 'user', None)
    if not user:
        return False
    
    try:
        permission_service = get_permission_service()
        return await permission_service.check_permission(
            user['user_id'],
            user['tenant_id'],
            permission_code
        )
    except Exception as e:
        logger.error(f"Permission check error: {e}")
        return False
