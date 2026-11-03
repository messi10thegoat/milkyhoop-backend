"""QTY-DASAR R1 (3 Nov 2026): SATU penentu satuan dokumen -> satuan dasar (services/satuan_dasar) + pemasangannya di setiap jalur stok.

Spek:
  * sama dengan dasar (huruf/spasi + alias kamus product_units: "Kg" == "kilogram") -> faktor 1; "Pcs" != "Kg";
  * mode konversi (jalur MASUK): satuan ber-konversi -> qty x faktor (Decimal, desimal boleh: 1 jerigen = 18 L, ½ L); tanpa konversi
    -> 400 SATUAN_TAK_TERKONVERSI berkalimat (dulu 1:1 diam);
  * mode tolak (jalur KELUAR R1): satuan selain dasar -> 400 SATUAN_BELUM_DIDUKUNG walau berkonversi (tak ada keadaan campur);
  * tiap jalur stok memanggil penentu dengan mode yang benar (pemindai AST, docstring dikecualikan);
  * jalur MASUK menulis kartu stok qty x faktor dan biaya / faktor (GRN, FPB)."""
import ast
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services import satuan_dasar as SD
from app.services import teks_galat as tg

T = "kaos-biru-konveksi"
P_MINYAK, P_LAIN = uuid.uuid4(), uuid.uuid4()
APP = Path(SD.__file__).resolve().parents[1]


class Kon:
    def __init__(self, konversi=None, kamus=None, dasar="Botol"):
        self.kamus = kamus if kamus is not None else [{"name": "Botol", "abbreviation": "btl"}, {"name": "Kilogram", "abbreviation": "kg"},
                                                      {"name": "Karton", "abbreviation": "krt"}, {"name": "Liter", "abbreviation": "ltr"},
                                                      {"name": "Pieces", "abbreviation": "pcs"}]
        self.konversi = konversi if konversi is not None else [
            {"product_id": P_MINYAK, "base_unit": "botol", "conversion_unit": "Karton", "conversion_factor": Decimal("12")}]
        self.dasar = dasar
        self.sql = []

    async def fetch(self, sql, *a):
        q = " ".join(sql.split())
        self.sql.append(q)
        if "FROM product_units" in q:
            return self.kamus
        if "FROM products" in q:
            return [{"id": P_MINYAK, "nama_produk": "Minyak Goreng 1L", "dasar": self.dasar},
                    {"id": P_LAIN, "nama_produk": "Gula Pasir", "dasar": "kg"}]
        if "FROM unit_conversions" in q:
            return self.konversi
        return []


async def _h(kon, pid, unit, qty, mode, aksi="Pengiriman"):
    return SD.hitung(await SD.muat(kon, T, [pid]), pid, unit, qty, mode, aksi)


@pytest.mark.asyncio
@pytest.mark.parametrize("unit", [None, "", "Botol", " botol ", "BTL", "btl"])
async def test_satuan_dasar_dan_aliasnya_faktor_satu_di_kedua_mode(unit):
    for mode in ("konversi", "tolak"):
        h = await _h(Kon(), P_MINYAK, unit, 3, mode)
        assert (h["qty_dasar"], h["faktor"], h["blok"]) == (Decimal("3"), Decimal("1"), None), (unit, mode)


@pytest.mark.asyncio
async def test_alias_kamus_kg_sama_dengan_kilogram_pcs_tidak():
    assert (await _h(Kon(), P_LAIN, "Kilogram", 2, "tolak"))["blok"] is None
    assert (await _h(Kon(), P_LAIN, "KG", 2, "tolak"))["blok"] is None
    h = await _h(Kon(), P_LAIN, "Pcs", 2, "tolak")
    assert h["blok"]["code"] == SD.KODE_BELUM_DIDUKUNG


