"""BUG-272 (A) (16 Nov 2026): GET /api/reports/inventory-valuation -- nilai persediaan per tanggal (qty ledger × HPP terakhir) + rekonsiliasi ke GL peran INVENTORY_MERCHANDISE.
Selisih DILAPORKAN; WAC milik barang (gudang hanya menyaring qty); barang nonaktif/terhapus ikut; hanya baca; izin baca laporan."""
import asyncio
import pathlib
import re
from datetime import date
from decimal import Decimal as D

import pytest

from app.services import laporan_nilai_persediaan as LN

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
TGL = date(2026, 11, 16)


class Konn:
    """Kunci per SQL konstanta modul. wac/qty_semua/qty/barang/gudang/gl disetel per tes."""
    def __init__(self, wac, qty_semua, qty, barang, gudang=None, gl="0"):
        self.d = {"wac": wac, "semua": qty_semua, "qty": qty, "barang": barang, "gudang": gudang or [], "gl": gl}
        self.panggilan = []

    async def fetch(self, sql, *a):
        self.panggilan.append((sql, a))
        if sql == LN.SQL_WAC:
            return self.d["wac"]
        if sql == LN.SQL_QTY_SEMUA:
            return self.d["semua"]
        if sql in (LN.SQL_QTY_BARANG, LN.SQL_QTY_GUDANG):
            return self.d["qty"]
        if sql == LN.SQL_BARANG:
            return self.d["barang"]
        if sql == LN.SQL_GUDANG:
            return self.d["gudang"]
        raise AssertionError("SQL tak dikenal: " + sql[:60])

    async def fetchval(self, sql, *a):
        self.panggilan.append((sql, a))
        assert sql == LN.SQL_GL
        return self.d["gl"]


def w(pid, hpp): return {"product_id": pid, "average_cost": D(str(hpp))}
def q(pid, qty, gud=None): return {"product_id": pid, "warehouse_id": gud, "qty": D(str(qty))}
def b(pid, kode, nama, kat=None, sat="pcs"): return {"id": pid, "kode": kode, "nama": nama, "kategori": kat, "satuan": sat}


def jalan(k, **kw):
    kw.setdefault("akun_persediaan", "akun-1")
    return asyncio.run(LN.muat_nilai_persediaan(k, "t1", TGL, 0, **kw))


def kasus_dasar(gl="3000"):
    # A: 10 × 100 = 1000; B: 4 × 500,50 = 2002; C minus: -1 × 10 = -10 ; total = 2992
    return Konn([w("a", 100), w("b", "500.5"), w("c", 10)], [q("a", 10), q("b", 4), q("c", -1)], [q("a", 10), q("b", 4), q("c", -1)],
                [b("a", "A-1", "Aqua", "Minuman"), b("b", "B-1", "Beras", "Pangan", "kg"), b("c", "C-1", "Cacat")], gl=gl)


def test_nilai_per_barang_hpp_terakhir_minus_dan_total():
    r = jalan(kasus_dasar())
    x = {i["kode"]: i for i in r["baris"]}
    assert (x["A-1"]["qty"], x["A-1"]["hpp_satuan"], x["A-1"]["nilai"], x["A-1"]["nilai_text"]) == (10.0, 100.0, 1000.0, "1.000")
    assert (x["B-1"]["nilai"], x["B-1"]["hpp_satuan"], x["B-1"]["hpp_satuan_text"]) == (2002.0, 500.5, "500,50")   # HPP per satuan: sen tak dibulatkan walau presisi tenant 0 and x["C-1"]["minus"] is True and x["C-1"]["nilai_text"] == "(10)"
    assert r["total_nilai"] == 2992.0 and r["total_nilai_text"] == "2.992" and r["jumlah_baris"] == 3 and r["jumlah_minus"] == 1
    assert [i["kode"] for i in r["baris"]] == ["A-1", "B-1", "C-1"]


def test_rekonsiliasi_selisih_dilaporkan_dan_cocok_dalam_toleransi():
    sel = jalan(kasus_dasar(gl="2944.006"))["rekonsiliasi"]   # sub 2992 vs GL 2944,01 -> selisih 47,99
    assert sel["status"] == "selisih" and sel["selisih"] == 47.99 and sel["gl"] == 2944.01 and "Selisih Rp 47,99" in sel["status_text"]
    ok = jalan(kasus_dasar(gl="2992"))["rekonsiliasi"]
    assert ok["status"] == "cocok" and ok["selisih"] == 0.0
    kecil = jalan(kasus_dasar(gl="2991.99"))["rekonsiliasi"]   # toleransi = (10+4+1) x 0,005 + 0,01 = 0,085 -> selisih 0,01 masih cocok
    assert kecil["status"] == "cocok" and kecil["toleransi"] == 0.09 and kecil["selisih"] == 0.01
    assert jalan(kasus_dasar(gl="2990"))["rekonsiliasi"]["status"] == "selisih"   # 2,00 > toleransi


