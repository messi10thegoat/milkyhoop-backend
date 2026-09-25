"""NK retur -- HPP + gudang ASLI dari keluarnya barang lewat faktur asal (25 Sep 2026).

Lanjutan #37 (C: biaya = WAC saat NK diposting, bukan HPP yang dibebankan saat
faktur dikirim; D: gudang = gudang pertama tenant). Diukur: semua tenant 1
gudang (D dampak nyata 0), keluar faktur = INVOICE_FULFILLMENT/SALE (kaos 5
baris). Temuan saat mengukur: retur atas faktur yang barangnya belum pernah
keluar tetap menambah stok + mengkredit HPP yang tak pernah didebit (stok
hantu); retur melebihi yang terkirim juga lolos.

post_credit_note dipanggil UTUH (harness #37) dengan faktur asal.
"""
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import credit_notes as CN
from app.services import inventory_helpers as IH

from .test_t37_cn_restock_retur import DB, Pool, TENANT, USER, CN_ID, PRODUK, GUDANG

FAKTUR = uuid.UUID("88888888-8888-8888-8888-888888888888")
GUDANG_KIRIM = uuid.UUID("99999999-9999-9999-9999-999999999999")


class DBFaktur(DB):
    def __init__(self, keluar, sudah_retur=Decimal("0"), qty=Decimal("2")):
        super().__init__("return")
        self.cn["original_invoice_id"] = FAKTUR
        self.cn["original_invoice_number"] = "INV-UJI-0001"
        self.items[0]["quantity"] = qty
        self.keluar = keluar
        self.sudah_retur = sudah_retur
        self.kunci = []
        self.kueri_keluar = []

    async def fetch(self, sql, *a):
        s = " ".join(sql.split())
        if "FROM inventory_ledger" in s and "source_type = ANY" in s:
            self.kueri_keluar.append(a)
            return self.keluar
        return await super().fetch(sql, *a)

    async def fetchval(self, sql, *a):
        s = " ".join(sql.split())
        if "get_weighted_average_cost" in s or s.startswith("SELECT id FROM warehouses"):
            raise AssertionError("dengan faktur asal, WAC/gudang pertama TIDAK boleh dipakai")
        if "source_type = 'CREDIT_NOTE'" in s:
            assert a == (TENANT, PRODUK, FAKTUR, CN_ID)
            assert "c.status <> 'void'" in s and "c.id <> $4" in s
            return self.sudah_retur
        return await super().fetchval(sql, *a)

    async def execute(self, sql, *a):
        if "pg_advisory_xact_lock" in sql:
            self.kunci.append(a[0])
        return await super().execute(sql, *a)


def keluar(q_out, biaya, gudang=GUDANG_KIRIM, q_in=0):
    return {"warehouse_id": gudang, "quantity_in": Decimal(str(q_in)),
            "quantity_out": Decimal(str(q_out)), "unit_cost": Decimal(str(biaya))}


@pytest.fixture
def jalankan(monkeypatch):
    async def _j(db):
        masuk = []

        async def _pool():
            return Pool(db)

        async def _nop(*a, **k):
            return None

        async def _akun(*a, **k):
            return uuid.uuid4()

        async def _faktur(*a, **k):
            return {"id": FAKTUR}

        async def _inbound(**kw):
            masuk.append(kw)
            return {"ledger_id": uuid.uuid4(), "new_average_cost": Decimal("30000")}

        monkeypatch.setattr(CN, "get_pool", _pool)
        monkeypatch.setattr(CN, "_ensure_role_preconditions", _nop)
        monkeypatch.setattr(CN, "resolve_account_id", _akun)
        monkeypatch.setattr(CN, "resolve_account_id_by_role", _akun)
        monkeypatch.setattr(CN, "resolve_account_id_by_role_if_pkp", _akun)
        monkeypatch.setattr(CN, "faktur_tenant_untuk_pelanggan", _faktur)
        monkeypatch.setattr(CN, "pastikan_cn_muat_faktur", _nop)
        monkeypatch.setattr(CN, "segarkan_cache_piutang_faktur", _nop)
        monkeypatch.setattr(IH, "record_inventory_inbound", _inbound)
        req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": USER}))
        r = await CN.post_credit_note(req, CN_ID)
        return r, masuk
    return _j


@pytest.mark.asyncio
async def test_biaya_dan_gudang_dari_keluar_faktur(jalankan):
    # 3 @ 35.000 + 1 @ 39.000 = 144.000 / 4 = 36.000 (bukan WAC)
    db = DBFaktur([keluar(3, 35000), keluar(1, 39000)])
    r, masuk = await jalankan(db)
    assert r["success"]
    assert len(masuk) == 1
    assert Decimal(str(masuk[0]["unit_cost"])) == Decimal("36000")
    assert masuk[0]["warehouse_id"] == GUDANG_KIRIM
    assert masuk[0]["quantity"] == 2.0
    assert db.kueri_keluar == [(TENANT, FAKTUR, PRODUK, ["SALES_INVOICE", "INVOICE_FULFILLMENT"])]
    assert db.jurnal == ["CREDIT_NOTE", "CREDIT_NOTE_COGS"]
    assert any(k.startswith("CN_RETUR_FAKTUR:") and str(FAKTUR) in k for k in db.kunci)


@pytest.mark.asyncio
async def test_gudang_terbesar_bila_keluar_dari_dua_gudang(jalankan):
    db = DBFaktur([keluar(1, 30000, GUDANG), keluar(5, 30000, GUDANG_KIRIM)])
    _, masuk = await jalankan(db)
    assert masuk[0]["warehouse_id"] == GUDANG_KIRIM


@pytest.mark.asyncio
async def test_faktur_tanpa_pengiriman_barang_400_tanpa_restock(jalankan):
    db = DBFaktur([])
    with pytest.raises(HTTPException) as e:
        await jalankan(db)
    assert e.value.status_code == 400 and "belum pernah dikirim" in e.value.detail
    assert db.jurnal == [] or "CREDIT_NOTE_COGS" not in db.jurnal


@pytest.mark.asyncio
async def test_retur_melebihi_sisa_terkirim_400(jalankan):
    # keluar 3, pengiriman dibatalkan 1, NK lain sudah meretur 1 -> sisa 1; retur 2 ditolak
    db = DBFaktur([keluar(3, 35000), keluar(0, 35000, q_in=1)], sudah_retur=Decimal("1"))
    with pytest.raises(HTTPException) as e:
        await jalankan(db)
    assert e.value.status_code == 400 and "melebihi" in e.value.detail


@pytest.mark.asyncio
async def test_retur_tepat_sisa_lolos(jalankan):
    db = DBFaktur([keluar(3, 35000)], sudah_retur=Decimal("1"), qty=Decimal("2"))
    r, masuk = await jalankan(db)
    assert r["success"] and masuk[0]["quantity"] == 2.0


@pytest.mark.asyncio
async def test_tanpa_faktur_asal_tetap_wac_dan_gudang_pertama(jalankan):
    db = DB("return")  # original_invoice_id None
    r, masuk = await jalankan(db)
    assert Decimal(str(masuk[0]["unit_cost"])) == Decimal("30000")
    assert masuk[0]["warehouse_id"] == GUDANG
