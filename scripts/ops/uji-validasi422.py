"""BUG-185 uji di kontainer sekali-pakai TANPA DB: penangan 422 bersama (services/galat_validasi.validasi_handler) atas rute berbentuk POST /expenses/{id}/attachments (model AddAttachmentRequest ASLI).
multipart PNG -> 422 VALIDASI_GAGAL (bukan 500), isi biner tak bocor; 4 badan biner x 4 Content-Type -> 400/422; JSON salah biasa -> 422 dgn loc/msg/type."""
import sys
import uuid

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.testclient import TestClient

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers.expenses import AddAttachmentRequest  # noqa: E402
from app.services import galat_validasi as GV  # noqa: E402

app = FastAPI()
app.add_exception_handler(RequestValidationError, GV.validasi_handler)


@app.post("/expenses/{eid}/attachments")
async def lampir(eid: uuid.UUID, body: AddAttachmentRequest):
    return {"ok": True}


cli = TestClient(app, raise_server_exceptions=False)
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(128, 200))
MP = b'--zz\r\nContent-Disposition: form-data; name="file"; filename="n.png"\r\nContent-Type: image/png\r\n\r\n' + PNG + b"\r\n--zz--\r\n"
u = f"/expenses/{uuid.uuid4()}/attachments"
r = cli.post(u, content=MP, headers={"Content-Type": "multipart/form-data; boundary=zz"})
cek("multipart PNG ke rute lampiran: 422 VALIDASI_GAGAL", r.status_code == 422 and r.json().get("code") == GV.KODE, f"{r.status_code} {r.text[:160]}")
cek("isi biner tak bocor; penanda <biner N byte> ada", "PNG" not in r.text and "<biner" in r.text)
for isi in (b"\x89\x00\xff\xfe", b"\xff" * 5000, b"\x80abc", b""):
    for ct in ("application/octet-stream", "multipart/form-data; boundary=zz", "application/json", "text/plain"):
        rr = cli.post(u, content=isi, headers={"Content-Type": ct})
        cek(f"badan biner {len(isi)}B x {ct}: 400/422 (bukan 500)", rr.status_code in (400, 422), f"{rr.status_code}")
rj = cli.post(u, json={"document_id": "bukan-uuid"})
j = rj.json()
cek("JSON salah biasa: 422, detail ber-loc/msg/type, kode VALIDASI_GAGAL", rj.status_code == 422 and j.get("code") == GV.KODE and j["detail"] and all(d.get("loc") and d.get("msg") and d.get("type") for d in j["detail"]), str(j)[:200])
print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
sys.exit(0 if all(HASIL) else 1)
