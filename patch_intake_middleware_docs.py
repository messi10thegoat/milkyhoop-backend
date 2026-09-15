"""[B] middleware WRITE_EXEMPT (intake+documents upload/attach; izin ditegakkan di handler) + baseline
removal + documents.py membership check."""
import io, json, sys

# 1) permission_middleware WRITE_EXEMPT
PM = "/root/mh-law2/backend/api_gateway/app/middleware/permission_middleware.py"
t = io.open(PM, encoding="utf-8").read()
A = '    (r"^/[^/]+/chat$", "chat per-tenant publik (ber-JWT)"),\n]'
B = ('    (r"^/[^/]+/chat$", "chat per-tenant publik (ber-JWT)"),\n'
     '    # [B] intake/dokumen: gate lolos, izin ditegakkan di handler (anggota aktif + modul-tujuan per doc_type tersimpan)\n'
     '    (r"^/api/document-intake/upload$", "intake: anggota aktif dicek di handler"),\n'
     '    (r"^/api/document-intake/execute-batch$", "intake: izin per-item dicek di handler"),\n'
     '    (r"^/api/document-intake/document/[^/]+/(confirm|execute|reject|retry)$", "intake: izin modul-tujuan/anggota dicek di handler"),\n'
     '    (r"^/api/documents/upload$", "unggah dokumen: anggota aktif dicek di handler"),\n'
     '    (r"^/api/documents/[^/]+/attach$", "lampir dokumen: anggota aktif dicek di handler"),\n'
     ']')
if t.count(A) != 1:
    print("GAGAL WRITE_EXEMPT jangkar", t.count(A)); sys.exit(1)
io.open(PM, "w", encoding="utf-8").write(t.replace(A, B))
print("OK WRITE_EXEMPT [B] ditambah")

# 2) baseline removal
BL = "/root/mh-law2/scripts/izin_write_baseline.json"
data = json.load(open(BL))
rm = {
    "POST /api/document-intake/upload", "POST /api/document-intake/execute-batch",
    "POST /api/document-intake/document/{doc_id}/confirm",
    "POST /api/document-intake/document/{doc_id}/execute",
    "POST /api/document-intake/document/{doc_id}/reject",
    "POST /api/document-intake/document/{doc_id}/retry",
    "POST /api/documents/upload", "POST /api/documents/{document_id}/attach",
}
miss = rm - set(data)
if miss:
    print("PERINGATAN baseline tak ada:", miss)
new = [x for x in data if x not in rm]
json.dump(sorted(new), open(BL, "w"), indent=1, ensure_ascii=False)
print(f"OK baseline: {len(data)} -> {len(new)}")

# 3) documents.py membership check (upload + attach)
DP = "/root/mh-law2/backend/api_gateway/app/routers/documents.py"
d = io.open(DP, encoding="utf-8").read()
HELP = '''async def _require_active_member_docs(request):
    """Anggota AKTIF tenant (bukan sekadar token valid)."""
    from ..services.policy_engine_client import get_policy_engine
    u = getattr(request.state, "user", {}) or {}
    eng = get_policy_engine()
    c = await eng.get_user_context(str(u.get("user_id")), u.get("tenant_id"), u.get("role", "USER"))
    if not c.membership_active:
        raise HTTPException(status_code=403, detail="Keanggotaan tenant tidak aktif")


@router.post("/upload", response_model=UploadDocumentResponse)
async def upload_document('''
if d.count('@router.post("/upload", response_model=UploadDocumentResponse)\nasync def upload_document(') != 1:
    print("GAGAL docs helper/upload jangkar"); sys.exit(1)
d = d.replace('@router.post("/upload", response_model=UploadDocumentResponse)\nasync def upload_document(', HELP)

for old, new, lbl in [
    ('    """Upload a new document"""\n    ctx = get_user_context(request)\n    pool = await get_pool()',
     '    """Upload a new document"""\n    ctx = get_user_context(request)\n    await _require_active_member_docs(request)\n    pool = await get_pool()', "docs-upload"),
    ('    """Attach document to an entity"""\n    ctx = get_user_context(request)\n    pool = await get_pool()',
     '    """Attach document to an entity"""\n    ctx = get_user_context(request)\n    await _require_active_member_docs(request)\n    pool = await get_pool()', "docs-attach"),
]:
    if d.count(old) != 1:
        print(f"GAGAL [{lbl}] count={d.count(old)}"); sys.exit(1)
    d = d.replace(old, new)
io.open(DP, "w", encoding="utf-8").write(d)
print("OK documents.py: membership di upload + attach")
