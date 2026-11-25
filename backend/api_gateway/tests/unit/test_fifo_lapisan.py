"""FIFO unit 1: alokasi lapisan murni, pembaca, dan bentuk migrasi V510."""
import asyncio
import pathlib
import random
from decimal import Decimal as D

import pytest

from app.services import fifo_lapisan as F

MIG = pathlib.Path(__file__).resolve().parents[3] / "migrations"


def L(*x): return [F.Lapisan(i, D(str(q)), D(str(n))) for i, q, n in x]


def test_makan_tertua_dulu_dan_sisa_nilai_utuh():
    assert F.alokasi(L(("a", 3, 100), ("b", 5, 200)), D("4")) == [("a", D(3), D(100)), ("b", D(1), D("40.00"))]
    assert F.alokasi(L(("a", 3, 100)), D("1")) == [("a", D(1), D("33.33"))]
    assert F.alokasi(L(("a", 1, "33.34")), D("1")) == [("a", D(1), D("33.34"))]        # habis = seluruh sisa nilai
    assert F.alokasi(L(("z", 0, 0), ("a", 2, 5)), D("2")) == [("a", D(2), D(5))]
    assert F.alokasi(L(("a", 3, "0.01")), D("2")) == [("a", D(2), D("0.01"))]          # HALF_UP, tak melebihi sisa


def test_grn_tak_habis_dibagi_tanpa_bocor_sen():
    """Kasus nyata nyawit: 180 unit bernilai 3.400.000 (18.888,89 x 180 = 3.400.000,20)."""
    q, n, jumlah = D(180), D("3400000.00"), D(0)
    while q > 0:
        x = F.alokasi([F.Lapisan("a", q, n)], min(D(7), q))[0]
        q, n, jumlah = q - x.qty, n - x.nilai, jumlah + x.nilai
        assert n >= 0
    assert n == 0 and jumlah == D("3400000.00")


def test_acak_kekekalan():
    r = random.Random(7)
    for _ in range(300):
        ls = L(*[(str(i), D(r.randint(1, 99999)) / 10000, D(r.randint(0, 999999)) / 100) for i in range(r.randint(1, 6))])
        tot = sum(x.qty for x in ls)
        q = min(tot, max(D("0.0001"), (tot * D(r.random())).quantize(D("0.0001"))))
        h = F.alokasi(ls, q)
        assert sum(x.qty for x in h) == q and [x.layer_id for x in h] == [x.id for x in ls][:len(h)]
        assert all(0 <= x.nilai <= y.nilai and (x.qty < y.qty or x.nilai == y.nilai) for x, y in zip(h, ls))


def test_stok_kurang_ditolak_t4_dan_qty_tak_sah():
    with pytest.raises(F.StokFifoKurang) as e:
        F.alokasi(L(("a", 2, 5)), D("2.0001"))
    assert e.value.tersedia == D(2) and e.value.diminta == D("2.0001")
    for q in ("0", "-1"):
        with pytest.raises(ValueError):
            F.alokasi(L(("a", 2, 5)), D(q))


def test_rencana_dan_kunci():
    class K:
        log = []
        async def fetch(self, s, *a): self.log.append(a); return [{"id": "a", "qty": 2, "nilai": 9}]
        async def execute(self, s, *a): self.log.append(a[0])
    k = K()
    assert asyncio.run(F.rencana_keluar(k, "t", "p", 2)) == [("a", D(2), D(9))] and k.log == [("t", "p")]
    k.log.clear()
    asyncio.run(F.kunci(k, "t", ["b", "a", "b"]))
    assert k.log == ["STOCK_LAYER:t:a", "STOCK_LAYER:t:b"]
    assert "l.tenant_id = $1" in F.SQL_SISA and F.SQL_SISA.endswith("ORDER BY l.layer_date, l.seq")


def test_migrasi_aditif_tanpa_flag_tanpa_backfill_dan_pembalik_menolak():
    up, dn = (MIG / "V510__fifo_lapisan.sql").read_text(), (MIG / "V510__fifo_lapisan_ROLLBACK.sql").read_text()
    for x in ("INSERT INTO", "UPDATE products", "tenant_features", "DROP "):
        assert x not in up
    assert "FIFO_SISA" in up and "FIFO_TAMBAH_SAJA" in up and "FIFO_METODE_TERKUNCI" in up and up.count("ENABLE ROW LEVEL SECURITY") == 2
    assert dn.index("RAISE EXCEPTION") < dn.index("\nDROP")
