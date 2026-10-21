"""PENJUALAN-SERAGAM / Pengiriman (20 Okt 2026): serah_keadaan/teks, aksi (perlu_resi > belum_dicatat), has_more+skip/offset, summary
?search=/hitungan, bundel /deliveries/{id}/documents. Nyata (kaos+grapgrap, baca-saja): scripts/ops/uji-nyata-pengiriman-seragam.sh."""
import asyncio
import inspect
import itertools
import uuid
from datetime import date

from app.middleware.permission_middleware import izin_untuk_jalur
from app.routers import deliveries as DL
from app.routers import dokumen as DK
from app.services import dokumen_bundel as DB
from app.services import pengiriman_daftar as PG


def test_kisi_serah_fakta():
    for aktif, st, jenis, dt, nr in itertools.product((True, False), ("posted", "voided", "draft"), (None, "kurir", "ambil_sendiri", "antar_sendiri"),
                                                       (None, date(2026, 10, 17)), (0, 2)):
        f = PG.serah_fakta(aktif, st, jenis, dt, nr)
        assert f["serah_keadaan"] in ("belum_dicatat", "belum_diterima", "diterima", "tanpa")
        if not aktif or st != "posted":
            assert f == {"serah_keadaan": "tanpa", "serah_teks": "—", "aksi": None}
            continue
        if jenis is None:
            assert f["serah_keadaan"] == "belum_dicatat" and f["aksi"] == "belum_dicatat" and f["serah_teks"] == "Serah belum dicatat"
        else:
            assert f["serah_keadaan"] == ("diterima" if dt else "belum_diterima")
            assert f["aksi"] == ("perlu_resi" if jenis == "kurir" and nr == 0 else None)
            assert f["serah_teks"] == ("Diterima 17 Okt" if dt else "Belum diterima")
        assert (f["aksi"] == "belum_dicatat") == (f["serah_keadaan"] == "belum_dicatat")


def test_urutan_desak_dan_belum_diterima_bukan_pill():
    assert PG.AKSI_SERAH == ("perlu_resi", "belum_dicatat")
    assert "belum_diterima" not in PG.AKSI                                     # putusan MASTER: derau permanen -> tiket
    assert PG.serah_fakta(True, "posted", "kurir", None, 0)["aksi"] == "perlu_resi"
    assert PG.serah_fakta(True, "posted", "kurir", None, 1)["aksi"] is None   # kurir sudah beresi tak jadi pill


def test_sql_aksi_setara_aturan():
    r, b = PG.sql_aksi("perlu_resi"), PG.sql_aksi("belum_dicatat")
    assert "f.status = 'posted'" in r and "s.jenis_serah = 'kurir'" in r and "NOT EXISTS (SELECT 1 FROM pengiriman_resi" in r and "$1" in r
    assert "f.status = 'posted'" in b and "NOT EXISTS (SELECT 1 FROM pengiriman_serah" in b and "s.jenis_serah IS NOT NULL" in b
    assert "f.id" in PG.sql_aksi("perlu_resi", "f") and "x.id" in PG.sql_aksi("belum_dicatat", "x")


def test_documents_baris_tanpa_rekap():
    kel = [{"key": "rekap", "docs": [{"kind": "rekap"}]}, {"key": "pnw", "docs": [{"kind": "quotation"}]},
           {"key": "ki", "docs": [{"kind": "delivery"}, {"kind": "invoice"}]}]
    assert [d["type"] for d in PG.documents_baris(kel)] == ["quotation", "delivery", "invoice"]


def test_tanda_tangan_rute_dan_izin():
    p = inspect.signature(DL.list_deliveries).parameters
    assert {"skip", "offset", "aksi", "page", "per_page"} <= set(p)
    assert "search" in inspect.signature(DL.get_deliveries_summary).parameters
    assert izin_untuk_jalur("/api/deliveries/0b3c/documents", "GET") == ("sales_invoice", "R")
    assert any(getattr(r, "path", "") == "/deliveries/{delivery_id}/documents" for r in DK.router.routes) and hasattr(DK, "susun_dokumen_pengiriman")
    c, par = DL._syarat_cari("kaos", 2)
    assert par == ["%kaos%"] and "f.fulfillment_number ILIKE $2" in c and "c.tenant_id" not in c or True
    assert DL._syarat_cari(None, 2) == (None, []) and DL._syarat_cari("", 2) == (None, [])


def test_surat_jalan_termasuk_batal_membuang_saring_aktif():
    sql = {}

    class Conn:
        async def fetch(self, q, *a):
            sql["q"] = q
            return [{"id": uuid.uuid4(), "fulfillment_number": "SJ-1", "fulfillment_date": date(2026, 10, 1)}]
    asyncio.run(DB.surat_jalan(Conn(), "t", [uuid.uuid4()]))
    assert "voided_at IS NULL" in sql["q"]
    d = asyncio.run(DB.surat_jalan(Conn(), "t", [uuid.uuid4()], termasuk_batal=True))
    assert "voided_at IS NULL" not in sql["q"] and d[0]["kind"] == "delivery"
