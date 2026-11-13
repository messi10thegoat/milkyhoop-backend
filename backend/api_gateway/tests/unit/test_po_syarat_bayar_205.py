"""BUG-205 (putusan pemilik 13 Nov 2026): syarat bayar DISIMPAN di PO (snapshot purchase_orders.payment_terms_days, V496 aditif nullable). Bawaan = termin vendor saat PO dibuat;
tagihan dari PO mewarisi (urutan: isian > termin PO > termin vendor > tanggal faktur); PDF mencetak snapshot. Paritas nyata (klon + V496) = uji salinan."""
import asyncio
import inspect
import re
import uuid
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest
from pydantic import ValidationError

import app.routers.purchase_orders as PO
from app.schemas import purchase_orders as S
from app.services import po_cetak as PC
from app.services import termin_bayar as TB
from app.services.pdf_service import PDFService

APP = Path(PO.__file__).resolve().parents[1]
MIG = APP.parents[1] / "migrations"
TID = "tenant-uji"
HARI = date(2026, 11, 13)


def jalan(aw):
    return asyncio.run(aw)


ITEM = {"description": "Kain", "quantity": 1, "unit_price": 1000}


def test_skema_buat_menerima_0_sampai_365_dan_menolak_di_luar():
    for n in (0, 30, 365):
        assert S.CreatePurchaseOrderRequest(vendor_name="V", po_date=HARI, items=[ITEM], payment_terms_days=n).payment_terms_days == n
    for n in (-1, 366):
        with pytest.raises(ValidationError):
            S.CreatePurchaseOrderRequest(vendor_name="V", po_date=HARI, items=[ITEM], payment_terms_days=n)
    assert S.CreatePurchaseOrderRequest(vendor_name="V", po_date=HARI, items=[ITEM]).payment_terms_days is None       # absen = bawaan vendor


def test_skema_ubah_hanya_angka_pengosongan_ditolak():
    assert S.UpdatePurchaseOrderRequest(payment_terms_days=14).model_dump(exclude_unset=True) == {"payment_terms_days": 14}
    assert S.UpdatePurchaseOrderRequest(payment_terms_days=0).payment_terms_days == 0                                   # 0 = Tunai sah
    assert "payment_terms_days" not in S.UpdatePurchaseOrderRequest(notes="x").model_dump(exclude_unset=True)            # absen = tak diubah
    with pytest.raises(ValidationError) as e:
        S.UpdatePurchaseOrderRequest(payment_terms_days=None)
    assert "tidak bisa dikosongkan" in str(e.value)
    with pytest.raises(ValidationError):
        S.UpdatePurchaseOrderRequest(payment_terms_days=366)


class _Kon:
    def __init__(self, vendor_hari=None):
        self.vendor_hari = vendor_hari

    async def fetchval(self, sql, *a):
        assert "FROM vendors" in sql and a[1] == TID
        return self.vendor_hari


@pytest.mark.parametrize("po_terms,vendor,body,sumber,hari,teks,jatuh", [
    (45, 60, None, "po_terms", 45, "Termin PO 45 hari", date(2026, 12, 28)),      # termin PO mengalahkan vendor
    (0, 60, None, "po_terms", 0, "Tunai", HARI),                                  # 0 = Tunai: jatuh tempo = tanggal tagihan
    (None, 60, None, "vendor_terms", 60, "Termin vendor 60 hari", date(2027, 1, 12)),   # PO lama (NULL) = perilaku lama
    (999, 60, None, "vendor_terms", 60, "Termin vendor 60 hari", date(2027, 1, 12)),    # di luar 0..365 diabaikan
    (None, None, None, "default", 0, "Tanggal faktur", HARI),
    (45, 60, date(2026, 12, 1), "body", None, "Diisi pengguna", date(2026, 12, 1)),     # isian pengguna menang atas semuanya
])
def test_urutan_jatuh_tempo_tagihan(po_terms, vendor, body, sumber, hari, teks, jatuh):
    jt = jalan(TB.tentukan_jatuh_tempo_tagihan(_Kon(vendor), TID, HARI, body, uuid.uuid4(), po_terms=po_terms))
    assert (jt["due_date_source"], jt["payment_terms_days"], jt["due_date_text"], jt["due_date"]) == (sumber, hari, teks, jatuh)


def test_kedua_jalur_tagih_meneruskan_termin_po():
    src = inspect.getsource(PO)
    assert src.count('po_terms=po.get("payment_terms_days"))') == 2          # /to-bill DAN /to-bill/preview (satu penentu)


class _KonBawaan:
    def __init__(self, n):
        self.n, self.sql = n, []

    async def fetchval(self, sql, *a):
        self.sql.append((sql, a))
        assert "FROM vendors" in sql and "tenant_id = $2" in sql and a[1] == TID
        return self.n


@pytest.mark.parametrize("termin_body,vendor_id,vendor_n,harap", [(7, uuid.uuid4(), 45, 7), (0, uuid.uuid4(), 45, 0), (None, uuid.uuid4(), 45, 45),
                                                                  (None, uuid.uuid4(), 0, 0), (None, uuid.uuid4(), None, None), (None, uuid.uuid4(), 400, None),
                                                                  (None, None, 45, None)])
