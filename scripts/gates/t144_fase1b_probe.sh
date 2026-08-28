#!/bin/bash
L=$1; PORT=$2; TXT=$3
TOK=$(cat /tmp/tok.txt)
SID=$(python3 -c 'import uuid;print(uuid.uuid4())')
python3 -c 'import json,sys;print(json.dumps({"conversation_id":sys.argv[1],"session_id":sys.argv[1],"text":sys.argv[2]}))' "$SID" "$TXT" > /tmp/body_$L.json
echo "### $L port=$PORT session=$SID"
S=$(date +%s.%N)
curl -s -X POST http://127.0.0.1:$PORT/api/v3/chat/message/stream \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $TOK" \
  --data-binary @/tmp/body_$L.json -o /tmp/out_$L.txt -w 'HTTP=%{http_code}\n'
E=$(date +%s.%N)
echo "LATENCY_S=$(echo "$E-$S"|bc)"
python3 -c "
import json,sys
s=open('/tmp/out_$L.txt').read()
d=[json.loads(l[6:]) for l in s.splitlines() if l.startswith('data: ')]
f=[x for x in d if x['event']=='DONE']
if not f:
    print('  NO DONE:',s[-500:]); sys.exit()
x=f[0]['data']
print('  message_type=',x['message_type'],' pending_action_id=',x['pending_action_id'])
print('  TEXT=',repr(x['text'])[:900])
dd=x.get('data') or {}
if dd.get('payload') is not None: print('  PAYLOAD=',json.dumps(dd['payload'],ensure_ascii=False)[:1300])
rc=dd.get('review_card') or {}
if rc:
    print('  CARD_items=',json.dumps(rc.get('items'),ensure_ascii=False)[:900])
    print('  CARD_totals=',json.dumps(rc.get('totals'),ensure_ascii=False)[:300])
    print('  CARD_journal=',json.dumps(rc.get('journal_lines'),ensure_ascii=False)[:600])
    print('  CARD_render=',rc.get('render_target'))
"
