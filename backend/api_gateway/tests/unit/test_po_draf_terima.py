"""PO DRAF-TAGIHAN + bisa_diterima + batas_tagih batch (4 Nov 2026, desain + putusan MASTER).

- perlu_ditagih hanya bila masih ada yang bisa dibuatkan faktur SESUDAH draf; draf_tagihan bila sisanya sudah faktur DRAF (pill
  "Buka faktur draf", draft_bills). Status PO tetap dari bill POSTED (D1). PARTISI: satu baris = satu pill.
- bisa_diterima = SARINGAN daftar (bukan pill/partisi), SATU sumber status dengan penentu terima (po_rencana.status_bisa_terima).
- rencana_tagih cabang items: SATU batas_tagih untuk semua baris, urutan blok = urutan item.
Tiruan DB mencocokkan POLA (tabel + kolom kunci)."""
import ast
import uuid
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from app.services import grn_rencana as G
from app.services import po_daftar as PD
from app.services import po_rencana as PR

T = "kaos-biru-konveksi"
HARI = date(2026, 11, 4)
APP = Path(PD.__file__).resolve().parents[1]


@pytest.mark.parametrize("status,belum,sesudah,harap", [
    ("partial_received", "5", "0", "draf_tagihan"),     # PO-2611-0002: seluruh sisa sudah faktur draf -> "Buka faktur draf"
    ("partial_billed", "5", "2", "perlu_ditagih"),      # masih ada yang bisa dibuatkan faktur
    ("received", "5", None, "perlu_ditagih"),           # pemanggil lama tanpa medan baru = perilaku lama
    ("received", "0", "0", None),                       # sudah ditagih POSTED semua
    ("billed", "3", "0", None)])                        # status bukan bisa-tagih
def test_partisi_perlu_ditagih_dan_draf_tagihan(status, belum, sesudah, harap):
    assert PD.aksi_po(status, None, D("0"), D(belum), HARI, None if sesudah is None else D(sesudah)) == harap


def test_sql_aksi_partisi_saling_lepas_dan_saringan_bukan_pill():
    p, d = PD.sql_aksi("perlu_ditagih", "$2"), PD.sql_aksi("draf_tagihan", "$2")
    assert "ag.belum_ditagih_sesudah_draf > 0" in p and "ag.belum_ditagih_sesudah_draf <= 0" in d
    assert p.replace("> 0)", "") == d.replace("<= 0)", "")  # predikat dasar SAMA, hanya syarat sesudah-draf yang membelah
    assert "draf_tagihan" in PD.AKSI and "bisa_diterima" not in PD.AKSI and PD.SARINGAN == ("bisa_diterima",)


def test_bisa_diterima_satu_sumber_status_penentu_terima():
    d = PD.sql_aksi("bisa_diterima", "$2", status_terima=PR.status_bisa_terima(True))
    lama = PD.sql_aksi("bisa_diterima", "$2", status_terima=PR.status_bisa_terima(False))
    assert "'partial_billed'" in d and "'billed'" in d and "ag.sisa_terima > 0" in d  # tagih dulu (aturan D): tetap bisa diterima
    assert "'billed'" not in lama and "'sent'" in lama and "'partial_received'" in lama
    assert PR.status_bisa_terima(True) == PR.STATUS_BISA_TERIMA_D and PR.status_bisa_terima(False) == PR.STATUS_BISA_TERIMA
    src = ast.unparse(next(n for n in ast.walk(ast.parse((APP / "services/po_rencana.py").read_text()))
                           if isinstance(n, ast.AsyncFunctionDef) and n.name == "rencana_terima"))
    assert "status_bisa_terima(aturan_d)" in src and "STATUS_BISA_TERIMA_D if aturan_d" not in src  # penentu memakai sumber yang SAMA


