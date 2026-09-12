import sys, json
sys.path.insert(0,'/app/backend/api_gateway')
from app.services.llm.gemini_client import GeminiClient
from app.services.unified_agent.entity_extractor import build_intent_schema
sc = build_intent_schema('create_stock_adjustment')['json_schema']['schema']
cleaned = GeminiClient._clean_schema(json.loads(json.dumps(sc)))
it = cleaned['properties']['items']
print('AFTER _clean_schema type:', it.get('type'))
print('items.type:', it.get('items',{}).get('type'))
print('items.properties:', sorted(it.get('items',{}).get('properties',{})))
assert it.get('type')=='array', 'DIRUNTUHKAN!'
assert it['items']['type']=='object'
print('LOLOS _clean_schema TANPA DIRUNTUHKAN')
