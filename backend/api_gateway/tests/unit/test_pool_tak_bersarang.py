"""Law 32 — koneksi pool BERSARANG (28 Sep 2026, journey V323): create/post/reverse jurnal memanggil get_journal (yang
mengambil koneksi pool SENDIRI) sambil MASIH memegang koneksi pertama. Pool max 10 (bawaan prod) + 10 permintaan
bersamaan = semua memegang 1 dan menunggu 1 lagi -> beku selamanya (acquire tanpa batas waktu). Terukur di salinan.

Pindai statis semua routers/: di dalam `async with ...acquire()`, tak boleh `await f(...)` bila f fungsi modul yang
sama yang mengambil pool. DIKENAL = kasus lama yang BELUM diperbaiki (daftar hanya boleh MENYUSUT)."""
import ast
import os

ROUTERS = os.path.join(os.path.dirname(__file__), "..", "..", "app", "routers")

DIKENAL = {   # berkas:fungsi -> fungsi-peminjam; tiket: pool bersarang (laporan BACKEND 28 Sep)
    "document_intake.py:execute_batch->_require_legacy_journal_perm",
    "fiscal_years.py:create_fiscal_year->get_fiscal_year",
    "fiscal_years.py:close_fiscal_year->get_fiscal_year",
    "periods.py:update_period->get_period",
    "periods.py:close_period->get_period",
    "periods.py:reopen_period->get_period",
    "permissions.py:update_role_permissions->get_role_permissions",
    "stock_transfers.py:update_stock_transfer->get_stock_transfer",
}


def _ambil_pool(fn) -> bool:
    s = ast.unparse(fn)
    return any(k in s for k in ("pool.acquire", "get_pool()", "get_db_pool()", "get_db_connection()"))


def _pindai():
    temuan = set()
    for f in sorted(os.listdir(ROUTERS)):
        if not f.endswith(".py"):
            continue
        t = ast.parse(open(os.path.join(ROUTERS, f)).read())
        fns = {n.name: n for n in t.body if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))}
        peminjam = {k for k, v in fns.items() if isinstance(v, ast.AsyncFunctionDef) and _ambil_pool(v)}
        for fn in fns.values():
            for w in ast.walk(fn):
                if isinstance(w, ast.AsyncWith) and any("acquire" in ast.unparse(i.context_expr) for i in w.items):
                    for n in ast.walk(ast.Module(body=w.body, type_ignores=[])):
                        if (isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
                                and isinstance(n.value.func, ast.Name) and n.value.func.id in peminjam
                                and n.value.func.id != fn.name):
                            temuan.add(f"{f}:{fn.name}->{n.value.func.id}")
    return temuan


TEMUAN = _pindai()


def test_pemindai_bisa_bicara():
    assert TEMUAN & DIKENAL, "pemindai tak melihat satu pun kasus lama yang diketahui ada -> alat buta"


def test_tak_ada_pool_bersarang_baru():
    baru = sorted(TEMUAN - DIKENAL)
    assert not baru, "await fungsi-pengambil-pool di dalam blok acquire (Law 32, deadlock pool):\n" + "\n".join(baru)


def test_daftar_dikenal_hanya_menyusut():
    sembuh = sorted(DIKENAL - TEMUAN)
    assert not sembuh, "kasus lama sudah diperbaiki — HAPUS dari DIKENAL:\n" + "\n".join(sembuh)


def test_jurnal_melepas_koneksi_sebelum_get_journal():
    for nama in ("create_journal", "post_journal", "reverse_journal", "get_journal_by_source"):
        assert f"journals.py:{nama}->get_journal" not in TEMUAN
