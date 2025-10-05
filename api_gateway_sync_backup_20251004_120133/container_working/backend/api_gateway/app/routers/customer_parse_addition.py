# SURGICAL ADDITION: Direct parse endpoint for tenant_parser testing
# Add this to customer.py after existing endpoints

@router.post("/parse")
async def parse_customer_query_direct(
    request: dict,
    current_user: dict = Depends(get_current_user)
):
    """
    Direct parse endpoint for tenant_parser testing
    Maps to existing tenant_parser infrastructure
    """
    try:
        session_id = request.get("session_id", "direct_parse")
        message = request.get("message", "")
        tenant_id = current_user.get("tenant_id", "default")
        
        # Use existing tenant_client infrastructure
        tenant_client = TenantParserClient()
        
        parsed = await tenant_client.parse_customer_query(
            session_id=session_id,
            message=message,
            tenant_id=tenant_id
        )
        
        return {
            "status": "success",
            "response": parsed.get("response", ""),
            "confidence": parsed.get("confidence", 0.0),
            "route_taken": parsed.get("route_taken", "unknown"),
            "metadata": parsed.get("confidence_metadata", {})
        }
        
    except Exception as e:
        logger.error(f"Direct parse error: {e}")
        return {
            "status": "error", 
            "message": str(e),
            "response": "Parse request failed"
        }
