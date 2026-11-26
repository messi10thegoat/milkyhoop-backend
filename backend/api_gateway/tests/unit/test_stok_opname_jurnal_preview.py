"""Stok Opname: jurnal_preview (akun, debit_text, kredit_text) di preview Post/Void/Batal (permintaan MASTER/FRONTEND 3; Accurate menampilkan jurnal)."""
import asyncio
import inspect
import uuid
from decimal import Decimal as D

import app.routers.stock_opnames as SO
from app.services import stok_sesuai as SS

PERS, PENY = uuid.uuid4(), uuid.uuid4()


class Kon:
    async def fetch(self, sql, *a):
        assert "chart_of_accounts" in sql
        return [{"id": PERS, "account_code": "1-10600", "name": "Persediaan Barang Dagangan"}, {"id": PENY, "account_code": "5-50100", "name": "Biaya Penyesuaian Persediaan"}]


def test_jurnal_teks_bentuk_dan_null():
    r = asyncio.run(SO._jurnal_teks(Kon(), "t", [(PERS, D(1500), D(0), "x"), (PENY, D(0), D(1500), "y")]))
    assert r == [{"akun": "1-10600 · Persediaan Barang Dagangan", "debit_text": "Rp 1.500", "kredit_text": None},
                 {"akun": "5-50100 · Biaya Penyesuaian Persediaan", "debit_text": None, "kredit_text": "Rp 1.500"}]
    assert asyncio.run(SO._jurnal_teks(Kon(), "t", [])) == []


def test_jurnal_post_naik_dan_turun(monkeypatch):
    async def hpp(conn, tid, pid):
        return D("1000.4")

    async def akun(conn, tid, bl):
        return PERS, PENY
    monkeypatch.setattr(SS, "harga_pokok", hpp)
    monkeypatch.setattr(SS, "_akun", akun)
    items = [({"product_id": 1}, D("2")), ({"product_id": 2}, D("-3"))]       # naik 2 x 1000,4 = 2001 ; turun 3 x 1000,4 = 3001
    r = asyncio.run(SO._jurnal_post(Kon(), "t", items))
    assert [(x["akun"].split(" · ")[0], x["debit_text"], x["kredit_text"]) for x in r] == [
        ("1-10600", "Rp 2.001", None), ("5-50100", None, "Rp 2.001"), ("5-50100", "Rp 3.001", None), ("1-10600", None, "Rp 3.001")]
    assert sum(1 for x in r if x["debit_text"]) == sum(1 for x in r if x["kredit_text"])
    assert asyncio.run(SO._jurnal_post(Kon(), "t", [])) == []


def test_tiga_preview_membawa_jurnal_preview():
    for fn in (SO.pratinjau_post, SO.pratinjau_batal, SO.pratinjau_void):
        assert '"jurnal_preview"' in inspect.getsource(fn), fn.__name__
    assert '"jurnal_preview": []' in inspect.getsource(SO.pratinjau_batal)       # batal = tanpa jurnal
    assert "_jurnal_post(conn, tid, h[\"items\"])" in inspect.getsource(SO.pratinjau_post)
    assert "_jurnal_teks(conn, tid, sa.get(\"baris\")" in inspect.getsource(SO.pratinjau_void)