@pytest.mark.asyncio
async def test_konversi_karton_dua_belas_kali_lewat_alias():
    for unit in ("Karton", "karton", "KRT"):
        h = await _h(Kon(), P_MINYAK, unit, Decimal("2"), "konversi")
        assert (h["qty_dasar"], h["faktor"], h["blok"]) == (Decimal("24"), Decimal("12"), None), unit


@pytest.mark.asyncio
async def test_faktor_desimal_liter():
    k = Kon(dasar="Liter", konversi=[{"product_id": P_MINYAK, "base_unit": "ltr", "conversion_unit": "Jerigen", "conversion_factor": Decimal("18")},
                                     {"product_id": P_MINYAK, "base_unit": "Liter", "conversion_unit": "Botol", "conversion_factor": Decimal("0.5")}])
    assert (await _h(k, P_MINYAK, "Jerigen", 2, "konversi"))["qty_dasar"] == Decimal("36")
    assert (await _h(k, P_MINYAK, "botol", 3, "konversi"))["qty_dasar"] == Decimal("1.5")


@pytest.mark.asyncio
async def test_tanpa_konversi_ditolak_berkalimat_bukan_satu_banding_satu():
    h = await _h(Kon(konversi=[]), P_MINYAK, "Karton", 2, "konversi")
    assert h["qty_dasar"] == Decimal("2") and h["blok"] == {
        "code": "SATUAN_TAK_TERKONVERSI", "status": 400,
        "message": "Satuan Karton untuk Minyak Goreng 1L belum punya konversi ke Botol. Atur konversinya di Barang › Satuan."}
    assert h["blok"]["message"] == tg.satuan_tak_terkonversi("Karton", "Minyak Goreng 1L", "Botol")


@pytest.mark.asyncio
async def test_konversi_milik_dasar_lain_tidak_dipakai():
    k = Kon(konversi=[{"product_id": P_MINYAK, "base_unit": "pcs", "conversion_unit": "Karton", "conversion_factor": Decimal("24")}])
    assert (await _h(k, P_MINYAK, "Karton", 1, "konversi"))["blok"]["code"] == "SATUAN_TAK_TERKONVERSI"


@pytest.mark.asyncio
async def test_mode_tolak_menolak_walau_berkonversi_kalimat_master():
    h = await _h(Kon(), P_MINYAK, "Karton", 1, "tolak")
    assert h["blok"] == {"code": "SATUAN_BELUM_DIDUKUNG", "status": 400,
                         "message": "Pengiriman dengan satuan Karton belum didukung; pakai Botol."}
    assert (await _h(Kon(), P_MINYAK, "Karton", 1, "tolak", "Retur"))["blok"]["message"] == "Retur dengan satuan Karton belum didukung; pakai Botol."


@pytest.mark.asyncio
async def test_penulis_melempar_galat_berkode_sama_dengan_blok():
    with pytest.raises(HTTPException) as e:
        await SD.qty_dasar(Kon(konversi=[]), T, P_MINYAK, "Karton", 1, "konversi")
    assert e.value.status_code == 400 and e.value.code == "SATUAN_TAK_TERKONVERSI" and "belum punya konversi" in e.value.detail
    r = await SD.qty_dasar(Kon(), T, P_MINYAK, "Karton", 5, "konversi")
    assert r["qty_dasar"] == Decimal("60") and r["faktor"] == Decimal("12")


@pytest.mark.asyncio
async def test_blok_baris_unik_urutan_baris_dan_hanya_baca():
    k = Kon(konversi=[])
    b = await SD.blok_baris(k, T, [{"product_id": P_MINYAK, "unit": "Karton", "quantity": 1}, {"product_id": P_MINYAK, "unit": "Karton", "quantity": 2},
                                   {"product_id": P_LAIN, "unit": "kg", "quantity": 1}], "konversi")
    assert [x["code"] for x in b] == ["SATUAN_TAK_TERKONVERSI"]
    assert not any(q.split(" ", 1)[0].upper() in ("INSERT", "UPDATE", "DELETE") or "FOR UPDATE" in q for q in k.sql)


