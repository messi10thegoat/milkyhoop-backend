"""PENJUALAN-SERAGAM / Penawaran (17 Okt 2026): kedaluwarsa_keadaan/hari/teks, documents/doc_count, with_totals(sum_total), summary ?search=.
Nyata (kaos+grapgrap, baca-saja, lama vs baru): scripts/ops/uji-nyata-penawaran-seragam.py."""
import asyncio
import inspect
import itertools
import uuid
from datetime import date, timedelta

from app.routers import quotes as QR
from app.schemas.quotes import QuoteDetail, QuoteListItem, QuoteListResponse
from app.services import penawaran_daftar as PD
from app.services import so_posisi as SP
from app.services.penawaran_kedaluwarsa import kedaluwarsa

HARI = date(2026, 10, 17)
TGL = [None, HARI - timedelta(days=9), HARI - timedelta(days=1), HARI, HARI + timedelta(days=1), HARI + timedelta(days=2), HARI + timedelta(days=30)]
STATUS = ["draft", "sent", "viewed", "accepted", "declined", "converted", "void"]


def test_kisi_keadaan_cocok_aturan_kedaluwarsa_lama():
    for st, tgl in itertools.product(STATUS, TGL):
        f = PD.kedaluwarsa_fakta(st, tgl, HARI)
        assert f["kedaluwarsa_keadaan"] in ("telat", "hari_ini", "akan", "selesai", "tanpa")
        assert (f["kedaluwarsa_keadaan"] == "telat") == kedaluwarsa(st, tgl, HARI), (st, tgl)   # telat == is_expired
        if f["kedaluwarsa_terbuka"]:
            assert st in ("sent", "viewed") and tgl is not None and f["kedaluwarsa_hari"] == (tgl - HARI).days
            assert f["kedaluwarsa_keadaan"] == ("telat" if f["kedaluwarsa_hari"] < 0 else "hari_ini" if f["kedaluwarsa_hari"] == 0 else "akan")
        else:
            assert f["kedaluwarsa_hari"] is None
            assert f["kedaluwarsa_keadaan"] == ("selesai" if st in ("accepted", "converted", "declined") and tgl is not None else "tanpa")
        assert f["kedaluwarsa_teks"] == ("—" if f["kedaluwarsa_keadaan"] == "tanpa" else f["kedaluwarsa_teks"])


def test_teks_sel():
    assert PD.kedaluwarsa_fakta("sent", HARI, HARI)["kedaluwarsa_teks"] == "Hari ini"
    assert PD.kedaluwarsa_fakta("viewed", HARI + timedelta(days=1), HARI)["kedaluwarsa_teks"] == "Besok"
    assert PD.kedaluwarsa_fakta("sent", HARI + timedelta(days=7), HARI)["kedaluwarsa_teks"] == "24 Okt"
    assert PD.kedaluwarsa_fakta("sent", HARI - timedelta(days=5), HARI)["kedaluwarsa_teks"] == "12 Okt, lewat masa"
    assert PD.kedaluwarsa_fakta("accepted", HARI - timedelta(days=5), HARI)["kedaluwarsa_teks"] == "12 Okt"
    assert PD.kedaluwarsa_fakta("draft", HARI + timedelta(days=5), HARI)["kedaluwarsa_teks"] == "—"
    assert PD.kedaluwarsa_fakta("sent", None, HARI)["kedaluwarsa_keadaan"] == "tanpa"


def test_syarat_cari_satu_sumber_param_berurut():
    assert PD.syarat_cari(None, 2) == (None, []) and PD.syarat_cari("   ", 2) == (None, [])
    p1, a1 = PD.syarat_cari("kaos", 2)
    assert a1 == ["%kaos%"] and "$2" in p1 and "$3" not in p1 and p1.count("ILIKE $2") >= 4 and "quotes.tenant_id" in p1
    p2, a2 = PD.syarat_cari("kaos biru", 4)
    assert a2 == ["%kaos%", "%biru%"] and " AND " in p2 and "$4" in p2 and "$5" in p2 and p2.startswith("(")


def test_dokumen_penawaran_tanpa_so_quotation_saja_dengan_so_dokumen_so(monkeypatch):
    q1, q2, so = str(uuid.uuid4()), str(uuid.uuid4()), uuid.uuid4()

    async def dok(conn, tenant, rows):
        assert [r["id"] for r in rows] in ([so], [])
        return {so: [{"type": "quotation"}, {"type": "proforma"}, {"type": "invoice"}]}

    class Conn:
        async def fetch(self, sql, *a):
            return [{"id": so, "status": "confirmed", "quote_id": uuid.uuid4()}]
    monkeypatch.setattr(SP, "dokumen_per_so", dok)
    hasil = asyncio.run(PD.dokumen_penawaran(Conn(), "t1", {q1: None, q2: str(so)}))
    assert hasil[q1] == [{"type": "quotation"}] and [d["type"] for d in hasil[q2]] == ["quotation", "proforma", "invoice"]
    assert asyncio.run(PD.dokumen_penawaran(Conn(), "t1", {q1: None})) == {q1: [{"type": "quotation"}]}


def test_aturan_dokumen_satu_so_sama_untuk_pesanan_dan_penawaran():
    pf = [{"status": "issued"}, {"status": "draft"}, {"status": "cancelled"}]
    d = SP.dokumen_baris_so("confirmed", True, pf, 2, 1, 1)
    assert [x["type"] for x in d] == ["quotation", "proforma", "receipt", "receipt", "delivery", "invoice"]
    assert [x["type"] for x in SP.dokumen_baris_so("cancelled", False, pf, 0, 0, 0)] == ["proforma", "proforma"]


def test_skema_dan_tanda_tangan():
    li = QuoteListItem.model_fields
    for k in ("kedaluwarsa_terbuka", "kedaluwarsa_hari", "kedaluwarsa_keadaan", "kedaluwarsa_teks", "documents", "doc_count"):
        assert k in li and (k in QuoteDetail.model_fields or k in ("documents", "doc_count")), k
    assert "sum_total" in QuoteListResponse.model_fields
    assert "with_totals" in inspect.signature(QR.list_quotes).parameters and "search" in inspect.signature(QR.get_quote_summary).parameters


def test_search_bukan_string_dipanggil_langsung_tanpa_saring():
    assert PD.syarat_cari(object(), 2) == (None, [])


def test_bundel_penawaran_rute_izin_dan_jangkar():
    from app.middleware.permission_middleware import izin_untuk_jalur
    from app.routers import dokumen as DK
    assert izin_untuk_jalur("/api/quotes/0b3c/documents", "GET") == ("quote", "R")
    assert any(getattr(r, "path", "") == "/quotes/{quote_id}/documents" for r in DK.router.routes) and hasattr(DK, "susun_dokumen_penawaran")
    src = inspect.getsource(DK.susun_dokumen_penawaran)
    assert "susun_dokumen(conn, ctx, so_id, sertakan_nk=True)" in src and '"kind": "quotation"' in src and '"group": "pnw"' in src
    assert '"draf" if q["status"] == "draft"' in src and 'q["status"] == "void"' in src
