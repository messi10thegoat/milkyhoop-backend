"""TERIMA-TABEL (4 Nov 2026; kontrak WORKSPACE kontrak-be-terima-tabel.md, desain + putusan MASTER).

Penentu TUNGGAL pratinjau == penulis: po_rencana.rencana_terima (ditolak, toleransi lebih-terima, blok berkalimat), grn_rencana.batas_tagih
(MAX(dipesan, diterima GRN aktif) -- satu sumber to-bill + posting tagihan), rencana_tagih 3-ARAH KETAT, kode pajak masukan (5).
Tiruan DB mencocokkan POLA (tabel + kolom kunci), bukan teks SQL utuh (pelajaran MASTER 3 Nov)."""
import ast
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.services import grn_rencana as G
from app.services import po_rencana as PR
from app.services import tagihan_aksi as TA

T = "kaos-biru-konveksi"
PO_ID, A, B = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
HARI = date(2026, 11, 4)
APP = Path(PR.__file__).resolve().parents[1]
D = Decimal


def _po(status="sent"):
    return {"id": PO_ID, "tenant_id": T, "status": status, "po_date": date(2026, 11, 1), "tax_rate": D("0"), "po_number": "PO-1"}


class Kon:
    def __init__(self, toleransi=None, grn=True, baris=None):
        self.toleransi, self.grn = toleransi, grn
        self.baris = baris or [
            {"id": A, "description": "Minyak", "quantity": D("100"), "jasa": False, "quantity_received": D("0"), "quantity_billed": D("0"), "unit": "Dus",
             "total": D("1000000")},
            {"id": B, "description": "Gula", "quantity": D("10"), "jasa": False, "quantity_received": D("4"), "quantity_billed": D("0"), "unit": "Kg",
             "total": D("100000")}]

    async def fetchval(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM purchase_settings" in q and "penerimaan_toleransi_persen" in q:
            return self.toleransi
        if "FROM tenant_features" in q:
            return 1 if self.grn else None
        if "FROM tax_codes" in q and "direction = 'input'" in q:
            return {D("11"): "kode-11-in"}.get(D(str(a[1])))
        raise AssertionError(q[:100])

    async def fetch(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM purchase_order_items" in q and "po_id = $1" in q:
            return self.baris
        raise AssertionError(q[:100])


async def _terima(kon, items, status="sent"):
    return await PR.rencana_terima(kon, _po(status), HARI, items, HARI, kunci=False)


def _kode(r):
    return [b["code"] for b in r["blocks"]]


# ---------------------------------------------------------------- terima
@pytest.mark.asyncio
async def test_toleransi_nol_blok_lama_persis():
    r = await _terima(Kon(), [{"po_item_id": str(A), "quantity_received": D("101")}])
    assert [(b["code"], b["message"]) for b in r["blocks"]] == [("QTY_MELEBIHI_SISA", "Jumlah diterima (101) melebihi sisa (100).")]
    assert r["toleransi_persen"] == 0 and r["notes"] == []


@pytest.mark.asyncio
async def test_toleransi_lima_persen_lebih_dalam_batas_catatan_di_luar_batas_blok():
    r = await _terima(Kon(toleransi=D("5")), [{"po_item_id": str(A), "quantity_received": D("105")}])
    a = next(b for b in r["baris"] if b["po_item_id"] == str(A))
    assert r["blocks"] == [] and [n["code"] for n in r["notes"]] == ["RECEIPT_LEBIH"]
    assert (a["akan_diterima"], a["lebih"], a["sisa_sesudah"], r["status_sesudah"]) == (D("105"), D("5"), D("0"), "partial_received")
    r2 = await _terima(Kon(toleransi=D("5")), [{"po_item_id": str(A), "quantity_received": D("106")}])
    assert _kode(r2) == ["QTY_MELEBIHI_TOLERANSI"] and "toleransi 5" in r2["blocks"][0]["message"] and "Minyak" in r2["blocks"][0]["message"]


@pytest.mark.asyncio
async def test_ditolak_dicatat_tak_mengurangi_sisa_dan_ikut_payload():
    r = await _terima(Kon(), [{"po_item_id": str(A), "quantity_received": D("90"), "quantity_rejected": D("10"), "reject_reason": " pecah "}])
    a = next(b for b in r["baris"] if b["po_item_id"] == str(A))
    assert r["blocks"] == []
    assert (a["akan_diterima"], a["akan_ditolak"], a["sisa_sesudah"]) == (D("90"), D("10"), D("10"))  # ditolak masih ditunggu
    assert r["payload"]["items"] == [{"po_item_id": str(A), "quantity_received": "90", "quantity_rejected": "10", "reject_reason": "pecah"}]
    assert r["nilai_diterima"] == D("900000.00")  # yang diterima saja


@pytest.mark.asyncio
async def test_ditolak_melebihi_yang_datang_negatif_dan_kosong_diblok():
    r = await _terima(Kon(), [{"po_item_id": str(A), "quantity_received": D("95"), "quantity_rejected": D("10")}])
    assert _kode(r) == ["QTY_DITOLAK_MELEBIHI"] and r["blocks"][0]["message"] == "Jumlah ditolak Minyak (10) melebihi yang datang."
    assert _kode(await _terima(Kon(), [{"po_item_id": str(A), "quantity_received": D("-1")}])) == ["QTY_NEGATIF"]
    r0 = await _terima(Kon(), [{"po_item_id": str(A), "quantity_received": D("0")}, {"po_item_id": str(B), "quantity_received": D("0")}])
    assert [(b["code"], b["message"]) for b in r0["blocks"]] == [("BARIS_KOSONG", "Isi jumlah diterima atau ditolak minimal satu barang.")]


@pytest.mark.asyncio
async def test_hanya_ditolak_sah_dan_tanpa_fitur_penerimaan_diblok():
    r = await _terima(Kon(), [{"po_item_id": str(B), "quantity_received": D("0"), "quantity_rejected": D("2")}])
    assert r["blocks"] == [] and r["payload"]["items"] == [{"po_item_id": str(B), "quantity_received": "0", "quantity_rejected": "2"}]
    assert r["status_sesudah"] == "partial_received" and r["nilai_diterima"] == 0
    r2 = await _terima(Kon(grn=False), [{"po_item_id": str(B), "quantity_received": D("1"), "quantity_rejected": D("1")}])
    assert _kode(r2) == ["DITOLAK_BUTUH_PENERIMAAN"]


@pytest.mark.asyncio
async def test_tanpa_ditolak_dan_toleransi_payload_bentuk_lama():
    r = await _terima(Kon(), [{"po_item_id": str(A), "quantity_received": D("40")}])
    assert r["payload"]["items"] == [{"po_item_id": str(A), "quantity_received": "40"}]


# ---------------------------------------------------------------- tagih: batas SATU sumber
def _bt(dipesan, diterima, ada, ditagih=D("0")):
    batas = max(dipesan, diterima) if ada else dipesan
    return {"dipesan": dipesan, "diterima": diterima, "ada_grn": ada, "batas": batas, "dasar": "diterima" if ada else "dipesan",
            "qty_dasar": max((diterima if ada else dipesan) - ditagih, D("0")), "sudah_ditagih": ditagih}


class KonTagih:
    def __init__(self, baris):
        self.baris = baris

    async def fetchrow(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM purchase_order_items WHERE id = $1 AND po_id = $2" in q:
            return next((b for b in self.baris if b["id"] == a[0]), None)
        raise AssertionError(q[:100])

    async def fetch(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM purchase_order_items WHERE po_id = $1" in q:
            return self.baris
        raise AssertionError(q[:100])

    async def fetchval(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM tax_codes" in q:
            return {D("11"): "kode-11-in"}.get(D(str(a[1])))
        raise AssertionError(q[:100])


def _poi(i, qty, tarif="0"):
    return {"id": i, "quantity": D(qty), "quantity_billed": D("0"), "unit_price": D("10000"), "discount_percent": D("0"),
            "discount_amount": D("0"), "tax_rate": D(tarif), "item_id": uuid.uuid4(), "item_code": "X", "description": "Minyak", "unit": "Dus"}


@pytest.mark.asyncio
async def test_tagih_tiga_arah_ketat_dengan_grn_dan_perilaku_lama_tanpa_grn(monkeypatch):
    kon = KonTagih([_poi(A, "100")])

    async def dengan_grn(conn, tid, ids): return {str(A): _bt(D("100"), D("60"), True)}
    monkeypatch.setattr(G, "batas_tagih", dengan_grn)
    r = await PR.rencana_tagih(kon, _po("partial_received"), None, kunci=False)
    assert [(ln["qty"], ln["batas"]["dasar"]) for ln in r["lines"]] == [(D("60"), "diterima")]
    r = await PR.rencana_tagih(kon, _po("partial_received"), [{"po_item_id": str(A), "quantity_to_bill": D("61")}], kunci=False)
    assert [(b["code"], b["message"]) for b in r["blocks"]][0] == (
        "QTY_MELEBIHI_SISA_TAGIH", "Jumlah ditagih (61) melebihi yang diterima belum ditagih (60).")

    async def tanpa_grn(conn, tid, ids): return {str(A): _bt(D("100"), D("0"), False)}
    monkeypatch.setattr(G, "batas_tagih", tanpa_grn)
    r = await PR.rencana_tagih(kon, _po("sent"), [{"po_item_id": str(A), "quantity_to_bill": D("101")}], kunci=False)
    assert r["blocks"][0]["message"] == "Jumlah ditagih (101) melebihi sisa belum tertagih (100)."  # teks lama persis


@pytest.mark.asyncio
async def test_lebih_terima_dalam_toleransi_bisa_ditagih_penuh(monkeypatch):
    kon = KonTagih([_poi(A, "100")])

    async def bt(conn, tid, ids): return {str(A): _bt(D("100"), D("105"), True)}
    monkeypatch.setattr(G, "batas_tagih", bt)
    r = await PR.rencana_tagih(kon, _po("received"), None, kunci=False)
    assert [ln["qty"] for ln in r["lines"]] == [D("105")] and r["bill_total"] == 1050000.0


class KonPagar:
    def __init__(self, tertagih):
        self.tertagih = tertagih

    async def fetch(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM purchase_order_items poi JOIN purchase_orders po" in q and "tertagih" in q:
            return [{"id": A, "quantity": D("100"), "unit": "Dus", "po_number": "PO-1", "tertagih": self.tertagih}]
        raise AssertionError(q[:100])


@pytest.mark.asyncio
@pytest.mark.parametrize("ini,diterima,ada,harap", [
    (D("105"), D("105"), True, None),                    # MASTER: terima 105 atas 100 (toleransi 5%) -> tagih 105 boleh
    (D("106"), D("105"), True, "diterima 105"),          # tagih 106 -> blok menyebut dasar diterima
    (D("101"), D("0"), False, "dipesan 100"),            # tanpa GRN: teks lama (dipesan)
    (D("101"), D("90"), True, "dipesan 100")])           # kurang-terima: batas tetap dipesan (MAX)
async def test_pagar_posting_tagihan_batas_max_dipesan_diterima(monkeypatch, ini, diterima, ada, harap):
    async def bt(conn, tid, ids): return {str(A): _bt(D("100"), diterima, ada)}
    monkeypatch.setattr(G, "batas_tagih", bt)
    out = await TA._blok_qty_po(KonPagar(D("0")), T, uuid.uuid4(), [{"purchase_order_item_id": A, "qty_dasar": ini}])
    assert (harap is None and out == []) or (len(out) == 1 and out[0]["code"] == "PO_QTY_TERLAMPAUI" and harap in out[0]["message"])


@pytest.mark.asyncio
async def test_pajak_masukan_dari_tarif_baris_header_nol_dan_tarif_header_po(monkeypatch):
    async def bt(conn, tid, ids): return {str(i): _bt(D("10"), D("0"), False) for i in ids}
    monkeypatch.setattr(G, "batas_tagih", bt)

    async def faktor(*a, **k): return (1, 1)
    monkeypatch.setattr("app.services.bills_service.resolve_dpp_factor", faktor)  # BUG-088: pajak baris lewat _bill_line_tax (faktor kode)
    r = await PR.rencana_tagih(KonTagih([_poi(A, "10", "11")]), _po(), None, kunci=False)
    assert (r["lines"][0]["tax_code_id"], r["tax_rate"], r["tax_code_id"], r["bill_tax"]) == ("kode-11-in", 0.0, None, 11000.0)

    monkeypatch.setattr("app.services.tax_factor.resolve_dpp_factor", faktor)
    po = {**_po(), "tax_rate": D("11")}
    r = await PR.rencana_tagih(KonTagih([_poi(A, "10")]), po, None, kunci=False)
    assert (r["lines"][0]["tax_code_id"], r["tax_rate"], r["tax_code_id"], r["bill_tax"], r["bill_total"]) == (None, 11.0, "kode-11-in", 11000.0, 111000.0)
    assert await PR.kode_pajak_masukan(None, T, 0) is None


# ---------------------------------------------------------------- penulis = penentu (AST)
def _fungsi(rel, nama):
    src = (APP / rel).read_text()
    return next(n for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nama)


def test_penulis_terima_memakai_qty_penentu_tanpa_cek_sisa_dobel():
    s = ast.unparse(_fungsi("routers/purchase_orders.py", "receive_goods"))
    assert "_rt['akan'].items()" in s and "melebihi sisa" not in s and "for recv_item in body.items" not in s


def test_ditolak_hanya_di_baris_grn_bukan_stok():
    t = ast.unparse(_fungsi("services/grn_rencana.py", "tulis_grn"))
    assert "quantity_rejected" in t and "reject_reason" in t
    r = ast.unparse(_fungsi("services/grn_rencana.py", "rencana_grn"))
    assert "b['ber_stok'] and b['qty'] > NOL" in r  # baris hanya-ditolak tak masuk kartu stok / jurnal


def test_pagar_tagih_dan_to_bill_satu_sumber_batas():
    assert "batas_tagih" in ast.unparse(_fungsi("services/tagihan_aksi.py", "_blok_qty_po"))
    assert "G.batas_tagih" in ast.unparse(_fungsi("services/po_rencana.py", "rencana_tagih"))


def test_penentu_satu_satunya_penjaga_ditolak_sebelum_tulis():
    # cek sisa dobel di penulis dihapus -> blok penentu WAJIB ditolak (400) SEBELUM penghitung diterima/GRN ditulis
    s = ast.unparse(_fungsi("routers/purchase_orders.py", "receive_goods"))
    import re
    i_tolak = s.index("_tolak(_rt['blocks'])")
    i_tulis = re.search(r"\bSET\s+quantity_received\b", s).start()  # tahan bentuk SQL (satu / multi-baris)
    assert i_tolak < i_tulis and s.index("_tolak(_grn['blocks'])") < i_tulis and s.index("G.tulis_grn(") > i_tulis
    assert "kunci" not in s.split("PR.rencana_terima(", 1)[1].split(")", 1)[0]  # bawaan kunci=True: baris PO FOR UPDATE di penentu


class KonBatas:
    def __init__(self, diterima, ada, ditagih="0"):
        self.r = {"id": A, "quantity": D("100"), "ditagih": D(ditagih), "diterima": D(diterima), "ada_grn": ada, "jasa": False}

    async def fetch(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM purchase_order_items poi" in q and "goods_receipt_items" in q and "g.status <> 'void'" in q:
            return [self.r]
        raise AssertionError(q[:100])


@pytest.mark.asyncio
@pytest.mark.parametrize("diterima,ada,ditagih,batas,dasar,qty_dasar", [
    ("105", True, "0", "105", "diterima", "105"),   # lebih-terima: batas = diterima
    ("60", True, "20", "100", "diterima", "40"),    # kurang-terima: batas = dipesan (MAX), 3-arah = diterima - ditagih
    ("0", False, "30", "100", "dipesan", "70")])     # tanpa GRN: perilaku lama
async def test_batas_tagih_satu_sumber(diterima, ada, ditagih, batas, dasar, qty_dasar):
    b = (await G.batas_tagih(KonBatas(diterima, ada, ditagih), T, [A]))[str(A)]
    assert (b["batas"], b["dasar"], b["qty_dasar"]) == (D(batas), dasar, D(qty_dasar))


@pytest.mark.asyncio
async def test_baris_nol_nol_tak_pernah_sampai_db():
    # V477 melonggarkan CHECK (quantity >= 0 AND (quantity > 0 OR quantity_rejected > 0)); baris 0/0 dibuang penentu SEBELUM DB,
    # semua baris 0/0 = blok berkalimat BARIS_KOSONG (bukan 500 CheckViolation)
    r = await _terima(Kon(), [{"po_item_id": str(A), "quantity_received": D("0")}, {"po_item_id": str(B), "quantity_received": D("1")}])
    assert r["blocks"] == [] and [i["po_item_id"] for i in r["payload"]["items"]] == [str(B)]
    g = {k: v for k, v in r["akan"].items() if v > 0 or r["tolak"].get(k, 0) > 0}
    assert list(g) == [str(B)]


def test_kolom_baru_pratinjau_float_2dp_seragam_kolom_lama():
    # MASTER 4 Nov: pemetaan router dipertahankan -> nilai 4dp tak bulat WAJIB jadi float 2dp (merah bila pemetaan dicabut)
    from app.routers import purchase_orders as RPO
    b = {"po_item_id": "x", "description": "Minyak", "unit": "Dus", "qty": D("10.12345"), "sudah_diterima": D("0"), "sisa": D("10.12345"),
         "akan_diterima": D("11.35801"), "akan_ditolak": D("1.23456"), "sisa_sesudah": D("0.00001"), "lebih": D("1.23456"),
         "toleransi_persen": D("12.5")}
    j = RPO._baris_terima_json(b)
    assert {k: (type(j[k]), j[k]) for k in ("akan_ditolak", "sisa_sesudah", "lebih", "toleransi_persen", "qty")} == {
        "akan_ditolak": (float, 1.23), "sisa_sesudah": (float, 0.0), "lebih": (float, 1.23), "toleransi_persen": (float, 12.5), "qty": (float, 10.12)}


# hash BEKU dari bentuk model master (ReceiveGoodsRequest sebelum unit ini: receive_date, items[{po_item_id, quantity_received}], notes,
# warehouse_id, vendor_delivery_note) -- dibekukan, BUKAN dihitung ulang. Kesetaraan dengan kode master sungguhan dibuktikan SETARA s1
# (INSERT idempotency_keys dasar == cabang).
HASH_MASTER_TERIMA = "81ba0d01355533efac944bd09341150ae5acd8d26bd264b62dfa752393a0b14f"
PO_ITEM_BEKU = "00000000-0000-0000-0000-0000000000a1"


def test_sidik_idempotensi_terima_bentuk_lama_tak_berubah():
    from app.routers import purchase_orders as RPO
    from app.schemas.purchase_orders import ReceiveGoodsRequest as RGR
    from app.utils.idempotency import hash_payload
    lama = RGR(receive_date=HARI, items=[{"po_item_id": PO_ITEM_BEKU, "quantity_received": D("3")}])
    assert hash_payload(RPO._sidik_terima(lama)) == HASH_MASTER_TERIMA
    tolak = RGR(receive_date=HARI, items=[{"po_item_id": PO_ITEM_BEKU, "quantity_received": D("3"), "quantity_rejected": D("2")}])
    assert hash_payload(RPO._sidik_terima(tolak)) != HASH_MASTER_TERIMA
    alasan = RGR(receive_date=HARI, items=[{"po_item_id": PO_ITEM_BEKU, "quantity_received": D("3"), "reject_reason": "pecah"}])
    assert hash_payload(RPO._sidik_terima(alasan)) != HASH_MASTER_TERIMA
    assert "lambda: _sidik_terima(body)" in ast.unparse(_fungsi("routers/purchase_orders.py", "receive_goods"))  # rute memakai sidik ini


@pytest.mark.asyncio
async def test_diterima_absen_default_sisa_dikurangi_ditolak():
    # putusan MASTER (A): "vendor bawa 90, 5 rusak" (sisa 90) -> diterima 85 + ditolak 5; sisa 5 masih ditunggu; payload = terselesaikan
    baris = [{"id": A, "description": "Minyak", "quantity": D("90"), "jasa": False, "quantity_received": D("0"), "quantity_billed": D("0"), "unit": "Dus",
              "total": D("900000")}]
    r = await _terima(Kon(baris=baris), [{"po_item_id": str(A), "quantity_rejected": D("5")}])
    a = r["baris"][0]
    assert r["blocks"] == [] and (a["akan_diterima"], a["akan_ditolak"], a["sisa_sesudah"]) == (D("85"), D("5"), D("5"))
    assert r["payload"]["items"] == [{"po_item_id": str(A), "quantity_received": "85", "quantity_rejected": "5"}]
    r2 = await _terima(Kon(baris=baris), [{"po_item_id": str(A), "quantity_received": D("90"), "quantity_rejected": D("5")}])
    assert [b["code"] for b in r2["blocks"]] == ["QTY_DITOLAK_MELEBIHI"]  # eksplisit 90 + 5 > 90 tetap diblok
    r3 = await _terima(Kon(baris=baris), [{"po_item_id": str(A), "quantity_rejected": D("95")}])
    assert [b["code"] for b in r3["blocks"]] == ["QTY_DITOLAK_MELEBIHI"] and r3["baris"][0]["akan_diterima"] == 0  # default tak negatif


@pytest.mark.asyncio
async def test_kalimat_terima_tanpa_decimal_mentah():
    # putusan MASTER 4 Nov (temuan kontrak nyata WORKSPACE): "1.0000"/"10.00%" -> teks_galat.qty
    baris = [{"id": A, "description": "Minyak", "quantity": D("10.0000"), "jasa": False, "quantity_received": D("0.0000"), "quantity_billed": D("0"),
              "unit": "Dus", "total": D("100000")}]
    r = await _terima(Kon(toleransi=D("10.00"), baris=baris), [{"po_item_id": str(A), "quantity_received": D("11.0000")}])
    assert [n["message"] for n in r["notes"]] == ["Diterima 1 lebih dari pesanan (dalam toleransi 10%)."]
    r = await _terima(Kon(toleransi=D("10.00"), baris=baris), [{"po_item_id": str(A), "quantity_received": D("12.0000")}])
    assert r["blocks"][0]["message"] == "Jumlah diterima Minyak (12) melebihi sisa (10) lebih dari toleransi 10%."
    r = await _terima(Kon(baris=baris), [{"po_item_id": str(A), "quantity_received": D("10.5000")}])
    assert r["blocks"][0]["message"] == "Jumlah diterima (10,5) melebihi sisa (10)."
    r = await _terima(Kon(baris=baris), [{"po_item_id": str(A), "quantity_received": D("8.0000"), "quantity_rejected": D("3.0000")}])
    assert r["blocks"][0]["message"] == "Jumlah ditolak Minyak (3) melebihi yang datang."