def test_biaya_dasar_presisi_penuh():
    assert SD.biaya_dasar(Decimal("120000"), Decimal("24")) == Decimal("5000")
    assert SD.biaya_dasar(Decimal("100"), Decimal("3")) == Decimal("100") / Decimal("3")
    assert SD.biaya_dasar(5, 0) == Decimal("0")


# --------------------------------------------------------------------------- pemasangan di jalur stok (pemindai AST)
def _fungsi(rel, nama):
    src = (APP / rel).read_text()
    fn = [n for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nama]
    assert len(fn) == 1, (rel, nama)
    return fn[0]


def _panggilan_penentu(fn) -> list:
    """Mode (konstanta string 'konversi'/'tolak') pada panggilan qty_dasar/_qty_dasar/hitung/blok_baris/blok_satuan_buat di fungsi itu."""
    out = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            nama = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", None)
            pemilik = getattr(n.func.value, "id", None) if isinstance(n.func, ast.Attribute) else None
            if nama == "hitung" and pemilik not in ("SD", "_SD"):  # TR.hitung (tagihan_rencana) dll. bukan penentu satuan
                continue
            if nama in ("qty_dasar", "_qty_dasar", "hitung", "blok_baris"):
                mode = [a.value for a in n.args if isinstance(a, ast.Constant) and a.value in ("konversi", "tolak")]
                out.append((nama, mode[0] if mode else None))
            elif nama == "blok_satuan_buat":
                out.append((nama, "konversi"))
    return out


JALUR = [
    # MASUK (konversi)
    ("services/grn_rencana.py", "rencana_grn", "konversi"),
    ("services/bills_service.py", "post_bill", "konversi"),
    ("services/bills_service.py", "create_bill_v2", "konversi"),
    ("services/bills_service.py", "blok_satuan_buat", "konversi"),
    ("services/tagihan_aksi.py", "blok_posting", "konversi"),
    ("routers/bills.py", "calculate_bill_totals", "konversi"),
    ("routers/credit_notes.py", "posting_nota_kredit", "konversi"),
    ("services/nota_kredit_rencana.py", "_rencana_restock_nk", "konversi"),
    # KELUAR (tolak sampai R2) + jalur lama tanpa helper WAC
    ("routers/sales_invoices.py", "_execute_fulfillment", "tolak"),
    ("services/faktur_terbit_rencana.py", "rencana_terbit_ro", "tolak"),
    ("services/sj_so.py", "rencana", "tolak"),
    ("routers/sales_receipts.py", "create_sales_receipt", "tolak"),
    ("services/nota_debit_gudang.py", "rencana_keluar", "tolak"),
    ("services/kernel_document_executor.py", "_create_inventory_movements", "tolak"),
    ("routers/transactions.py", "_create_pos_inventory_and_journals", "tolak"),
    ("routers/production.py", "issue_materials", "tolak"),
    ("routers/production.py", "report_output", "tolak"),
]


@pytest.mark.parametrize("rel,nama,mode", JALUR)
def test_tiap_jalur_stok_memanggil_penentu_dengan_mode_benar(rel, nama, mode):
    p = _panggilan_penentu(_fungsi(rel, nama))
    assert p, f"{rel}:{nama} tak memanggil penentu satuan_dasar"
    assert all(m == mode for _, m in p), (rel, nama, p)


def test_tak_ada_lagi_konversi_satu_banding_satu_diam_di_jalur_stok():
    """unit_helpers.convert_to_base_unit (tanpa konversi -> 1:1 + log) tak boleh dipakai jalur stok mana pun."""
    for f in APP.rglob("*.py"):
        if f.name in ("unit_helpers.py",):
            continue
        pohon = ast.parse(f.read_text())
        for n in ast.walk(pohon):
            if isinstance(n, (ast.ImportFrom, ast.Import)):
                nama = [a.name for a in n.names]
                assert not (getattr(n, "module", "") or "").endswith("unit_helpers") and "convert_to_base_unit" not in nama, f


