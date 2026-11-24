"""GET /api/reports/inventory-mutation (24 Nov 2026): awal + masuk − keluar + pindah + revaluasi = akhir; rekonsiliasi GL POSTED; hanya baca."""
import asyncio
import pathlib
import re
from datetime import date
from decimal import Decimal as D

import pytest

from app.services import laporan_mutasi_persediaan as LM

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
A, B = date(2026, 11, 1), date(2026, 11, 24)


class Konn:
    def __init__(self, rows, barang, gudang=(), ledger=("0", "0"), gl=("0", "0")):
        self.rows, self.barang, self.gudang, self.ledger, self.gl, self.log = rows, barang, list(gudang), ledger, gl, []

    async def fetch(self, sql, *a):
        self.log.append((sql, a))
        if sql in (LM.SQL_BARIS_BARANG, LM.SQL_BARIS_GUDANG):
            return self.rows
        return {LM.SQL_BARANG: self.barang, LM.SQL_GUDANG: self.gudang}[sql]

    async def fetchrow(self, sql, *a):
        self.log.append((sql, a))
        x = {LM.SQL_SEMUA: self.ledger, LM.SQL_GL: self.gl}[sql]
        return {"awal": D(x[0]), "akhir": D(x[1])}


def r(pid, q, n, gud=None, nama="L"):
    d = {"product_id": pid, "warehouse_id": gud, "nama_ledger": nama}
    for a, v in (("q", q), ("n", n)):
        d.update({f"{a}_{k}": D(str(x)) for k, x in zip(LM.KOLOM, v)})
    return d


def b(pid, kode, kat=None): return {"id": pid, "kode": kode, "nama": "Nama " + kode, "kategori": kat, "satuan": "pcs"}


def jalan(k, **kw):
    kw.setdefault("akun_persediaan", "akun")
    return asyncio.run(LM.muat_mutasi_persediaan(k, "t1", A, B, 0, **kw))


def dasar(**kw):
    return Konn([r("b", (0, 4, 5, 0, 0, -1), (0, 40, 50, 0, 0, -10)), r("a", (10, "2.5", 3, 0, 0, "9.5"), (1000, 250, 300, 0, 50, 1000))],
                [b("a", "A-1", "X"), b("b", "B-1", "Y")], ledger=("1000", "990"), **kw)


def test_baris_total_teks_dan_urutan():
    h = jalan(dasar(gl=("1000", "990")))
    assert [x["kode"] for x in h["baris"]] == ["A-1", "B-1"] and h["jumlah_minus"] == 1 and h["baris"][1]["minus"] is True
    x = h["baris"][0]
    assert x["masuk"] == {"qty": 2.5, "qty_text": "2,50", "nilai": 250.0, "nilai_text": "250"} and x["awal"]["nilai_text"] == "1.000"
    assert {k: v["nilai"] for k, v in h["total"].items()} == {"awal": 1000, "masuk": 290, "keluar": 350, "pindah": 0, "revaluasi": 50, "akhir": 990}
    assert h["baris"][1]["akhir"]["nilai_text"] == "(10)" and h["dari"] == "2026-11-01" and h["sampai"] == "2026-11-24" and h["periode_text"]


def test_rekonsiliasi_cocok_selisih_awal_saja_dan_tak_terpetakan():
    ok = jalan(dasar(gl=("1000", "990.01")))["rekonsiliasi"]
    assert ok["status"] == "cocok" and ok["mutasi"]["ledger"] == -10.0
    s = jalan(dasar(gl=("1000", "942")))["rekonsiliasi"]
    assert s["status"] == "selisih" and s["akhir"]["selisih"] == 48.0 and "saldo akhir" in s["status_text"]
    aw = jalan(dasar(gl=("900", "990")))["rekonsiliasi"]
    assert aw["status"] == "selisih" and aw["status_text"].startswith("Saldo akhir cocok") and aw["mutasi"]["selisih"] == -100.0
    assert jalan(dasar(gl=("1000", "989.98")))["rekonsiliasi"]["status"] == "selisih"
    t = jalan(dasar(), akun_persediaan=None)["rekonsiliasi"]
    assert t["status"] == "tak_terpetakan" and t["akhir"]["gl"] is None and t["akhir"]["selisih"] is None and t["akhir"]["ledger"] == 990.0


def test_saringan_kategori_tak_memengaruhi_rekonsiliasi():
    h = jalan(dasar(gl=("1000", "990")), kategori=" X ")
    assert [x["kode"] for x in h["baris"]] == ["A-1"] and h["total"]["akhir"]["nilai"] == 1000.0 and h["rekonsiliasi"]["akhir"]["ledger"] == 990.0


