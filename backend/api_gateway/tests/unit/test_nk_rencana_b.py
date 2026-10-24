"""NK-RENCANA-B (23 Okt 2026): pembantu ber-kunci mendapat `kunci` (bawaan True -> pemanggil lama tak berubah); kunci=False (pratinjau READ
ONLY) = pemeriksaan SAMA tanpa advisory lock / FOR UPDATE. Kontrol merah: kunci=True TETAP mengunci."""
import asyncio
import uuid
from decimal import Decimal as D

from app.routers import credit_notes as C
from app.services import cn_tertunda as CT


class _KonnKunci:
    def __init__(self, porsi=None):
        self.exec, self.sql, self.porsi = [], [], porsi or []

    async def execute(self, sql, *a):
        self.exec.append(sql)

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        if "credit_note_deferral_lines" in sql:
            return self.porsi
        return []

    async def fetchrow(self, sql, *a):
        return None

    async def fetchval(self, sql, *a):
        return None


CN = {"id": uuid.uuid4(), "original_invoice_id": uuid.uuid4(), "reason": "pricing_error"}


def test_hitung_untuk_nk_tanpa_kunci_vs_dengan_kunci():
    k0 = _KonnKunci()
    asyncio.run(CT.hitung_untuk_nk(k0, "t", CN, D("100"), kunci=False))
    assert k0.exec == [] and not any("FOR UPDATE" in s for s in k0.sql)
    k1 = _KonnKunci()
    asyncio.run(CT.hitung_untuk_nk(k1, "t", CN, D("100")))
    assert any("pg_advisory_xact_lock" in s for s in k1.exec) and any("FOR UPDATE OF sii" in s for s in k1.sql)


def test_pulihkan_saat_void_periksa_tanpa_kunci():
    porsi = [{"id": uuid.uuid4(), "invoice_item_id": uuid.uuid4(), "amount": D("10"), "recognized_at_cn": D("0"), "invoice_id": uuid.uuid4(),
              "recognized_now": D("0"), "terkirim_sesudah": False}]
    k0 = _KonnKunci(porsi)
    asyncio.run(CT.pulihkan_saat_void(k0, "t", CN, periksa_saja=True, kunci=False))
    assert k0.exec == []
    k1 = _KonnKunci(porsi)
    asyncio.run(CT.pulihkan_saat_void(k1, "t", CN, periksa_saja=True))
    assert any("pg_advisory_xact_lock" in s for s in k1.exec)


def test_periksa_bisa_dinotakan_tanpa_kunci():
    items = [{"original_invoice_item_id": uuid.uuid4(), "quantity": 1}]
    k0 = _KonnKunci()
    asyncio.run(C.periksa_bisa_dinotakan(k0, "t", uuid.uuid4(), items, kunci=False))
    assert k0.exec == []
    k1 = _KonnKunci()
    asyncio.run(C.periksa_bisa_dinotakan(k1, "t", uuid.uuid4(), items))
    assert any("CN_FAKTUR:" in s or "pg_advisory_xact_lock" in s for s in k1.exec)


def test_penentu_meneruskan_kunci_hanya_saat_tulis():
    import inspect
    from app.services import nota_kredit_rencana as NKR
    p = inspect.getsource(NKR.rencana_posting_nk)
    b = inspect.getsource(NKR.rencana_batal_nk)
    assert "kunci=not pratinjau" in p and p.count("kunci=not pratinjau") == 2   # periksa_bisa_dinotakan + hitung_untuk_nk
    assert "kunci=not pratinjau" in b and '("" if pratinjau else " FOR UPDATE")' in b
    assert "_rencana_restock_nk(conn, ctx, cn, bl, items=baris_nk, semua=maya is not None) if pratinjau else []" in p  # AS-G7b: + maya   # restock tulis tetap di loop penulis
