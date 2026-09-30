"""NK "dari faktur" (30 Sep 2026, MASTER/WORKSPACE): V348 credit_note_items.original_invoice_item_id + pratinjau bentuk
(2) -- items ABSEN + original_invoice_id -> baris dari faktur (harga/diskon/kode pajak/pelanggan), `lines` memilih qty.

Bisa-dinotakan per baris = INFORMASI (belum penghalang, menunggu putusan pemilik). Tautan baris DIVALIDASI di buat/ubah
(dulu dibuang diam). Uji nyata di salinan DB: NK penuh dari faktur = angka faktur.
"""
import os
from datetime import date
from decimal import Decimal
from uuid import UUID

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.routers import credit_notes as CN  # noqa: E402

T = "kaos-biru-konveksi"
INV = UUID("20000000-0000-0000-0000-0000000000e1")
L1, L2 = UUID(int=101), UUID(int=102)


def _baris(i, qty, harga, pct=0, amt=0, kode=None, tarif=0):
    return {"id": i, "item_id": None, "item_code": None, "description": f"B{i.int}", "quantity": Decimal(qty),
            "unit": "pcs", "unit_price": Decimal(harga), "discount_percent": Decimal(pct), "discount_amount": Decimal(amt),
            "tax_code": None, "tax_code_id": kode, "tax_rate": Decimal(tarif)}


class _C:
    def __init__(self, rows=None, sudah=None, lawas=(), diskon_dok=Decimal("0"), ongkir=Decimal("0"), ada=True):
        self.rows = rows if rows is not None else [_baris(L1, "4", "100000", amt="40000", kode=UUID(int=7), tarif=12),
                                                  _baris(L2, "2", "50000")]
        self.sudah, self.lawas, self.diskon_dok, self.ongkir, self.ada = sudah or {}, list(lawas), diskon_dok, ongkir, ada

    async def fetchrow(self, sql, *a):
        assert "FROM sales_invoices WHERE id = $1 AND tenant_id = $2" in sql and a[1] == T
        return {"id": INV, "invoice_number": "INV-7", "customer_id": UUID(int=4), "customer_name": "Budi", "status": "posted",
                "discount_amount": self.diskon_dok, "shipping_amount": self.ongkir} if self.ada else None

    async def fetch(self, sql, *a):
        if "FROM sales_invoice_items WHERE invoice_id" in sql:
            return list(self.rows)
        if "SUM(cni.quantity)" in sql:
            assert "cn.status <> 'void'" in sql and "cn.tenant_id = $1" in sql
            return [{"oid": k, "q": v} for k, v in self.sudah.items()]
        if "original_invoice_item_id IS NULL" in sql:
            return [{"credit_note_number": n} for n in self.lawas]
        raise AssertionError(sql[:70])


def _b(**k):
    return CN.CreditNotePreviewRequest(reason="return", original_invoice_id=str(INV), **k)


@pytest.mark.asyncio
async def test_semua_baris_bawaan_dari_faktur_dengan_tautan():
    r = await CN.rakit_dari_faktur(_C(), T, _b())
    assert r["blocks"] == []
    c = r["create"]
    assert c["customer_id"] == str(UUID(int=4)) and c["customer_name"] == "Budi" and c["original_invoice_id"] == str(INV)
    assert [(i["original_invoice_item_id"], i["quantity"], i["discount_amount"], i["tax_code_id"], i["tax_rate"]) for i in c["items"]] == [
        (str(L1), Decimal("4"), Decimal("40000.00"), str(UUID(int=7)), 12.0), (str(L2), Decimal("2"), Decimal("0.00"), None, 0.0)]
    assert [(x["invoiced"], x["already_credited"], x["creditable"], x["quantity"]) for x in r["lines"]] == [
        (4.0, 0.0, 4.0, 4.0), (2.0, 0.0, 2.0, 2.0)]


@pytest.mark.asyncio
async def test_sebagian_diskon_baris_diskalakan_dan_diskon_dokumen_prorata():
    # neto L1 = 400.000 - 40.000 = 360.000; L2 = 100.000; total neto 460.000; diskon dok 46.000
    r = await CN.rakit_dari_faktur(_C(diskon_dok=Decimal("46000")), T,
                                   _b(lines=[{"original_invoice_item_id": str(L1), "quantity": "1"}]))
    it = r["create"]["items"]
    assert len(it) == 1 and it[0]["discount_amount"] == Decimal("10000.00")  # 40.000 x 1/4
    # neto terpilih = 100.000 - 10.000 = 90.000 -> 46.000 x 90/460 = 9.000
    assert r["create"]["discount_amount"] == Decimal("9000.00")
    assert [x["quantity"] for x in r["lines"]] == [1.0, 0.0]


@pytest.mark.asyncio
async def test_sudah_dinotakan_mengurangi_bisa_dan_lebih_hanya_catatan():
    r = await CN.rakit_dari_faktur(_C(sudah={L1: Decimal("3")}, lawas=["CN-OLD"]), T,
                                   _b(lines=[{"original_invoice_item_id": str(L1), "quantity": "2"}]))
    assert r["lines"][0]["already_credited"] == 3.0 and r["lines"][0]["creditable"] == 1.0
    kode = [n["code"] for n in r["notes"]]
    assert kode == ["CN_LEGACY_UNLINKED"]
    # rakit tak menolak sendiri; blok datang dari _rencana_nota_kredit -> periksa_bisa_dinotakan (satu sumber)
    assert r["blocks"] == [] and r["create"]["items"][0]["quantity"] == Decimal("2")