def test_per_gudang_transit_tanpa_gudang_dan_param():
    k = Konn([r("a", (0, 0, 0, 5, 0, 5), (0, 0, 0, 50, 0, 50), "g1"), r("a", (0, 0, 0, -5, 0, -5), (0, 0, 0, -50, 0, -50), None)], [b("a", "A-1")],
             [{"id": "g1", "name": "Transit", "is_system": True}])
    h = jalan(k, kelompok="gudang", gudang_id="g1")
    assert [x["gudang"]["nama"] for x in h["baris"]] == ["Dalam perjalanan (Transit)", "Tanpa gudang"] and h["total"]["pindah"]["nilai"] == 0.0
    assert next(a for s, a in k.log if s == LM.SQL_BARIS_GUDANG) == ("t1", A, B, "g1")
    assert all("g1" not in a for s, a in k.log if s in (LM.SQL_SEMUA, LM.SQL_GL))


def test_nol_disembunyikan_barang_tanpa_master_tetap_tampil_dan_batas():
    nol = r("z", (0,) * 6, (0,) * 6)
    assert jalan(Konn([nol], []))["baris"] == [] and len(jalan(Konn([nol], []), sertakan_nol=True)["baris"]) == 1
    # qty bersih nol tetapi bergerak tetap tampil
    assert len(jalan(Konn([r("a", (0, 1, 1, 0, 0, 0), (0, 5, 5, 0, 0, 0))], [b("a", "A")]))["baris"]) == 1
    assert jalan(Konn([r("h", (1,) * 6, (1,) * 6, nama="Lama")], []))["baris"][0]["nama"] == "Lama"
    n = LM.BATAS_BARIS + 1
    with pytest.raises(LM.LaporanTerlaluBesar):
        jalan(Konn([r(f"p{i}", (1,) * 6, (1,) * 6) for i in range(n)], []))


def test_sql_pagar_tenant_periode_dan_klasifikasi():
    for s in (LM.SQL_BARIS_BARANG, LM.SQL_BARIS_GUDANG, LM.SQL_SEMUA, LM.SQL_GL, LM.SQL_GUDANG):
        assert "tenant_id = $1" in s and not re.search(r"\b(INSERT|UPDATE|DELETE)\b", s, re.I)
    for s in (LM.SQL_BARIS_BARANG, LM.SQL_BARIS_GUDANG):
        assert "il.movement_date <= $3" in s and "($4::uuid IS NULL OR il.warehouse_id = $4)" in s
        for p in ("q", "n"):
            kol = {k: re.search(r"SUM\(((?:(?!SUM\().)*?)\) FILTER \(WHERE ((?:(?!SUM\().)*?)\), 0\) AS " + f"{p}_{k}\\b", s) for k in LM.KOLOM}
            assert kol["awal"][2] == "il.movement_date < $2" and kol["akhir"][2] == "TRUE"
            for k in ("masuk", "keluar", "pindah", "revaluasi"):
                assert kol[k][2].startswith("il.movement_date >= $2 AND ")
            assert "NOT (COALESCE(il.quantity_out, 0) > 0)" in kol["masuk"][2] and kol["keluar"][2].endswith("AND COALESCE(il.quantity_out, 0) > 0")
            for k in ("masuk", "keluar"):
                assert f"NOT {LM._PINDAH}" in kol[k][2] and f"NOT {LM._REVALUASI}" in kol[k][2]
            assert kol["keluar"][1].startswith("-") and kol["pindah"][2].endswith(LM._PINDAH) and kol["revaluasi"][2].endswith(LM._REVALUASI)
    assert "THEN -il.total_cost" in LM._N and "average_cost" not in LM.SQL_BARIS_BARANG
    assert LM.SQL_BARIS_GUDANG.endswith("GROUP BY il.product_id, il.warehouse_id") and LM.SQL_BARIS_BARANG.endswith("GROUP BY il.product_id")
    assert "je.status = 'POSTED'" in LM.SQL_GL and "is_effective" not in LM.SQL_GL and "je.journal_date <= $4" in LM.SQL_GL and "je.journal_date < $3" in LM.SQL_GL
    assert "$4" not in LM.SQL_SEMUA and "il.movement_date <= $3" in LM.SQL_SEMUA


def test_rute_readonly_izin_dan_rentang_terbalik():
    src = (APP / "routers" / "reports.py").read_text().split("async def get_inventory_mutation")[1].split("@router.get")[0]
    assert "readonly=True" in src and "LMP.muat_mutasi_persediaan(" in src and "AccountRole.INVENTORY_MERCHANDISE" in src
    assert "if dari > sampai:" in src and "status_code=422" in src
    assert '(r"^/api/reports", ["GET"], "reports", "R")' in (APP / "middleware" / "permission_middleware.py").read_text()
