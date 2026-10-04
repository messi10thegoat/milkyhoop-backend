"""U3a-b (MASTER 4 Okt 2026): pratinjau lepas penerapan uang muka + idempotensi /reverse.

POST /customer-deposits/{id}/applications/{app}/reverse/preview = penentu (_rencana_dp 'reverse', pesan & urutan
SAMA dengan reverse_deposit_application_core) + INTI YANG SAMA di savepoint lalu rollback. Tanpa DB; perilaku nyata
(jurnal pembalik, sisa faktur/uang muka naik, nol tulis, replay) = harness kaos tx luar rollback."""
import inspect
from pathlib import Path

from app.routers import customer_deposits as CD

MW = Path(CD.__file__).parents[1] / "middleware" / "permission_middleware.py"


def _rata(f):
    return " ".join(inspect.getsource(f).split())


def test_rute_pratinjau_reverse_terpasang():
    nama = {(r.path, tuple(sorted(r.methods))): r.endpoint.__name__ for r in CD.router.routes}
    assert nama[("/{deposit_id}/applications/{application_id}/reverse/preview", ("POST",))] == \
        "preview_reverse_customer_deposit_application"
    assert nama[("/{deposit_id}/applications/{application_id}/reverse", ("POST",))] == \
        "reverse_customer_deposit_application"


def test_izin_pratinjau_reverse_sama_dengan_tulis():
    t = MW.read_text()
    for akhir in ("reverse", "reverse/preview"):
        assert f'(r"^/api/customer-deposits/[^/]+/applications/[^/]+/{akhir}$", ["POST"], "customer_deposit", "V")' in t


def test_pratinjau_memakai_penulis_yang_sama():
    assert CD._INTI_DP["reverse"] == "_reverse_inti_dp"
    assert "await reverse_deposit_application_core(conn, ctx, deposit_id, body.application_id)" in _rata(CD._reverse_inti_dp)
    p = _rata(CD.preview_reverse_customer_deposit_application)
    assert '_pratinjau_dp(ctx, deposit_id, "reverse", PratinjauReverseDp(application_id=application_id))' in p
    assert "_ctx_pratinjau(request)" in p


def test_idempotensi_reverse_sebelum_inti():
    s = _rata(CD.reverse_customer_deposit_application)
    assert '_ib.mulai_aksi(conn, ctx, _ib.kunci_dari(request), "DEPOSIT_REVERSE", application_id' in s
    assert s.index("_ib.mulai_aksi(") < s.index("await reverse_deposit_application_core(")
    assert "if _lama is not None: return _lama" in s and '_ib.simpan(conn, ctx, _kp, _sd, "DEPOSIT_REVERSE"' in s


def test_pesan_penentu_sama_dengan_inti():
    r, inti = inspect.getsource(CD._rencana_dp), inspect.getsource(CD.reverse_deposit_application_core)
    for pesan in ("Penerapan uang muka tidak ditemukan.", "Penerapan uang muka ini tidak punya jurnal untuk dibalik.",
                  "Jurnal penerapan uang muka ini sudah dibalik.", "tg.periode_tertutup(None,"):
        assert pesan in r and pesan in inti, pesan
    # urutan penentu == urutan inti: tanpa jurnal -> periode -> jurnal sudah dibalik
    seg = r[r.index('if aksi == "reverse":'):r.index('if aksi == "void":')]
    assert [seg.index(x) for x in ("APPLICATION_NO_JOURNAL", "PERIOD_CLOSED", "APPLICATION_JOURNAL_REVERSED")] == \
        sorted(seg.index(x) for x in ("APPLICATION_NO_JOURNAL", "PERIOD_CLOSED", "APPLICATION_JOURNAL_REVERSED"))
    # sudah dilepas: inti mengembalikan sukses idempoten tanpa menulis; pratinjau menandainya sebagai blok
    assert '"APPLICATION_ALREADY_REVERSED", 409, "Penerapan ini sudah dilepas."' in r


def test_periode_tutup_semua_blok_dp_lewat_teks_galat():
    r = inspect.getsource(CD._rencana_dp)
    assert "Periode akuntansi sudah {tutup}" not in r
    assert r.count('_blok_dp("PERIOD_CLOSED", 400, tg.periode_tertutup(None, tutup))') == 3
