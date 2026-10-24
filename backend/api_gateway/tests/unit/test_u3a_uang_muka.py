"""U3a Uang Muka BE (4 Okt 2026, MASTER GO): riwayat, pratinjau void/refund/apply (penentu + INTI YANG SAMA lalu
rollback), idempotensi void/refund/apply, pagar kunci void-draf/DELETE vs post. Tanpa DB; perilaku nyata (jurnal +
cermin bank pratinjau = tulis, R9 tetap, semua blok, replay/409, nol tulis) = harness kaos tx luar rollback."""
import inspect
import re
from pathlib import Path

from app.routers import customer_deposits as CD
from app.services import uang_muka_rencana as UMR

MW = Path(CD.__file__).parents[1] / "middleware" / "permission_middleware.py"


def test_rute_terpasang():
    nama = {(r.path, tuple(sorted(r.methods))): r.endpoint.__name__ for r in CD.router.routes}
    assert nama[("/{deposit_id}/void/preview", ("POST",))] == "preview_void_customer_deposit"
    assert nama[("/{deposit_id}/refund/preview", ("POST",))] == "preview_refund_customer_deposit"
    assert nama[("/{deposit_id}/apply/preview", ("POST",))] == "preview_apply_customer_deposit"
    assert nama[("/{deposit_id}/history", ("GET",))] == "get_customer_deposit_history"
    for p in ("/{deposit_id}/void", "/{deposit_id}/refund", "/{deposit_id}/apply"):
        assert nama[(p, ("POST",))].endswith("_customer_deposit")


def test_izin_pratinjau_sama_dengan_tulis_dan_riwayat_baca():
    t = MW.read_text()
    for akhir, aksi in (("apply", "P"), ("refund", "V"), ("void", "V")):
        assert f'(r"^/api/customer-deposits/[^/]+/{akhir}$", ["POST"], "customer_deposit", "{aksi}")' in t
        assert f'(r"^/api/customer-deposits/[^/]+/{akhir}/preview$", ["POST"], "customer_deposit", "{aksi}")' in t
    assert '(r"^/api/customer-deposits/[^/]+/history$", ["GET"], "customer_deposit", "R")' in t


def test_tulis_dan_pratinjau_memakai_penentu_yang_sama():
    """AS-G7c: penulis dan pratinjau memakai PENENTU yang sama (services/uang_muka_rencana); pratinjau READ ONLY tanpa inti."""
    assert CD._RENCANA_DP == {"void": UMR.rencana_batal, "refund": UMR.rencana_kembalikan, "apply": UMR.rencana_terapkan,
                              "reverse": CD._reverse_rencana_dp}
    for h, inti in ((CD.void_customer_deposit, "void_deposit_core"), (CD.refund_customer_deposit, "refund_deposit_core"),
                    (CD.apply_customer_deposit, "apply_deposit_core")):
        s = inspect.getsource(h)
        assert f"await {inti}(conn, ctx, deposit_id, body)" in s and "_ib.mulai_aksi(" in s
        assert s.index("_ib.mulai_aksi(") < s.index(f"await {inti}(")
    for inti, rencana in ((CD.void_deposit_core, "UMR.rencana_batal("), (CD.refund_deposit_core, "UMR.rencana_kembalikan("),
                          (CD.apply_deposit_core, "UMR.rencana_terapkan(")):
        assert rencana in inspect.getsource(inti), inti.__name__
    p = inspect.getsource(CD._pratinjau_dp)
    assert "conn.transaction(readonly=True)" in p and "_core(" not in p and "_RENCANA_DP[aksi](" in p


def test_kunci_void_dan_delete_urutan_sama_dengan_post():
    for f in (CD.void_deposit_core, CD.delete_customer_deposit):
        s = inspect.getsource(f)
        assert s.index('f"DEPOSIT_POST:{deposit_id}"') < s.index('f"DEPOSIT:{deposit_id}"'), f.__name__
        assert "AND status = 'draft'" in s and 'n != "DELETE 1"' in s, f.__name__
    d = inspect.getsource(CD.delete_customer_deposit)
    assert d.index("async with conn.transaction():") < d.index("SELECT id, status, deposit_number FROM customer_deposits")


def test_pesan_penentu_sama_dengan_inti_dan_berbahasa_indonesia():
    """Permintaan WORKSPACE: FE menampilkan pesan server APA ADANYA -> bahasa Indonesia + Rupiah; penentu == inti."""
    r = inspect.getsource(CD._rencana_dp)
    # AS-G7c: pemeriksaan penulis kini di penentu bersama (UMR.rencana_*), yang dipanggil inti sesudah kuncinya
    for f, pesan in ((UMR.rencana_batal, "Uang muka sudah diterapkan ke faktur. Lepas penerapannya dulu."),
                     (UMR.rencana_batal, "Uang muka sudah dikembalikan (sebagian) ke pelanggan, jadi tidak bisa dibatalkan."),
                     (UMR.rencana_batal, "Uang muka sudah dibatalkan."),
                     (UMR.rencana_kembalikan, "Rekening pengembalian tidak ditemukan."),
                     (UMR.rencana_kembalikan, "Rekening pengembalian harus akun kas/bank.")):
        assert pesan in inspect.getsource(f) and pesan in r, pesan
    for f in (CD.void_deposit_core, CD.refund_deposit_core, CD.apply_deposit_core, CD._rencana_dp, UMR.rencana_batal,
              UMR.rencana_kembalikan, UMR.rencana_terapkan, UMR.rencana_lepas):
        src = inspect.getsource(f)
        for inggris in ("Cannot ", "exceeds", "not found", "already applied", "must be an asset"):
            assert inggris not in src, (f.__name__, inggris)
    assert "Alasan pembatalan wajib diisi." in inspect.getsource(CD.void_deposit_core)
    assert CD._rp_dp(100000) == "Rp 100.000" and CD._rp_dp(1250.5) == "Rp 1.250,50"
    assert CD._status_dp("applied") == "Terpakai" and CD._status_dp("x") == "x"