def test_peran_belum_dipetakan_tak_mengarang_gl():
    r = jalan(kasus_dasar(), akun_persediaan=None)["rekonsiliasi"]
    assert r["status"] == "tak_terpetakan" and r["gl"] is None and r["selisih"] is None and r["sub_ledger"] == 2992.0


def test_rekonsiliasi_tak_terpengaruh_saringan_tapi_baris_ya():
    k = kasus_dasar(gl="2992")
    r = jalan(k, kategori="Minuman")
    assert [i["kode"] for i in r["baris"]] == ["A-1"] and r["total_nilai"] == 1000.0
    assert r["rekonsiliasi"]["sub_ledger"] == 2992.0 and r["rekonsiliasi"]["status"] == "cocok"   # SEMUA barang


def test_kelompok_gudang_baris_per_gudang_dan_hpp_tetap_milik_barang():
    k = Konn([w("a", 100)], [q("a", 15)], [q("a", 10, "g1"), q("a", 5, "g2"), q("a", 0, "g3")], [b("a", "A-1", "Aqua")], [{"id": "g1", "name": "Utama"}, {"id": "g2", "name": "Cabang"}], gl="1500")
    r = jalan(k, kelompok="gudang")
    assert [(i["gudang"]["nama"], i["qty"], i["nilai"]) for i in r["baris"]] == [("Cabang", 5.0, 500.0), ("Utama", 10.0, 1000.0)]   # qty 0 tak tampil
    assert r["total_nilai"] == 1500.0 and r["rekonsiliasi"]["status"] == "cocok"
    sql, a = next(p for p in k.panggilan if p[0] == LN.SQL_QTY_GUDANG)
    k2 = Konn([w("a", 100)], [q("a", 15)], [q("a", 5, "g2")], [b("a", "A-1", "Aqua")], [{"id": "g2", "name": "Cabang"}], gl="1500")
    r2 = jalan(k2, kelompok="gudang", gudang_id="g2")
    assert k2.panggilan and next(p for p in k2.panggilan if p[0] == LN.SQL_QTY_GUDANG)[1][2] == "g2" and r2["rekonsiliasi"]["sub_ledger"] == 1500.0
    wac_sql = next(p for p in k2.panggilan if p[0] == LN.SQL_WAC)
    assert len(wac_sql[1]) == 2   # HPP tak disaring gudang


def test_sertakan_nol_dan_batas_baris():
    k = Konn([w("a", 100)], [q("a", 0)], [q("a", 0)], [b("a", "A-1", "Aqua")])
    assert jalan(k)["baris"] == [] and len(jalan(k, sertakan_nol=True)["baris"]) == 1
    n = LN.BATAS_BARIS + 1
    kb = Konn([w(f"p{i}", 1) for i in range(n)], [q(f"p{i}", 1) for i in range(n)], [q(f"p{i}", 1) for i in range(n)], [b(f"p{i}", f"K{i}", f"N{i}") for i in range(n)])
    with pytest.raises(LN.LaporanTerlaluBesar):
        jalan(kb)


def test_sql_hpp_urutan_aplikasi_per_tanggal_berpagar_tenant_hanya_baca():
    assert "il.movement_date <= $2" in LN.SQL_WAC and "ORDER BY il.product_id, il.created_at DESC, il.id DESC" in LN.SQL_WAC and "DISTINCT ON (il.product_id)" in LN.SQL_WAC
    for s in (LN.SQL_WAC, LN.SQL_QTY_BARANG, LN.SQL_QTY_GUDANG, LN.SQL_QTY_SEMUA, LN.SQL_BARANG, LN.SQL_GUDANG, LN.SQL_GL):
        assert "tenant_id = $1" in s and not re.search(r"\b(INSERT|UPDATE|DELETE)\b", s, re.I)
    assert all("$3::uuid IS NULL OR il.warehouse_id = $3" in x for x in (LN.SQL_QTY_BARANG, LN.SQL_QTY_GUDANG))   # gudang menyaring qty
    assert not any("warehouse_id = $3" in x for x in (LN.SQL_WAC, LN.SQL_QTY_SEMUA))   # HPP + rekonsiliasi tak disaring gudang
    assert "is_effective_journal(je.id)" in LN.SQL_GL and "je.journal_date <= $3" in LN.SQL_GL and "warehouse_stock" not in LN.SQL_QTY_SEMUA


def test_rute_dan_izin_laporan():
    src = (APP / "routers" / "reports.py").read_text()
    assert '@router.get("/inventory-valuation")' in src and "LNP.muat_nilai_persediaan(" in src and "AccountRole.INVENTORY_MERCHANDISE" in src
    assert "conn.transaction(readonly=True)" in src.split("async def get_inventory_valuation")[1].split("@router.get")[0]
    assert '(r"^/api/reports", ["GET"], "reports", "R")' in (APP / "middleware" / "permission_middleware.py").read_text()
