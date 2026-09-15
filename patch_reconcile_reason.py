"""production.py void month-end reconcile: terima alasan DARI PENGGUNA.

Body OPSIONAL, bukan wajib: FE hari ini (useMonthEndReconcile.ts:244) memanggil
tanpa body. Body wajib = tombol FE langsung 422. Kalau body dikirim, reason
wajib 1..500 karakter (sama dengan ReverseJournalRequest). Tanpa body = kalimat
lama, persis seperti sekarang.
"""
import io
import sys

PATH = "backend/api_gateway/app/routers/production.py"

LAMA_SIG = "async def void_month_end_reconcile(request: Request, journal_id: UUID):\n"
BARU_SIG = (
    "async def void_month_end_reconcile(\n"
    "    request: Request,\n"
    "    journal_id: UUID,\n"
    "    body: Optional[VoidReconcileRequest] = None,\n"
    "):\n"
)

LAMA_REASON = (
    "                    journal_id,\n"
    "                    \"Void month-end manufacturing reconcile\",\n"
    "                )\n"
)
BARU_REASON = (
    "                    journal_id,\n"
    "                    body.reason if body else \"Void month-end manufacturing reconcile\",\n"
    "                )\n"
)

LAMA_DEKOR = (
    "@router.post(\n"
    "    \"/month-end-reconcile/{journal_id}/void\", response_model=ProductionResponse\n"
    ")\n"
)
BARU_DEKOR = (
    "class VoidReconcileRequest(BaseModel):\n"
    "    # Alasan dari pengguna -> reversal_reason + description jurnal pembalik.\n"
    "    # Body opsional: tanpa body tetap memakai kalimat lama (FE lama tak mengirim).\n"
    "    reason: str = Field(..., min_length=1, max_length=500)\n"
    "\n"
    "\n" + LAMA_DEKOR
)

teks = io.open(PATH, encoding="utf-8").read()
for nama, lama in (("sig", LAMA_SIG), ("reason", LAMA_REASON), ("dekor", LAMA_DEKOR)):
    if teks.count(lama) != 1:
        print("GAGAL: jangkar %s cocok %dx, harus 1x — NOL ditulis" % (nama, teks.count(lama)))
        sys.exit(1)

IMPOR_LAMA = "from uuid import UUID\n"
if teks.count(IMPOR_LAMA) != 1:
    print("GAGAL: jangkar impor cocok %dx — NOL ditulis" % teks.count(IMPOR_LAMA))
    sys.exit(1)
teks = teks.replace(IMPOR_LAMA, IMPOR_LAMA + "from pydantic import BaseModel, Field\n", 1)

teks = teks.replace(LAMA_DEKOR, BARU_DEKOR).replace(LAMA_SIG, BARU_SIG).replace(LAMA_REASON, BARU_REASON)

# impor yang dibutuhkan
kepala = teks[:4000]
tambah = []
if "BaseModel" not in kepala:
    tambah.append("from pydantic import BaseModel, Field")
elif "Field" not in kepala:
    tambah.append("from pydantic import Field")
if "Optional" not in kepala:
    tambah.append("from typing import Optional")
if tambah:
    print("PERLU IMPOR, tidak ditambah otomatis:", tambah)
    sys.exit(2)

io.open(PATH, "w", encoding="utf-8").write(teks)
print("terpasang")
