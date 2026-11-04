"""HPP-NETO (4 Nov 2026, desain disetujui MASTER): biaya kartu stok FPB (create_bill_v2 + post_bill) = nilai baris jurnal (neto diskon
baris + porsi diskon kepala/tunai pro-rata, tagihan_aksi.neto_baris) / qty dasar -> Σ kartu == Dr Persediaan. Dulu qty x harga BRUTO
(diukur nyata: diskon baris 5% -> kartu 10000, neto 9500). GRN tidak diubah. Kirim PO: satuan ber-stok tanpa konversi -> blok berkalimat
SAMA dengan Terima (draf tetap boleh disimpan). Tiruan DB mencocokkan POLA kueri."""
import ast
import uuid
from decimal import Decimal as D
from pathlib import Path

import pytest

from app.services import po_rencana as PR
from app.services import satuan_dasar as SD
from app.services import tagihan_aksi as TA

APP = Path(TA.__file__).resolve().parents[1]
T = "kaos-biru-konveksi"
INV, BEBAN = uuid.UUID(int=1), uuid.UUID(int=2)


def _fungsi(rel, nama):
    pohon = ast.parse((APP / rel).read_text())
    return next(n for n in ast.walk(pohon) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nama)


class KonTagih:
    def __init__(self, baris):
        self.baris = baris

    async def fetch(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM bill_items bi LEFT JOIN products p" in q and "ORDER BY bi.line_number" in q:
            return self.baris
        raise AssertionError(q[:100])


def _b(net, jenis="goods"):
    return {"id": uuid.uuid4(), "product_id": uuid.uuid4(), "net": D(net), "item_type": jenis, "track_inventory": jenis == "goods",
            "purchase_account_id": None}


@pytest.mark.asyncio
async def test_kartu_fpb_sama_dengan_dr_persediaan_diskon_kepala_pro_rata(monkeypatch):
    baris = [_b("1368000.00"), _b("333333.33"), _b("100000.00", "service")]
    sub = D("1621199.99")  # diskon kepala ~10% atas Σ neto 1801333.33 (sen tak rata)

    async def bukan_pembuka(*a): return False
    async def akun_inv(conn, tid, pid): return {"inventory_account_id": INV}
    async def beban(*a): return BEBAN
    monkeypatch.setattr(TA, "tagihan_pembuka", bukan_pembuka)
    monkeypatch.setattr(TA, "akun_beban_default", beban)
    import app.services.inventory_helpers as IH
    monkeypatch.setattr(IH, "resolve_inventory_accounts", akun_inv)
    k = KonTagih(baris)
    debit = {a: v for a, v, _ in await TA.hitung_debit(k, T, uuid.uuid4(), sub, INV)}
    nilai = await TA.nilai_baris(k, uuid.uuid4(), sub)
    stok = sum((nilai[b["id"]] for b in baris if b["item_type"] == "goods"), D("0"))
    assert debit[INV] == stok and sum(debit.values(), D("0")) == sub  # Σ kartu baris stok == Dr Persediaan; Σ debit == neto dokumen
    assert debit[BEBAN] == nilai[baris[2]["id"]]  # porsi kepala baris jasa ke akunnya sendiri (bukan ditumpuk ke persediaan)
    assert all(v == v.quantize(D("0.01")) for v in nilai.values())


@pytest.mark.parametrize("rel,nama", [("services/bills_service.py", "create_bill_v2"), ("services/bills_service.py", "post_bill")])
def test_kartu_fpb_memakai_nilai_baris_bukan_harga_bruto(rel, nama):
    src = ast.unparse(_fungsi(rel, nama))
    assert "TA.nilai_baris(conn, bill_id," in src and "SD.biaya_dasar(total_cost, quantity)" in src
    assert "unit_price']))" not in src.split("TA.nilai_baris", 1)[1].split("record_inventory_inbound", 1)[0]  # tanpa qty x harga bruto


def test_hitung_debit_dan_kartu_satu_sumber_baris():
    hd = ast.unparse(_fungsi("services/tagihan_aksi.py", "hitung_debit"))
    nb = ast.unparse(_fungsi("services/tagihan_aksi.py", "nilai_baris"))
    assert "neto_baris(rows, subtotal_dec)" in hd and "SQL_BARIS_DEBIT" in hd and "neto_baris(" in nb and "SQL_BARIS_DEBIT" in nb


class KonKirim:
    def __init__(self, unit, konversi):
        self.unit, self.konversi = unit, konversi

    async def fetch(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM purchase_order_items i JOIN products p" in q and "track_inventory" in q:
            return [{"product_id": uuid.UUID(int=9), "unit": self.unit, "quantity": D("12")}]
        if "FROM product_units" in q:
            return []
        if "FROM products" in q and "nama_produk" in q:
            return [{"id": uuid.UUID(int=9), "nama_produk": "Minyak Kita", "dasar": "Dus"}]
        if "FROM unit_conversions" in q:
            return [{"product_id": uuid.UUID(int=9), "base_unit": "Dus", "conversion_unit": "Krat", "conversion_factor": D("12")}] if self.konversi else []
        raise AssertionError(q[:100])


@pytest.mark.asyncio
@pytest.mark.parametrize("unit,konversi,status,kode", [
    ("Krat", False, "draft", ["SATUAN_TAK_TERKONVERSI"]),
    ("Krat", True, "draft", []),
    ("Dus", False, "draft", []),
    ("Krat", False, "sent", ["PO_TAK_BISA_DIKIRIM", "SATUAN_TAK_TERKONVERSI"])])
async def test_kirim_po_satuan_tanpa_konversi_diblok_kalimat_sama_terima(unit, konversi, status, kode):
    r = await PR.rencana_kirim(KonKirim(unit, konversi), T, {"id": uuid.uuid4(), "status": status})
    assert [b["code"] for b in r["blocks"]] == kode
    if "SATUAN_TAK_TERKONVERSI" in kode:
        assert r["blocks"][-1]["message"] == SD.tg.satuan_tak_terkonversi("Krat", "Minyak Kita", "Dus")


def test_kirim_tunggal_massal_pratinjau_memakai_penentu_sama():
    for rel, nama in (("routers/purchase_orders.py", "kirim_po_core"), ("routers/purchase_orders.py", "preview_send_purchase_order"),
                      ("routers/bulk_po.py", "_blok")):
        assert "await PR.rencana_kirim(conn," in ast.unparse(_fungsi(rel, nama)), (rel, nama)
