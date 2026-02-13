"""
FCL (Financial Confidentiality Layer) Middleware
Automatically filters sensitive data in responses based on user visibility levels.

IRON LAW COMPLIANCE:
- Law 0: Separation of Concerns - Middleware layer for presentation filtering
- Law 9: Deterministic Reporting - Consistent filtering rules
- Law 10: AI Safety Boundary - FCL decisions logged
"""
import json
import logging
from typing import Callable, List, Dict, Any
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.types import ASGIApp

from backend.api_gateway.app.services.fcl_service import get_fcl_service

logger = logging.getLogger(__name__)


# Routes that should have FCL filtering applied (entity_type mapping)
FCL_ROUTE_MAPPING: Dict[str, str] = {
    "/api/sales-invoices": "invoice",
    "/api/invoices": "invoice",
    "/api/bills": "bill",
    "/api/products": "product",
    "/api/items": "product",
    "/api/customers": "customer",
    "/api/vendors": "vendor",
    "/api/suppliers": "vendor",
    "/api/bank-accounts": "bank_account",
    "/api/payroll": "payroll",
    "/api/employees": "payroll",
    # Reports handled separately via require_visibility
}

# Routes to skip FCL (public, auth, etc.)
FCL_SKIP_ROUTES = {
    "/api/auth",
    "/api/health",
    "/api/tenants",
    "/docs",
    "/openapi.json",
}


class FCLMiddleware(BaseHTTPMiddleware):
    """
    Middleware that filters sensitive financial data in responses.
    Only applies to GET requests returning JSON data.
    """
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip non-GET requests (mutations don't need response filtering)
        if request.method != "GET":
            return await call_next(request)
        
        # Skip certain routes
        path = request.url.path
        if any(path.startswith(skip) for skip in FCL_SKIP_ROUTES):
            return await call_next(request)
        
        # Get entity type for this route
        entity_type = self._get_entity_type(path)
        if not entity_type:
            return await call_next(request)
        
        # Get user's visibility levels from request state
        visibility_levels = self._get_visibility_levels(request)
        
        # Call the actual endpoint
        response = await call_next(request)
        
        # Only filter JSON responses
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response
        
        # Handle streaming responses
        if isinstance(response, StreamingResponse):
            return await self._filter_streaming_response(
                response, entity_type, visibility_levels
            )
        
        return response
    
    def _get_entity_type(self, path: str) -> str | None:
        """Determine entity type from request path."""
        # Remove trailing slash and query params
        clean_path = path.rstrip("/").split("?")[0]
        
        # Check exact match first
        for route, entity in FCL_ROUTE_MAPPING.items():
            if clean_path == route or clean_path.startswith(f"{route}/"):
                return entity
        
        return None
    
    def _get_visibility_levels(self, request: Request) -> List[str]:
        """Get user's visibility levels from request state."""
        # Try user dict first
        user = getattr(request.state, "user", {})
        if isinstance(user, dict):
            levels = user.get("visibility_levels")
            if levels:
                return levels
        
        # Try FCL context
        fcl_context = getattr(request.state, "fcl_context", {})
        if fcl_context:
            visibility_str = fcl_context.get("user_visibility", "")
            if visibility_str:
                return visibility_str.split(",")
        
        # Default to L1 only
        return ["L1"]
    
    async def _filter_streaming_response(
        self,
        response: StreamingResponse,
        entity_type: str,
        visibility_levels: List[str]
    ) -> Response:
        """Filter a streaming JSON response."""
        try:
            # Collect response body
            body = b""
            async for chunk in response.body_iterator:
                body += chunk
            
            # Parse and filter
            data = json.loads(body)
            fcl = get_fcl_service()
            
            # Filter based on response structure
            if "data" in data:
                if isinstance(data["data"], list):
                    data["data"] = fcl.filter_entity_list(
                        data["data"], entity_type, visibility_levels
                    )
                elif isinstance(data["data"], dict):
                    data["data"] = fcl.filter_entity(
                        data["data"], entity_type, visibility_levels
                    )
            else:
                # Direct entity response
                data = fcl.filter_entity(data, entity_type, visibility_levels)
            
            # Return filtered response
            filtered_body = json.dumps(data).encode()
            
            return Response(
                content=filtered_body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type="application/json"
            )
            
        except Exception as e:
            logger.error(f"FCL filtering error: {e}")
            # Return original response on error
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type="application/json"
            )
