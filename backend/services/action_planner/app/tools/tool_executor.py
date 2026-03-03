"""
Tool executor for enrichment tools.
Calls API Gateway endpoints to fetch master data.
"""
import httpx
from ..config import settings
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


class ToolExecutor:
    """Execute enrichment tools by calling API Gateway."""
    
    def __init__(self, api_gateway_url: str):
        self.api_url = api_gateway_url
        self.timeout = httpx.Timeout(10.0, connect=5.0)
    
    async def execute(
        self, 
        tool_name: str, 
        args: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Route tool call to appropriate handler.
        
        Args:
            tool_name: Name of tool to execute
            args: Tool arguments from LLM
            context: Request context (tenant_id, user_id, etc.)
        
        Returns:
            Tool execution result
        """
        handlers = {
            "search_customers": self._search_customers,
            "search_items": self._search_items,
            "get_item_details": self._get_item_details,
            "search_accounts": self._search_accounts,
        }
        
        handler = handlers.get(tool_name)
        if not handler:
            logger.error(f"Unknown tool: {tool_name}")
            return {"error": f"Unknown tool: {tool_name}"}
        
        try:
            result = await handler(args, context)
            logger.info(f"Tool {tool_name} executed successfully")
            return result
        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}", exc_info=True)
            return {"error": str(e)}
    
    async def _search_customers(self, args: Dict, context: Dict) -> Dict:
        """Call /api/customers/search."""
        async with httpx.AsyncClient(
                timeout=self.timeout,
                headers={"X-Internal-API-Key": settings.INTERNAL_API_KEY}
            ) as client:
            resp = await client.get(
                f"{self.api_url}/api/customers/search",
                params={
                    "q": args["query"],
                    "tenant_id": context["tenant_id"],
                    "limit": args.get("limit", 10)
                }
            )
            
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "success": True,
                    "customers": data.get("customers", []),
                    "count": data.get("total", 0)
                }
            
            return {
                "success": False,
                "error": f"API returned {resp.status_code}",
                "customers": [],
                "count": 0
            }
    
    async def _search_items(self, args: Dict, context: Dict) -> Dict:
        """Call /api/items?search= (list endpoint with search param)."""
        async with httpx.AsyncClient(
                timeout=self.timeout,
                headers={"X-Internal-API-Key": settings.INTERNAL_API_KEY}
            ) as client:
            resp = await client.get(
                f"{self.api_url}/api/items",
                params={
                    "search": args["query"],
                    "tenant_id": context["tenant_id"],
                    "limit": args.get("limit", 10)
                }
            )
            
            if resp.status_code == 200:
                data = resp.json()
                items_list = data.get("items", [])
                simplified = []
                for item in items_list:
                    simplified.append({
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "sku": item.get("sku"),
                        "rate": item.get("rate", 0),
                        "selling_price": item.get("sales_price", item.get("selling_price", item.get("rate", 0))),
                        "purchase_price": item.get("purchase_price", 0),
                        "unit": item.get("unit", "pcs"),
                        "item_type": item.get("item_type", "goods"),
                    })
                return {
                    "success": True,
                    "items": simplified,
                    "count": len(simplified)
                }
            
            return {
                "success": False,
                "error": f"API returned {resp.status_code}",
                "items": [],
                "count": 0
            }
    
    async def _get_item_details(self, args: Dict, context: Dict) -> Dict:
        """Call /api/items/{id}."""
        async with httpx.AsyncClient(
                timeout=self.timeout,
                headers={"X-Internal-API-Key": settings.INTERNAL_API_KEY}
            ) as client:
            resp = await client.get(
                f"{self.api_url}/api/items/{args['item_id']}",
                params={"tenant_id": context["tenant_id"]}
            )
            
            if resp.status_code == 200:
                return {
                    "success": True,
                    "item": resp.json()
                }
            
            return {
                "success": False,
                "error": "Item not found"
            }
    
    async def _search_accounts(self, args: Dict, context: Dict) -> Dict:
        """Call /api/accounts/search."""
        params = {
            "q": args["query"],
            "tenant_id": context["tenant_id"],
            "limit": args.get("limit", 10)
        }
        
        if args.get("account_type"):
            params["account_type"] = args["account_type"]
        
        async with httpx.AsyncClient(
                timeout=self.timeout,
                headers={"X-Internal-API-Key": settings.INTERNAL_API_KEY}
            ) as client:
            resp = await client.get(
                f"{self.api_url}/api/accounts/search",
                params=params
            )
            
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "success": True,
                    "accounts": data.get("accounts", []),
                    "count": data.get("total", 0)
                }
            
            return {
                "success": False,
                "error": f"API returned {resp.status_code}",
                "accounts": [],
                "count": 0
            }
