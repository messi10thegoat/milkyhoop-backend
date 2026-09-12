import json, sys
sys.path.insert(0, '/app/backend/api_gateway')
from app.services.unified_agent.direct_action_registry import DIRECT_ACTIONS
from app.services.unified_agent.entity_extractor import build_intent_schema
out = {}
for k in sorted(DIRECT_ACTIONS):
    try:
        out[k] = build_intent_schema(k)
    except Exception as e:
        out[k] = {'__error__': repr(e)}
print(json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False))
