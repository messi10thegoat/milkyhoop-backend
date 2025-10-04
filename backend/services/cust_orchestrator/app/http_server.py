"""
Simple HTTP wrapper for cust_reference gRPC service
For easier testing via curl
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import asyncio
import grpc
import sys

# Add path for imports
sys.path.append('/app/backend/services/cust_reference/app')

# Import gRPC components
import cust_reference_pb2
import cust_reference_pb2_grpc

app = FastAPI(title="Customer Reference Resolution HTTP API")

class ReferenceRequest(BaseModel):
    session_id: str
    tenant_id: str
    reference_text: str
    context_query: str = ""

class ReferenceResponse(BaseModel):
    success: bool
    resolved_entity: str = ""
    entity_type: str = ""
    resolution_method: str = ""
    candidates: list = []
    error_message: str = ""

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        channel = grpc.aio.insecure_channel('localhost:5013')
        stub = cust_reference_pb2_grpc.Cust_referenceStub(channel)
        
        from google.protobuf.empty_pb2 import Empty
        response = await stub.Health(Empty())
        
        await channel.close()
        
        return {
            "status": response.status,
            "service": response.service,
            "timestamp": response.timestamp
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/resolve", response_model=ReferenceResponse)
async def resolve_reference(request: ReferenceRequest):
    """Resolve Indonesian pronoun reference"""
    try:
        channel = grpc.aio.insecure_channel('localhost:5013')
        stub = cust_reference_pb2_grpc.Cust_referenceStub(channel)
        
        # Create gRPC request
        grpc_request = cust_reference_pb2.ReferenceRequest(
            session_id=request.session_id,
            tenant_id=request.tenant_id,
            reference_text=request.reference_text,
            context_query=request.context_query
        )
        
        # Call gRPC service
        response = await stub.ResolveReference(grpc_request)
        
        await channel.close()
        
        # Convert to HTTP response
        return ReferenceResponse(
            success=response.success,
            resolved_entity=response.resolved_entity,
            entity_type=response.entity_type,
            resolution_method=response.resolution_method,
            candidates=list(response.candidates),
            error_message=response.error_message
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8013)
