"""BUG-275: Tinjau tambah/ubah SATU baris harga memberi `rencana` (dari/ke/selisih_harga) dari penentu server -- FE tak menghitung. Kunci lama (ok, blocks, payload) tak berubah."""
import asyncio
import inspect
from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.routers import price_lists as PL
from app.schemas.price_lists import PriceListItemCreate
from app.services import harga_riwayat as HR

PID, LINE = uuid4(), uuid4()


class Konn:
    def __init__(self, lama=None):
        self.lama, self.sql = lama, []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM products WHERE id" in sql:
            return {"nama_produk": "Minyak", "kode": "A-1", "dasar": "Pcs"}
        if "FROM price_list_items pli" in sql:
            return self.lama
        raise AssertionError(sql)


def lama_db(harga=10000):
    return {"id": LINE, "item_id": PID, "kode": "A-1", "nama": "Minyak", "unit": None, "dasar": "Pcs", "min_quantity": Decimal("1"), "price": Decimal(harga),
            "min_price": Decimal("9000"), "discount_percent": Decimal("5"), "start_date": None, "end_date": date(2026, 12, 31)}


def item(**kw):
    return PriceListItemCreate(item_id=str(PID), price=kw.pop("price", 11000), min_quantity=kw.pop("min_quantity", 1), **kw)


def rencana(c, it, line=None):
    return asyncio.run(HR.rencana_baris(c, "t-1", it, line))


def test_tambah_dari_null_ke_berisi_selisih_null_dan_teks_barang_dari_server():
    r = rencana(Konn(), item(price=12500, min_quantity=10, min_price=9000, discount_percent=7.5, start_date=date(2026, 11, 20)))
    assert r["dari"] is None and r["selisih_harga"] is None and r["barang"] == "Minyak" and r["item_code"] == "A-1" and r["unit_text"] == "Pcs" and r["min_quantity"] == 10.0
    assert r["ke"] == {"harga": 12500.0, "min_quantity": 10.0, "min_price": 9000.0, "diskon_persen": 7.5, "mulai": "2026-11-20", "berakhir": None}


def test_ubah_dari_baris_tersimpan_ke_badan_gabungan_dan_selisih():
    r = rencana(Konn(lama_db(10000)), item(price=11500, min_price=9000, discount_percent=5, end_date=date(2026, 12, 31)), LINE)
    assert r["dari"] == {"harga": 10000.0, "min_quantity": 1.0, "min_price": 9000.0, "diskon_persen": 5.0, "mulai": None, "berakhir": "2026-12-31"}
    assert r["ke"]["harga"] == 11500.0 and r["selisih_harga"] == 1500.0
    assert rencana(Konn(lama_db(10000)), item(price=9000), LINE)["selisih_harga"] == -1000.0                       # turun = negatif
    assert rencana(Konn(lama_db(10000)), item(price=10000), LINE)["selisih_harga"] == 0.0


def test_satuan_tertulis_dipakai_unit_text_dan_kode_dari_badan_menang():
    r = rencana(Konn(), item(unit=" Dus  besar ", item_code="KODE-X"))
    assert r["unit_text"] == "Dus besar" and r["item_code"] == "KODE-X"


def test_pratinjau_menambah_rencana_hanya_bila_tanpa_blok_dan_kunci_lama_utuh():
    s = inspect.getsource(PL._pratinjau_baris)
    assert 'HR.rencana_baris(conn, ctx["tenant_id"], item, line_id)' in s
    assert s.index("siapkan_baris_harga(") < s.index("HR.rencana_baris(")                                         # rencana hanya sesudah penentu lolos (blok = tanpa rencana)
    assert '{"ok": not bloks, "blocks": bloks, "payload": payload, **({"rencana": rencana} if rencana is not None else {})}' in s
    assert '"data": {"ok": False, "blocks": blok_validasi(e), "payload": payload}' in s                            # galat validasi skema: tanpa rencana
    assert "tr.rollback()" in s and "readonly=True" in s                                                            # tetap READ ONLY