def test_grn_menulis_kartu_stok_qty_kali_faktor_dan_biaya_dibagi_faktor():
    fn = _fungsi("services/grn_rencana.py", "tulis_grn")
    pang = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "record_inventory_inbound"]
    assert len(pang) == 1
    args = [ast.unparse(a) for a in pang[0].args]
    assert "p['qty_stok'] * f" in args and "b['harga_satuan'] / f" in args


def test_fpb_langsung_menulis_qty_dasar_dan_biaya_per_dasar():
    src = ast.unparse(_fungsi("services/bills_service.py", "create_bill_v2"))
    assert "quantity = _qdv2['qty_dasar']" in src and "SD.biaya_dasar(total_cost, quantity)" in src


class KonAsal:
    """asal_retur ND: kueri tautan GRN harus menjumlah dalam SATUAN DASAR (rasio kartu GRN), sebanding dengan kartu BILL & qty ND."""
    def __init__(self):
        self.sql = []

    async def fetch(self, sql, *a):
        q = " ".join(sql.split())
        self.sql.append(q)
        if "goods_receipt_bill_links" in q:  # 2 karton x rasio kartu (24 botol / 2 karton = 12) dihitung DB -> di sini nilai jadi
            return [{"warehouse_id": "G1", "q": Decimal("24")}]
        return []

    async def fetchval(self, sql, *a):
        return 0


@pytest.mark.asyncio
async def test_asal_retur_nd_tautan_grn_dalam_satuan_dasar_lewat_rasio_kartu():
    from app.services import nota_debit_gudang as NG
    k = KonAsal()
    r = await NG.asal_retur(k, T, uuid.uuid4(), P_MINYAK, None)
    assert r["masuk"] == Decimal("24") and r["sisa"] == Decimal("24")
    q = next(x for x in k.sql if "goods_receipt_bill_links" in x)
    assert "SUM(l.quantity * COALESCE(il.quantity_in / NULLIF(gi.quantity, 0), 1))" in q
    assert "LEFT JOIN inventory_ledger il ON il.id = gi.inventory_ledger_id" in q


class KonND:
    """rencana_keluar ND tanpa tagihan asal: gudang bawaan + baris barang ber-stok bersatuan Karton (berkonversi)."""
    def __init__(self):
        self.galat = []

    async def fetchval(self, sql, *a):
        if "FROM warehouses" in sql:
            return "G1"
        return 0

    async def fetchrow(self, sql, *a):
        if "FROM products" in sql:
            return {"id": P_MINYAK, "item_code": "MG1", "nama_produk": "Minyak Goreng 1L", "track_inventory": True}
        return None

    async def fetch(self, sql, *a):
        return await Kon().fetch(sql, *a)


class Bl:
    def __init__(self):
        self.isi = []

    def galat(self, code, status, message):
        self.isi.append((code, status, message))


@pytest.mark.asyncio
async def test_nd_karton_ditolak_belum_didukung_walau_berkonversi_dan_tak_ikut_keluar():
    from app.services import nota_debit_gudang as NG
    bl = Bl()
    k = await NG.rencana_keluar(KonND(), T, {"original_bill_id": None, "id": None}, [{"item_id": P_MINYAK, "quantity": 1, "unit": "Karton"}], False, bl)
    assert k == [] and bl.isi == [("SATUAN_BELUM_DIDUKUNG", 400, "Retur dengan satuan Karton belum didukung; pakai Botol.")]
    bl2 = Bl()
    k2 = await NG.rencana_keluar(KonND(), T, {"original_bill_id": None, "id": None}, [{"item_id": P_MINYAK, "quantity": 1, "unit": "botol"}], False, bl2)
    assert not any(c == "SATUAN_BELUM_DIDUKUNG" for c, _, _ in bl2.isi) and len(k2) == 1
