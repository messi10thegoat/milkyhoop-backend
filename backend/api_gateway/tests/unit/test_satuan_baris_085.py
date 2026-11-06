"""BUG-085 (6 Nov 2026): satuan ketikan per baris ND / NK / Kirim SJ -> qty dalam satuan BARIS dokumen asal.

Grosir mengetik "retur 1 dus" pada baris faktur ber-satuan karton/botol; dulu badan baris hanya {id baris, quantity} sehingga qty
selalu dibaca dalam satuan baris. Kini `unit` opsional per baris; SATU penentu `satuan_dasar.ke_satuan_baris` (lewat satuan dasar,
konversi barang) dipakai NK/ND "dari faktur" (pratinjau -> payload create) dan SJ dari SO (rencana = pratinjau + tulis).
Absen = perilaku lama. Tak terkonversi / tidak pas 4 desimal = blok berkalimat.
"""
import asyncio
import os
import uuid
from decimal import Decimal as D

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.routers import credit_notes as CN  # noqa: E402
from app.routers import vendor_credits as VC  # noqa: E402
from app.services import satuan_dasar as SD  # noqa: E402
from app.services import sj_so as S  # noqa: E402

T = "t-uji"
P = uuid.UUID(int=7)  # Minyak Uji Gladi 1L: dasar botol; 1 dus = 12 botol; 1 karton = 120 botol (data GLADI-1)
KTX = {"alias": {"btl": "botol"}, "barang": {str(P): {"nama": "Minyak 1L", "dasar": "botol",
                                                     "konversi": {"dus": D("12"), "karton": D("120")}}}}


def _jalan(c):
    return asyncio.run(c)


def _sd(unit, qty, unit_baris, pid=P):
    return SD.ke_satuan_baris(KTX, pid, unit, D(str(qty)), unit_baris, "Minyak 1L")


# ---------------- penentu ----------------
@pytest.mark.parametrize("unit,qty,baris,hasil", [
    (None, "3", "karton", D("3")), ("", "3", "karton", D("3")),          # absen = perilaku lama
    ("Karton", "3", "karton", D("3")), ("  KARTON ", "2", "karton", D("2")),  # sama sesudah normalisasi
    ("dus", "1", "karton", D("0.1")),                                     # retur 1 dus dari baris karton (GLADI-1 ND)
    ("botol", "24", "dus", D("2")), ("btl", "12", "dus", D("1")),          # satuan dasar + alias kamus tenant
    ("karton", "1", "botol", D("120")), ("dus", "1", None, D("12")),       # baris dalam satuan dasar (unit baris kosong = dasar)
])
def test_konversi_ke_satuan_baris(unit, qty, baris, hasil):
    h = _sd(unit, qty, baris)
    assert h["blok"] is None and h["qty"] == hasil


def test_tidak_pas_empat_desimal_diblok_berkalimat():
    h = _sd("botol", "5", "karton")  # 5/120 = 0,041666...
    assert h["blok"]["code"] == "SATUAN_TIDAK_HABIS_DIBAGI" and h["blok"]["status"] == 400
    assert h["blok"]["message"] == "5 botol Minyak 1L tidak pas dalam karton (1 karton = 120 botol). Isi jumlah yang pas dalam karton."


def test_satuan_tak_dikenal_dan_baris_tanpa_barang_diblok():
    assert _sd("pak", "1", "karton")["blok"]["code"] == SD.KODE_TAK_TERKONVERSI
    assert _sd("lusin", "1", "lusin2")["blok"]["code"] == SD.KODE_TAK_TERKONVERSI  # satuan baris sendiri tanpa konversi
    h = _sd("dus", "1", "karton", pid=None)  # baris jasa / teks bebas
    assert h["blok"]["code"] == SD.KODE_TAK_TERKONVERSI and "Minyak 1L" in h["blok"]["message"]


# ---------------- tiga jalur: muat diganti tabel konversi tetap ----------------
@pytest.fixture
def ktx(monkeypatch):
    diminta = []

    async def muat(conn, tid, ids):
        diminta.append([str(i) for i in ids])
        return KTX
    monkeypatch.setattr(SD, "muat", muat)
    return diminta


L1, L2 = uuid.UUID(int=101), uuid.UUID(int=102)


class _NK:
    async def fetchrow(self, sql, *a):
        return {"id": uuid.UUID(int=1), "invoice_number": "INV-7", "customer_id": uuid.UUID(int=4), "customer_name": "Toko",
                "status": "posted", "discount_amount": D("0"), "shipping_amount": D("0")}

    async def fetch(self, sql, *a):
        if "FROM sales_invoice_items WHERE invoice_id" in sql:
            b = {"item_code": None, "unit_price": D("1600000"), "discount_percent": D("0"), "discount_amount": D("0"),
                 "tax_code": None, "tax_code_id": None, "tax_rate": D("0")}
            return [{**b, "id": L1, "item_id": P, "description": "Minyak 1L", "quantity": D("2"), "unit": "karton"},
                    {**b, "id": L2, "item_id": None, "description": "Ongkos bongkar", "quantity": D("1"), "unit": "kali"}]
        return []


def _nk(lines):
    return _jalan(CN.rakit_dari_faktur(_NK(), T, CN.CreditNotePreviewRequest(
        reason="return", original_invoice_id=str(uuid.UUID(int=1)), lines=lines)))


def test_nk_dari_faktur_satu_dus_jadi_satuan_baris_create_ikut(ktx):
    r = _nk([{"original_invoice_item_id": str(L1), "quantity": "1", "unit": "dus"}])
    assert r["blocks"] == []
    assert [(i["original_invoice_item_id"], i["quantity"], i["unit"]) for i in r["create"]["items"]] == [(str(L1), D("0.1000"), "karton")]
    assert ktx == [[str(P)]]  # hanya baris bersatuan dibaca


