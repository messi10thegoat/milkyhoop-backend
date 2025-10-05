import json
import os
from typing import Dict, List

def generate_basic_chatbot_flow(tenant_profile: Dict) -> Dict:
    """Generate basic chatbot flow based on tenant profile"""
    
    tenant_id = tenant_profile.get("session_id", "unknown")
    business_name = tenant_profile.get("business_name", "Business")
    
    flow = {
        "flow_id": f"chatbot-{tenant_id}",
        "name": f"{business_name} Chatbot",
        "trigger_id": "user-message",
        "context": {
            "tenant_id": tenant_id,
            "input": {
                "user_id": "{{user_id}}",
                "message": "{{message}}"
            }
        },
        "nodes": [
            {
                "id": "start",
                "hoop": "",
                "parameters": {},
                "next": "parse_intent"
            },
            {
                "id": "parse_intent", 
                "hoop": "IntentParser",
                "parameters": {
                    "message": "{{input.message}}",
                    "user_id": "{{input.user_id}}"
                },
                "next": "handle_response"
            },
            {
                "id": "handle_response",
                "hoop": "rag_llm", 
                "parameters": {
                    "query": "{{input.message}}",
                    "tenant_id": "{{tenant_id}}"
                },
                "next": "send_reply"
            },
            {
                "id": "send_reply",
                "hoop": "SendBotReply",
                "parameters": {
                    "message": "{{handle_response.answer}}"
                },
                "next": null
            }
        ]
    }
    
    return flow

def save_flow_to_executor(flow_data: Dict, tenant_id: str) -> str:
    """Save generated flow to flow-executor directory"""
    
    flow_path = f"backend/services/flow-executor/flows/business/{tenant_id}.json"
    os.makedirs(os.path.dirname(flow_path), exist_ok=True)
    
    with open(flow_path, "w") as f:
        json.dump(flow_data, f, indent=2, ensure_ascii=False)
    
    return flow_path
