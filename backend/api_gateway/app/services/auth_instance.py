"""
Auth Client Singleton Instance
Separate module to avoid circular import between main.py and routers
"""
from backend.api_gateway.app.services.auth_client import AuthClient

# Global singleton instance
auth_client = AuthClient(
    host="milkyhoop-dev-auth_service-1",
    port=8013,
    timeout=60.0
)
