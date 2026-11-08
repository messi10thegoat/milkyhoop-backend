"""Kartu summary PO 'Perlu diterima' (8 Nov 2026, tugas MASTER grosir): perlu_diterima_value = Σ nilai SISA terima (barang yang belum
datang: (dipesan - diterima) x total baris / qty, 2dp per baris, jasa = 0), BUKAN Σ total PO. Satu sumber: kolom SQL_AGREGAT di samping
sisa_terima (lateral yang sama dipakai aksi/telat_terima). Kunci pill lain tetap Σ total PO.
(t) 8 Nov putusan MASTER: telat_terima_value IKUT nilai sisa terima -- satu definisi kartu barang-belum-datang."""
import asyncio
from decimal import Decimal as D

from starlette.requests import Request

from app.routers import purchase_orders as POR
from app.services import po_daftar as PD

SISA_QTY = "GREATEST(i.quantity - COALESCE(i.quantity_received, 0), 0)"


def test_agregat_memuat_nilai_sisa_terima_satu_ekspresi_dengan_sisa_terima():
    a = PD.SQL_AGREGAT
    assert f"SUM(CASE WHEN q.jasa THEN 0 ELSE {SISA_QTY} END), 0) AS sisa_terima" in a
    assert (f"SUM(CASE WHEN q.jasa THEN 0 ELSE ROUND({SISA_QTY} * i.total / NULLIF(i.quantity, 0), 2) END), 0) AS nilai_sisa_terima"
            in a)


SISA = ("perlu_diterima", "telat_terima")  # kartu barang-belum-datang


def test_nilai_aksi_kartu_barang_belum_datang():
    assert PD.NILAI_AKSI == {k: "ag.nilai_sisa_terima" for k in SISA}


def _summary(monkeypatch):
    tangkap = []

    class K:
        async def execute(self, *a):
            return None

        async def fetchrow(self, sql, *a):
            tangkap.append(sql)
            row = {k: 0 for k in ("total", "draft_count", "sent_count", "received_count", "billed_count", "closed_count",
                                  "cancelled_count", "total_value", "total_received", "total_billed", "pending_amount",
                                  "draft_bill_amount")}
            for k in PD.AKSI:
                row[f"{k}_count"], row[f"{k}_value"] = 0, D("0")
            row["perlu_diterima_count"], row["perlu_diterima_value"] = 2, D("2844000.004")
            row["telat_terima_count"], row["telat_terima_value"] = 1, D("744000")
            return row

    class P:
        def acquire(self):
            class Cm:
                async def __aenter__(s):
                    return K()

                async def __aexit__(s, *a):
                    return False
            return Cm()

    async def pool():
        return P()

    async def hari(conn, tid):
        return __import__("datetime").date(2026, 11, 8)
    monkeypatch.setattr(POR, "get_pool", pool)
    monkeypatch.setattr(PD, "tanggal_hari_ini", hari)
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    r.state.user = {"tenant_id": "t1", "user_id": "00000000-0000-0000-0000-000000000001"}
    d = asyncio.run(POR.get_purchase_orders_summary(r, search=None))["data"]
    return tangkap[0], d


def test_summary_perlu_diterima_value_dari_nilai_sisa_terima(monkeypatch):
    sql, d = _summary(monkeypatch)
    assert (f"COALESCE(SUM(ag.nilai_sisa_terima) FILTER (WHERE {PD.sql_aksi('perlu_diterima', '$2')}), 0) AS perlu_diterima_value"
            in sql)
    assert (d["perlu_diterima_count"], d["perlu_diterima_value"]) == (2, 2844000.0)


def test_summary_telat_terima_value_dari_nilai_sisa_terima(monkeypatch):
    sql, d = _summary(monkeypatch)
    assert (f"COALESCE(SUM(ag.nilai_sisa_terima) FILTER (WHERE {PD.sql_aksi('telat_terima', '$2')}), 0) AS telat_terima_value"
            in sql)
    assert (d["telat_terima_count"], d["telat_terima_value"]) == (1, 744000.0)


def test_summary_kunci_lain_tetap_total_po(monkeypatch):
    sql, _ = _summary(monkeypatch)
    for k in PD.AKSI:
        if k not in SISA:
            assert f"COALESCE(SUM(po.total_amount) FILTER (WHERE {PD.sql_aksi(k, '$2')}), 0) AS {k}_value" in sql, k
    for k in SISA:
        assert "SUM(po.total_amount) FILTER (WHERE " + PD.sql_aksi(k, "$2") not in sql, k