def test_nk_tanpa_unit_perilaku_lama_dan_blok_berkode(ktx):
    r = _nk([{"original_invoice_item_id": str(L1), "quantity": "1"}])
    assert r["blocks"] == [] and r["create"]["items"][0]["quantity"] == D("1")
    r = _nk([{"original_invoice_item_id": str(L1), "quantity": "5", "unit": "botol"},
             {"original_invoice_item_id": str(L2), "quantity": "1", "unit": "dus"}])
    assert [(b["code"], b["original_invoice_item_id"]) for b in r["blocks"]] == [
        ("SATUAN_TIDAK_HABIS_DIBAGI", str(L1)), (SD.KODE_TAK_TERKONVERSI, str(L2))]


class _ND:
    async def fetchrow(self, sql, *a):
        if "FROM bills" in sql:
            return {"id": a[0], "invoice_number": "BILL-2611-0007", "vendor_id": uuid.UUID(int=9), "vendor_name": "Vendor", "status_v2": "posted",
                    "status": "unpaid", "tax_rate": 0, "tax_code_id": None, "tax_inclusive": False, "dpp_manual": None,
                    "invoice_discount_total": D("0"), "cash_discount_total": D("0"), "amount": D("3200000")}
        return None

    async def fetch(self, sql, *a):
        if "FROM bill_items" in sql:
            return [{"id": L1, "product_id": P, "product_code": "BRG-0002", "product_name": "Minyak 1L", "description": "Minyak 1L",
                     "quantity": D("2"), "unit": "karton", "unit_price": D("1600000"), "discount_percent": D("0"),
                     "discount_amount": D("0"), "tax_rate": D("0"), "tax_code_id": None}]
        return []


def _nd(lines):
    return _jalan(VC.rakit_dari_tagihan(_ND(), T, VC.VendorCreditPreviewRequest(
        reason="return", vendor_credit_date="2026-11-06", original_bill_id=str(uuid.UUID(int=3)), lines=lines)))


def test_nd_dari_tagihan_satu_dus_jadi_satuan_baris_dan_blok(ktx):
    r = _nd([{"original_bill_item_id": str(L1), "quantity": "1", "unit": "dus"}])
    assert [b["code"] for b in r["blocks"]] == []
    assert [(i["quantity"], i["unit"]) for i in r["create"]["items"]] == [(D("0.1000"), "karton")]  # ND belum menyimpan tautan baris
    r = _nd([{"original_bill_item_id": str(L1), "quantity": "1", "unit": "pak"}])
    assert [(b["code"], b["original_bill_item_id"]) for b in r["blocks"]] == [(SD.KODE_TAK_TERKONVERSI, str(L1))]
    r = _nd([{"original_bill_item_id": str(L1), "quantity": "1"}])  # absen = lama
    assert r["create"]["items"][0]["quantity"] == D("1")


# ---------------- SJ dari SO: rencana dipakai pratinjau DAN tulis ----------------
class _SJ:
    def __init__(self):
        self.rows = [{"id": L1, "item_id": P, "description": "Minyak 1L", "quantity": D("2"), "unit": "dus", "perlu_kirim": True,
                      "stok": False, "nama_produk": "Minyak 1L", "batch": False, "serial": False, "item_code": "BRG-0002"}]

    async def fetchval(self, sql, *a):
        return 1 if "FROM tenant_features" in sql and a and a[1] == S.FLAG else None

    async def fetchrow(self, sql, *a):
        if "FROM sales_orders WHERE id" in sql:
            return {"id": a[0], "order_number": "SO-2611-0011", "status": "confirmed", "customer_id": uuid.UUID(int=4),
                    "customer_name": "Toko", "warehouse_id": None}
        return None

    async def fetch(self, sql, *a):
        return self.rows if "FROM sales_order_items soi" in " ".join(sql.split()) else []


@pytest.fixture
def sj(monkeypatch, ktx):
    async def hari(conn, tid):
        from datetime import date
        return date(2026, 11, 6)
    monkeypatch.setattr(S, "tanggal_dokumen", hari)

    async def terkirim(conn, tid, so_ids):
        return ({}, {})
    monkeypatch.setattr(S.so_kirim, "terkirim_per_baris", terkirim)

    async def periode(conn, tid, tanggal):
        return None
    monkeypatch.setattr(S, "_periode_terbuka", periode)


def _rsj(items):
    return _jalan(S.rencana(_SJ(), {"tenant_id": T, "user_id": uuid.uuid4()}, uuid.UUID(int=5), S.SJSOIn(items=items)))


def test_sj_dua_belas_botol_jadi_satu_dus_dan_sisa_dihitung_dalam_satuan_baris(sj):
    r = _rsj([{"sales_order_item_id": L1, "quantity": "12", "unit": "botol"}])
    assert r["blocks"] == [] and [(l["quantity"], l["unit"]) for l in r["lines"]] == [(D("1.0000"), "dus")]
    r = _rsj([{"sales_order_item_id": L1, "quantity": "36", "unit": "botol"}])  # 3 dus > sisa 2 dus
    assert [b["code"] for b in r["blocks"]] == ["SJ_SO_QTY_MELEBIHI_SISA"]


def test_sj_tanpa_unit_lama_dan_tidak_pas_diblok(sj):
    assert [l["quantity"] for l in _rsj([{"sales_order_item_id": L1, "quantity": "2"}])["lines"]] == [D("2")]
    r = _rsj([{"sales_order_item_id": L1, "quantity": "5", "unit": "botol"}])
    assert [(b["code"], b["sales_order_item_id"]) for b in r["blocks"]] == [("SATUAN_TIDAK_HABIS_DIBAGI", str(L1))]
