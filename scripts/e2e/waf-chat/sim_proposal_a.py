import re, sys, json
sys.path.insert(0, "/root/mh-waf-chat/backend/api_gateway")
from app.middleware.waf_middleware import WAFMiddleware as W

COMMENT_DASH = None
for p in W.SQL_INJECTION_PATTERNS:
    if p.pattern == r"(--\s*$|--\s+)":
        COMMENT_DASH = p
assert COMMENT_DASH is not None, "pola -- tidak ditemukan"

def detect(content, skip=()):
    for p in W.SQL_INJECTION_PATTERNS:
        if p in skip: continue
        if p.search(content): return "SQL Injection:" + p.pattern
    for p in W.XSS_PATTERNS:
        if p.search(content): return "XSS:" + p.pattern
    for p in W.PATH_TRAVERSAL_PATTERNS:
        if p.search(content): return "Path Traversal:" + p.pattern
    return None

cases = {
 "R1": "halo, berapa saldo kas -- sekarang",
 "R2": "Daftarkan item baru: 1. Kaos A -- harga jual 120000 -- harga beli 75000",
 "C1": "halo, berapa saldo kas xx sekarang",
 "G2": "1' OR '1'='1' UNION ALL SELECT NULL--",
 "G3a": "coba ../ ini",
 "G3b": "coba /* ini */ ya",
}
print(f"{'id':5} {'SEKARANG':45} {'USULAN-A (skip pola -- pd body chat)':45}")
for k, v in cases.items():
    body = json.dumps({"conversation_id": "x", "text": v})
    now = detect(body)
    after = detect(body, skip=(COMMENT_DASH,))
    print(f"{k:5} {str(now)[:44]:45} {str(after)[:44]:45}")
