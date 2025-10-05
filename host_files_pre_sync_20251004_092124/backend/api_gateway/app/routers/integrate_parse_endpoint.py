"""
SURGICAL INTEGRATION: Add parse endpoint to customer.py
"""

def integrate_parse_endpoint():
    """Add parse endpoint to customer router"""
    
    # Read current customer.py
    with open('/app/backend/api_gateway/app/routers/customer.py', 'r') as f:
        content = f.read()
    
    # Find insertion point (before last line or after imports)
    if 'from app.services.tenant_client import TenantParserClient' not in content:
        # Add import if missing
        import_section = content.split('\n')
        for i, line in enumerate(import_section):
            if line.startswith('from app.middleware'):
                import_section.insert(i, 'from app.services.tenant_client import TenantParserClient')
                break
        content = '\n'.join(import_section)
    
    # Add parse endpoint before the last line
    lines = content.split('\n')
    
    parse_endpoint = '''
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
        }'''
    
    # Insert before last few lines
    lines.insert(-3, parse_endpoint)
    
    # Write updated content
    with open('/app/backend/api_gateway/app/routers/customer_updated.py', 'w') as f:
        f.write('\n'.join(lines))
    
    print("✅ Parse endpoint integrated")
    return True

if __name__ == "__main__":
    integrate_parse_endpoint()
