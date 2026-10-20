"""PENJUALAN-SERAGAM / Proforma (21 Okt 2026): jatuh_tempo_keadaan/hari/teks (setara aksi), documents/doc_count, with_totals, summary ?search=,
bundel /proformas/{id}/documents (jangkar draf & batal tampil sebagai dirinya). Nyata: scripts/ops/uji-nyata-proforma-seragam.sh."""
import asyncio
import inspect
import itertools
import uuid
from datetime import date, timedelta

from app.middleware.permission_middleware import izin_untuk_jalur
from app.routers import dokumen as DK
from app.routers import proformas as PR
from app.services import dokumen_bundel as DB
from app.services import proforma_aksi as PA
from app.services import proforma_daftar as PD

HARI = date(2026, 10, 21)
TGL = [None, HARI - timedelta(days=9), HARI - timedelta(days=1), HARI, HARI + timedelta(days=1), HARI + timedelta(days=3), HARI + timedelta(days=4), HARI + timedelta(days=30)]


def test_kisi_jatuh_tempo_setara_aksi():
    for st, so, lunas, due in itertools.product(["draft", "issued", "cancelled", "expired"], ["confirmed", "cancelled", None], (True, False), TGL):
        f = PD.jatuh_tempo_fakta(st, so, lunas, due, HARI)
        aksi = PA.aksi_proforma(st, so, lunas, due, HARI)
        assert f["jatuh_tempo_keadaan"] in ("telat", "hari_ini", "akan", "selesai", "tanpa")
        assert (f["jatuh_tempo_keadaan"] == "telat") == (aksi == "lewat_tempo"), (st, so, lunas, due)       # telat == pill lewat_tempo
        if f["jatuh_tempo_terbuka"]:
            assert st == "issued" and so != "cancelled" and not lunas and due is not None and f["jatuh_tempo_hari"] == (due - HARI).days
            assert aksi in ("lewat_tempo", "segera_tempo", "menunggu_bayar")
        else:
            assert f["jatuh_tempo_hari"] is None
            assert f["jatuh_tempo_keadaan"] == ("selesai" if st == "issued" and lunas and due is not None else "tanpa")
        if f["jatuh_tempo_keadaan"] == "tanpa":
            assert f["jatuh_tempo_teks"] == "—"


def test_teks_persis():
    t = lambda d, **k: PD.jatuh_tempo_fakta("issued", "confirmed", k.get("lunas", False), d, HARI)["jatuh_tempo_teks"]  # noqa: E731
    assert t(HARI) == "Hari ini" and t(HARI + timedelta(days=1)) == "Besok" and t(HARI + timedelta(days=7)) == "28 Okt"
    assert t(HARI - timedelta(days=9)) == "12 Okt, lewat tempo" and t(HARI - timedelta(days=9), lunas=True) == "12 Okt"
    assert t(None) == "—"


def test_search_helper_satu_sumber():
    assert PR._syarat_cari(None, 2) == (None, []) and PR._syarat_cari("", 2) == (None, []) and PR._syarat_cari(object(), 2) == (None, [])
    p, par = PR._syarat_cari("kaos", 2)
    assert par == ["%kaos%"] and "p.proforma_number ILIKE $2" in p and "p.tenant_id" in p
    src = inspect.getsource(PR.list_proformas)
    assert "_syarat_cari(search" in src and "with_totals" in src


def test_tanda_tangan_rute_izin():
    assert {"with_totals", "aksi", "skip", "limit"} <= set(inspect.signature(PR.list_proformas).parameters)
    assert "search" in inspect.signature(PR.get_proforma_summary).parameters
    assert "cari" in inspect.signature(PR.aksi_semua_proforma).parameters
    assert izin_untuk_jalur("/api/proformas/0b3c/documents", "GET") == ("proforma", "R")
    assert any(getattr(r, "path", "") == "/proformas/{proforma_id}/documents" for r in DK.router.routes) and hasattr(DK, "susun_dokumen_proforma")


def test_proforma_so_termasuk_semua_default_lama(monkeypatch):
    from app.services import proforma_terbayar as PT

    async def tb(conn, tid, so_ids):
        return {}
    monkeypatch.setattr(PT, "terbayar_proforma", tb)
    sql, hasil = {}, {}

    class Conn:
        async def fetch(self, q, *a):
            sql["q"], sql["a"] = q, a
            semua = [{"id": uuid.uuid4(), "proforma_number": "PRO-1", "proforma_date": date(2026, 10, 1), "purpose": "DP", "amount": 1000,
                      "status": "draft", "issued_at": None},
                     {"id": uuid.uuid4(), "proforma_number": "PRO-2", "proforma_date": date(2026, 10, 1), "purpose": "DP", "amount": 500,
                      "status": "cancelled", "issued_at": None}]
            return semua if a[-1] else [r for r in semua if r["status"] != "draft"]  # meniru saring SQL ($3 OR status <> draft)

    asyncio.run(DB.proforma_so(Conn(), "t", uuid.uuid4(), False))
    assert sql["a"][-1] is False and "$3 OR status <> 'draft'" in sql["q"]                  # default = perilaku lama (saring draf)
    d_lama = asyncio.run(DB.proforma_so(Conn(), "t", uuid.uuid4(), False))
    assert d_lama == []                                                                     # cancelled & SO tak batal dibuang (lama)
    d_semua = asyncio.run(DB.proforma_so(Conn(), "t", uuid.uuid4(), False, termasuk_semua=True))
    assert sql["a"][-1] is True and [x["number"] for x in d_semua] == ["PRO-1", "PRO-2"]


def test_status_dokumen_draf_proforma():
    assert DK.status_dokumen("proforma", False, False, draf=True) == "draf"
    assert DK.status_dokumen("proforma", True, False, draf=True) == "batal"
    assert DK.status_dokumen("proforma", False, True) == "lunas" and DK.status_dokumen("proforma", False, False) == "terbit"
    src = inspect.getsource(DB.rampungkan)
    # S2 b (BACKEND, 20 Okt): himpunan jangkar-draf kini DB.DRAF_SEBAGAI_DIRINYA (faktur, proforma, kwitansi UM, NK) -- proforma tetap di dalamnya
    assert 'd["kind"] in DRAF_SEBAGAI_DIRINYA' in src and "proforma" in DB.DRAF_SEBAGAI_DIRINYA


def test_documents_baris_tanpa_rekap():
    kel = [{"key": "rekap", "docs": [{"kind": "rekap"}]}, {"key": "pnw", "docs": [{"kind": "quotation"}]}, {"key": "pro", "docs": [{"kind": "proforma"}]},
           {"key": "kw", "docs": [{"kind": "receipt"}, {"kind": "receipt"}]}]
    assert [d["type"] for d in PD.documents_baris(kel)] == ["quotation", "proforma", "receipt", "receipt"]
