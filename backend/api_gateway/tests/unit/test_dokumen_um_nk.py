"""S2 b (20 Okt 2026): bundel /customer-deposits/{id}/documents + /credit-notes/{id}/documents lewat pembentuk bersama.
Harness nyata: scripts/ops/uji-nyata-dokumen-um-nk.{py,sh} (+ uji-nyata-dokumen-faktur.sh: bundel SO lama == baru)."""
import inspect

import pytest

from app.middleware.permission_middleware import izin_untuk_jalur
from app.routers import credit_notes as CN
from app.routers import customer_deposits as CD
from app.routers import dokumen as DK
from app.services import dokumen_bundel as DBN
from app.services import nota_kredit_daftar as NKD
from app.services import uang_muka_daftar as UMD


@pytest.mark.parametrize("jalur,harap", [
    ("/api/customer-deposits/0b3c/documents", ("customer_deposit", "R")),
    ("/api/credit-notes/0b3c/documents", ("credit_note", "R")),
])
def test_izin_rute(jalur, harap):
    assert izin_untuk_jalur(jalur, "GET") == harap


@pytest.mark.parametrize("fn", (UMD.kelompok_untuk, NKD.kelompok_untuk, DK.susun_dokumen_uang_muka, DK.susun_dokumen_nota_kredit))
def test_tanpa_sql_bentuk_dokumen_tersalin(fn):
    src = inspect.getsource(fn)
    for salinan in ("LEFT JOIN LATERAL", "sql_jenis_efektif", "label_rekening", "label_metode", "keadaan_kirim", '"paper"'):
        assert salinan not in src, salinan


def test_daftar_dan_bundel_satu_kelompok():
    assert "_umd.kelompok_untuk(" in inspect.getsource(CD.list_customer_deposits)
    assert "UMD.kelompok_untuk(" in inspect.getsource(DK.susun_dokumen_uang_muka)
    assert "_nkd.kelompok_untuk(" in inspect.getsource(CN.list_credit_notes)
    assert "NKD.kelompok_untuk(" in inspect.getsource(DK.susun_dokumen_nota_kredit)
    for fn in (DK.susun_dokumen_uang_muka, DK.susun_dokumen_nota_kredit):
        assert "DB.rampungkan(conn, tid, kelompok)" in inspect.getsource(fn)


def test_jangkar_sendiri_apa_pun_status():
    assert "kwitansi_uang_muka(conn, tid, ids, sendiri=True)" in inspect.getsource(UMD.kelompok_untuk)
    assert "DB.nota_kredit(conn, tid, ids)" in inspect.getsource(NKD.kelompok_untuk)
    # draf tampil "draf" hanya untuk dokumen yang tampil sebagai dirinya; rekap TIDAK (bundel SO tak berubah)
    assert set(DBN.DRAF_SEBAGAI_DIRINYA) == {"invoice", "proforma", "receipt", "credit_note"}
    assert DK.status_dokumen("receipt", False, None, draf=True) == "draf"
    assert DK.status_dokumen("credit_note", True, None, draf=True) == "batal"
