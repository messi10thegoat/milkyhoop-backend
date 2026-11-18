"""19 Nov 2026 (putusan pemilik 14 Nov: dua langkah K8 = satu dokumen, tuntas saat Diterima): KPI /summary 'selesai_bulan_ini' +
'nilai_selesai_bulan_ini' menghitung status completed DAN received (kunci tanggal received_date tetap); in_transit/draft/voided tak ikut.
Stub membaca himpunan status dari SQL handler ASLI lalu menyaring baris -> mengubah filter = tes merah."""
import asyncio
import re
import uuid
from datetime import date
from decimal import Decimal

from app.routers import stock_transfers as R

T = "t-uji"
AWAL, AKHIR = date(2026, 11, 1), date(2026, 12, 1)


def _baris():  # (status, received_date, total_value)
    return [("completed", date(2026, 11, 3), Decimal("100")), ("received", date(2026, 11, 9), Decimal("11.10")),
            ("in_transit", date(2026, 11, 7), Decimal("500")), ("draft", None, Decimal("700")), ("voided", date(2026, 11, 5), Decimal("900")),
            ("received", date(2026, 10, 31), Decimal("1000")), ("completed", date(2026, 12, 1), Decimal("2000"))]


def _statuses(sql, alias):
    kepala = sql[:sql.index("AS " + alias)]
    klausa = kepala[kepala.rindex("FILTER (WHERE"):]
    assert "received_date" in klausa
    return set(re.findall(r"'(\w+)'", klausa.split("received_date")[0]))


def _ringkasan(baris):
    sql = {}

    class Konek:
        async def execute(self, *a):
            return None

        async def fetchrow(self, q, *a):
            sql["q"] = q
            s_n, s_v = _statuses(q, "selesai_bulan_ini"), _statuses(q, "nilai_selesai_bulan_ini")
            ok = lambda st, tg, S: st in S and tg is not None and AWAL <= tg < AKHIR
            return {"draft": sum(1 for b in baris if b[0] == "draft"),
                    "selesai_bulan_ini": sum(1 for b in baris if ok(b[0], b[1], s_n)),
                    "void_bulan_ini": sum(1 for b in baris if b[0] == "voided"),
                    "nilai_selesai_bulan_ini": sum((b[2] for b in baris if ok(b[0], b[1], s_v)), Decimal(0))}

    class Cm:
        async def __aenter__(s):
            return Konek()

        async def __aexit__(s, *x):
            return False

    class Pool:
        def acquire(self):
            return Cm()

    async def pool():
        return Pool()

    async def hari(c, t):
        return date(2026, 11, 19)

    class Req:
        class state:
            user = {"tenant_id": T, "user_id": str(uuid.uuid4())}
    R.get_pool, R.tanggal_dokumen = pool, hari
    return asyncio.new_event_loop().run_until_complete(R.summary_stock_transfers(Req()))["data"]


def test_completed_dan_received_ikut_in_transit_draft_void_luar_bulan_tidak():
    d = _ringkasan(_baris())
    assert d["selesai_bulan_ini"] == 2 and d["nilai_selesai_bulan_ini"] == 111.1


def test_completed_saja():
    d = _ringkasan([b for b in _baris() if b[0] != "received"])
    assert d["selesai_bulan_ini"] == 1 and d["nilai_selesai_bulan_ini"] == 100


def test_received_saja():
    d = _ringkasan([b for b in _baris() if b[0] != "completed"])
    assert d["selesai_bulan_ini"] == 1 and d["nilai_selesai_bulan_ini"] == 11.1


def test_in_transit_saja_nol():
    d = _ringkasan([b for b in _baris() if b[0] == "in_transit"])
    assert d["selesai_bulan_ini"] == 0 and d["nilai_selesai_bulan_ini"] == 0