class KonFakta:
    def __init__(self, po_id):
        self.po_id = po_id

    async def fetch(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM purchase_orders po" in q and "belum_ditagih_sesudah_draf" in q:
            return [{"id": self.po_id, "status": "partial_received", "expected_date": None, "total_amount": D("1000"), "sisa_terima": D("5"),
                     "belum_ditagih": D("5"), "belum_ditagih_sesudah_draf": D("0"), "amount_received": D("500"), "amount_billed": D("0"),
                     "draft_bill_amount": D("500")}]
        if "FROM bill_items bi JOIN bills b" in q and "= 'draft'" in q and "poi.po_id = ANY" in q:
            return [{"po_id": self.po_id, "id": uuid.UUID(int=7), "invoice_number": "BILL-2611-0004"}]
        raise AssertionError(q[:100])


@pytest.mark.asyncio
async def test_fakta_po_membawa_faktur_draf_dan_sisa_sesudah_draf():
    po = uuid.uuid4()
    f = (await PD.fakta_po(KonFakta(po), T, [po], HARI))[po]
    assert f["aksi"] == "draf_tagihan" and f["belum_ditagih_sesudah_draf"] == 0 and f["draft_bill_amount"] == D("500")
    assert await PD.faktur_draf_po(KonFakta(po), T, [po]) == {po: [{"id": str(uuid.UUID(int=7)), "bill_number": "BILL-2611-0004"}]}
    assert await PD.faktur_draf_po(KonFakta(po), T, []) == {}  # tanpa PO = tanpa kueri
    assert "q.pagar" in PD.SQL_AGREGAT and "i.quantity_billed" not in PD.SQL_AGREGAT  # tampilan dari bill bertaut, bukan cache


class KonTagih:
    def __init__(self, baris):
        self.baris = baris

    async def fetchrow(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM purchase_order_items WHERE id = $1 AND po_id = $2" in q:
            return next((b for b in self.baris if b["id"] == a[0]), None)
        raise AssertionError(q[:100])

    async def fetchval(self, sql, *a):
        return None  # kode pajak masukan tarif 0


def _poi(i):
    return {"id": i, "quantity": D("10"), "quantity_billed": D("0"), "unit_price": D("1000"), "discount_percent": D("0"), "discount_amount": D("0"),
            "tax_rate": D("0"), "item_id": uuid.uuid4(), "item_code": "X", "description": "Minyak", "unit": "Dus"}


@pytest.mark.asyncio
async def test_to_bill_items_satu_batas_tagih_dan_urutan_blok_tetap(monkeypatch):
    a, b = uuid.uuid4(), uuid.uuid4()
    panggil = []

    async def batas(conn, tid, ids):
        panggil.append(list(ids))
        return {str(i): {"dipesan": D("10"), "diterima": D("0"), "ada_grn": False, "batas": D("10"), "dasar": "dipesan",
                         "qty_dasar": D("10"), "sudah_ditagih": D("0")} for i in ids}
    monkeypatch.setattr(G, "batas_tagih", batas)
    po = {"id": uuid.uuid4(), "tenant_id": T, "status": "sent", "tax_rate": D("0")}
    items = [{"po_item_id": "bukan-uuid"}, {"po_item_id": str(a), "quantity_to_bill": D("11")}, {"po_item_id": str(uuid.uuid4())},
             {"po_item_id": str(b), "quantity_to_bill": D("4")}]
    r = await PR.rencana_tagih(KonTagih([_poi(a), _poi(b)]), po, items, kunci=False)
    assert panggil == [[a, b]]  # SATU kueri batas untuk semua baris yang dikenali
    assert [x["code"] for x in r["blocks"]] == ["BARIS_TIDAK_DITEMUKAN", "QTY_MELEBIHI_SISA_TAGIH", "BARIS_TIDAK_DITEMUKAN"]  # urutan item
    assert [ln["qty"] for ln in r["lines"]] == [D("4")]


def test_rute_daftar_menerima_kunci_baru_dan_saringan_memakai_flag():
    s = (APP / "routers/purchase_orders.py").read_text()
    assert '"draf_tagihan", "bisa_diterima"]] = Query(None)' in s
    assert "PR.status_bisa_terima(await G.aktif(conn, ctx[\"tenant_id\"]))" in s
    assert s.count("await PD.faktur_draf_po(conn, ctx[\"tenant_id\"]") == 2  # daftar + detail (fungsi SAMA)
