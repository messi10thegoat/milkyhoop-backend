from fastapi import APIRouter, HTTPException
import httpx
import os

router = APIRouter()

FLOW_EXECUTOR_HOST = os.getenv("FLOW_EXECUTOR_HOST", "http://flow-executor:8088")

@router.post("/flow/run")
async def run_flow_endpoint(body: dict):
    flow_id = body.get("flow_id")
    input_data = body.get("input")

    if not flow_id or not input_data:
        raise HTTPException(status_code=400, detail="flow_id and input are required")

    url = f"{FLOW_EXECUTOR_HOST}/run-flow/{flow_id}.json"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json={"input": input_data})
            response.raise_for_status()
            return {"status": "success", "result": response.text}
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Request failed: {e}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Flow executor error: {e.response.text}")
