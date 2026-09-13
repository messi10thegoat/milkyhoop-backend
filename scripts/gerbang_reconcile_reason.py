"""Gerbang dua sisi: parsing body void reconcile, di versi FastAPI kontainer.

LAMA: signature tanpa body -> alasan pengguna DIBUANG (sisi merah).
BARU: body opsional -> tanpa body = kalimat lama, dengan body = alasan pengguna,
      reason kosong = 422.
Tanpa DB: handler tiruan hanya mengembalikan string alasan yang AKAN diteruskan
ke _reverse_journal, ekspresi persis seperti kode.
"""
import sys
from typing import Optional
from uuid import UUID, uuid4
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

KONST = "Void month-end manufacturing reconcile"


class VoidReconcileRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


app = FastAPI()


@app.post("/lama/{journal_id}/void")
async def lama(request: Request, journal_id: UUID):
    return {"r": KONST}


@app.post("/baru/{journal_id}/void")
async def baru(request: Request, journal_id: UUID, body: Optional[VoidReconcileRequest] = None):
    return {"r": body.reason if body else KONST}


c = TestClient(app)
jid = uuid4()
H = {"Content-Type": "application/json"}
gagal = 0


def cek(label, ok):
    global gagal
    print(("[H] " if ok else "[X] ") + label)
    gagal += 0 if ok else 1


# sisi merah: kode lama membuang alasan
r = c.post(f"/lama/{jid}/void", json={"reason": "salah periode"})
cek("MERAH lama: alasan pengguna dibuang (dapat konstanta)", r.status_code == 200 and r.json()["r"] == KONST)

# sisi hijau
r = c.post(f"/baru/{jid}/void", headers=H)  # persis FE hari ini: tanpa body
cek("baru tanpa body (FE sekarang) -> 200 + konstanta  [%s]" % r.status_code, r.status_code == 200 and r.json()["r"] == KONST)
r = c.post(f"/baru/{jid}/void", json={"reason": "salah periode"})
cek("baru dengan alasan -> alasan pengguna", r.status_code == 200 and r.json()["r"] == "salah periode")
r = c.post(f"/baru/{jid}/void", json={"reason": ""})
cek("baru alasan kosong -> 422  [%s]" % r.status_code, r.status_code == 422)
r = c.post(f"/baru/{jid}/void", json={"reason": "x" * 501})
cek("baru alasan 501 karakter -> 422  [%s]" % r.status_code, r.status_code == 422)

sys.exit(1 if gagal else 0)