def test_bawaan_termin_snapshot(termin_body, vendor_id, vendor_n, harap):
    k = _KonBawaan(vendor_n)
    assert jalan(PO.bawaan_termin(k, TID, termin_body, vendor_id)) == harap
    if termin_body is not None or vendor_id is None:
        assert k.sql == []                                                    # isian pengguna / tanpa vendor: tak membaca vendor


def test_termin_syarat_teks():
    assert PC.termin_syarat(None) is None and PC.termin_syarat(0) == "Tunai" and PC.termin_syarat(30) == "30 hari"


class _Conn:
    def __init__(self, po, vendor):
        self.po, self.vendor = po, vendor

    async def fetchrow(self, sql, *a):
        if "FROM purchase_orders" in sql:
            return self.po
        if "FROM vendors" in sql:
            return self.vendor
        if '"Tenant"' in sql:
            return {"display_name": "Usaha", "address": "Jl. 1", "phone": None, "logo_url": None, "tax_id": None}
        raise AssertionError(sql)

    async def fetch(self, sql, *a):
        return [{"description": "Kain", "item_code": "K1", "quantity": D("1"), "unit": "m", "unit_price": D("1000"), "discount_percent": D("0"), "discount_amount": D("0"),
                 "subtotal": D("1000"), "total": D("1000"), "tax_rate": D("0"), "tax_amount": D("0")}]


def _po(**k):
    d = {"id": uuid.uuid4(), "po_number": "PO-1", "status": "sent", "po_date": HARI, "expected_date": None, "ref_no": None, "ship_to_address": None, "warehouse_name": None,
         "notes": None, "cancelled_at": None, "cancelled_reason": None, "vendor_id": uuid.uuid4(), "vendor_name": "V", "subtotal": D("1000"), "discount_percent": D("0"),
         "discount_amount": D("0"), "tax_rate": D("0"), "tax_amount": D("0"), "total_amount": D("1000")}
    d.update(k)
    return d


def _html(po, vendor_hari=99):
    vendor = {"name": "V", "address": None, "city": None, "province": None, "postal_code": None, "phone": None, "mobile_phone": None, "email": None, "tax_id": None,
              "payment_terms_days": vendor_hari}
    m = jalan(PC.muat_pdf_po(_Conn(po, vendor), {"tenant_id": TID}, uuid.uuid4()))
    return PDFService().render_purchase_order(m["po_data"], m["tenant_info"]).html, m["po_data"]


@pytest.mark.parametrize("snap,teks", [(45, "45 hari"), (0, "Tunai")])
def test_pdf_mencetak_snapshot_bukan_termin_vendor(snap, teks):
    h, d = _html(_po(payment_terms_days=snap), vendor_hari=99)
    assert d["syarat_bayar"] == teks and f"<b>Syarat bayar</b><span>{teks}</span>" in h and "99 hari" not in h


def test_pdf_po_lama_tanpa_snapshot_tak_mencetak_syarat_bayar():
    h, d = _html(_po(), vendor_hari=30)                                       # tanpa kunci payment_terms_days (PO lama / NULL)
    assert d["syarat_bayar"] is None and "Syarat bayar" not in h.split("</style>", 1)[-1]
    h2, d2 = _html(_po(payment_terms_days=None), vendor_hari=30)
    assert d2["syarat_bayar"] is None and "Syarat bayar" not in h2.split("</style>", 1)[-1]


def test_detail_memuat_snapshot_dan_teks():
    src = inspect.getsource(PO.get_purchase_order)
    assert '"payment_terms_days": po.get("payment_terms_days")' in src and '"syarat_bayar_text": termin_syarat(po.get("payment_terms_days"))' in src
    assert "payment_terms_days" in S.PurchaseOrderDetail.model_fields and "syarat_bayar_text" in S.PurchaseOrderDetail.model_fields


def test_buat_menyimpan_snapshot_dengan_bawaan_vendor():
    src = inspect.getsource(PO.create_purchase_order)
    assert "bawaan_termin(conn, ctx[\"tenant_id\"], body.payment_terms_days" in src and "created_by, payment_terms_days" in src and "$16, $17, $18)" in src
    # argumen ke-17 yang DIKIRIM ke INSERT harus snapshot `terms` (bukan None/konstanta): terms = nilai terakhir sebelum penutup
    assert re.search(r'ctx\["user_id"\],\s*terms,\s*_alamat\["warehouse_id"\],', src), "snapshot terms tak dikirim sebagai $17"


def test_migrasi_aditif_nullable_check_tanpa_backfill_dan_rollback():
    m = (MIG / "V496__po_payment_terms_days.sql").read_text()
    isi = "\n".join(x for x in m.splitlines() if not x.strip().startswith("--"))
    assert "ADD COLUMN IF NOT EXISTS payment_terms_days INTEGER;" in isi and "NOT NULL" not in isi and "DEFAULT" not in isi
    assert re.search(r"payment_terms_days >= 0 AND payment_terms_days <= 365\)", isi)  # batas TEPAT 365 (bukan awalan 3650) and "chk_po_payment_terms_days" in isi
    for terlarang in ("UPDATE ", "INSERT ", "DELETE ", "vendors"):
        assert terlarang not in isi                                            # TANPA backfill / tanpa menyentuh data vendor
    rb = (MIG / "V496__po_payment_terms_days_ROLLBACK.sql").read_text()
    assert "DROP CONSTRAINT IF EXISTS chk_po_payment_terms_days" in rb and "DROP COLUMN IF EXISTS payment_terms_days" in rb