@pytest.mark.asyncio
async def test_baris_luar_faktur_nol_dan_faktur_tak_ada():
    r = await CN.rakit_dari_faktur(_C(), T, _b(lines=[{"original_invoice_item_id": str(UUID(int=999)), "quantity": "1"}]))
    assert [b["code"] for b in r["blocks"]] == ["CN_LINE_NOT_IN_INVOICE"] and r["create"] is None
    r = await CN.rakit_dari_faktur(_C(), T, _b(lines=[]))
    assert [b["code"] for b in r["blocks"]] == ["CN_NOTHING_TO_CREDIT"]
    r = await CN.rakit_dari_faktur(_C(ada=False), T, _b())
    assert [b["code"] for b in r["blocks"]] == ["CN_INVOICE_INVALID"]


class _Cek:
    def __init__(self, milik):
        self.milik = milik

    async def fetch(self, sql, *a):
        assert "si.tenant_id = $3" in sql and "si.id = $2" in sql
        return [{"id": u} for u in a[0] if u in self.milik]


@pytest.mark.asyncio
async def test_tautan_baris_divalidasi():
    await CN.periksa_tautan_baris_faktur(_Cek({L1}), T, INV, [{"original_invoice_item_id": str(L1)}, {}])
    with pytest.raises(HTTPException) as e:
        await CN.periksa_tautan_baris_faktur(_Cek({L1}), T, INV, [{"original_invoice_item_id": str(L2)}])
    assert e.value.status_code == 400
    with pytest.raises(HTTPException):
        await CN.periksa_tautan_baris_faktur(_Cek(set()), T, None, [{"original_invoice_item_id": str(L1)}])
    await CN.periksa_tautan_baris_faktur(_Cek(set()), T, None, [{"description": "x"}])  # tanpa tautan: lolos


def test_insert_baris_nk_menyimpan_tautan():
    import ast
    src = open(CN.__file__, encoding="utf-8").read()
    ins = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Constant) and isinstance(n.value, str)
           and "INSERT INTO credit_note_items" in n.value]
    assert len(ins) == 2 and all("original_invoice_item_id" in n.value for n in ins)
    for nama in ("buat_nota_kredit", "update_credit_note"):
        fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.AsyncFunctionDef) and n.name == nama)
        assert any(isinstance(c, ast.Call) and getattr(c.func, "id", None) == "periksa_tautan_baris_faktur"
                   for c in ast.walk(fn)), nama


class _Bisa:
    """baris faktur L1 qty 4; NK lain tak-void sudah 3 (draf ikut)."""

    def __init__(self, sudah=Decimal("3")):
        self.sudah, self.kunci, self.args = sudah, [], None

    async def execute(self, sql, *a):
        self.kunci.append(a[0])

    async def fetch(self, sql, *a):
        assert "cn.status <> 'void'" in sql and "cn.id <> $4::uuid" in sql and "si.tenant_id = $3" in sql
        self.args = a
        return [{"id": L1, "description": "Kaos", "quantity": Decimal("4"), "sudah": self.sudah}]


@pytest.mark.asyncio
async def test_penjaga_bisa_dinotakan_per_baris():
    c = _Bisa()
    await CN.periksa_bisa_dinotakan(c, T, INV, [{"original_invoice_item_id": str(L1), "quantity": Decimal("1")}])
    assert c.kunci == [f"CN_FAKTUR:{T}:{INV}"]  # diserialkan per faktur
    with pytest.raises(HTTPException) as e:
        await CN.periksa_bisa_dinotakan(_Bisa(), T, INV, [{"original_invoice_item_id": str(L1), "quantity": Decimal("1")},
                                                           {"original_invoice_item_id": str(L1), "quantity": Decimal("1")}])
    assert e.value.status_code == 400 and e.value.detail["code"] == "CN_QTY_EXCEEDS_CREDITABLE"
    assert e.value.detail["lines"] == [{"original_invoice_item_id": str(L1), "description": "Kaos", "requested": 2.0, "creditable": 1.0}]
    c = _Bisa()
    await CN.periksa_bisa_dinotakan(c, T, INV, [{"original_invoice_item_id": str(L1), "quantity": Decimal("1")}], kecuali_cn=UUID(int=55))
    assert c.args[3] == UUID(int=55)  # NK ini sendiri dikecualikan (ubah/posting)
    await CN.periksa_bisa_dinotakan(_Bisa(), T, INV, [{"description": "tanpa tautan", "quantity": 99}])  # lama: tak dihitung


def test_penjaga_dipanggil_di_buat_ubah_posting_dan_pratinjau():
    import ast
    src = open(CN.__file__, encoding="utf-8").read()
    t = ast.parse(src)
    for nama in ("buat_nota_kredit", "update_credit_note", "posting_nota_kredit", "_rencana_nota_kredit"):
        fn = next(n for n in ast.walk(t) if isinstance(n, ast.AsyncFunctionDef) and n.name == nama)
        assert any(isinstance(c, ast.Call) and getattr(c.func, "id", None) == "periksa_bisa_dinotakan"
                   for c in ast.walk(fn)), nama
