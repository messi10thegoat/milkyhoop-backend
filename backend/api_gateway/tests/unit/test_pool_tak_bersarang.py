"""Law 32 — koneksi pool BERSARANG (28 Sep 2026, journey V323): create/post/reverse jurnal memanggil get_journal (yang
mengambil koneksi pool SENDIRI) sambil MASIH memegang koneksi pertama. Pool max 10 (bawaan prod) + 10 permintaan
bersamaan = semua memegang 1 dan menunggu 1 lagi -> beku selamanya (acquire tanpa batas waktu). Terukur di salinan.

Pindai statis semua routers/: di dalam `async with ...acquire()`, tak boleh `await f(...)` bila f fungsi modul yang
sama yang mengambil pool. DIKENAL = kasus lama yang BELUM diperbaiki (daftar hanya boleh MENYUSUT)."""
import ast
import os

ROUTERS = os.path.join(os.path.dirname(__file__), "..", "..", "app", "routers")

DIKENAL: set = set()   # 28 Sep: SEMUA kasus lama diperbaiki (journals x4 + 9 titik lain) -> daftar KOSONG


def _ambil_pool(fn) -> bool:
    s = ast.unparse(fn)
    return any(k in s for k in ("pool.acquire", "get_pool()", "get_db_pool()", "get_db_connection()"))


def _pindai():
    temuan = set()
    for f in sorted(os.listdir(ROUTERS)):
        if f.endswith(".py"):
            temuan |= _pindai_sumber(f, open(os.path.join(ROUTERS, f)).read())
    return temuan


def _pindai_sumber(f, sumber):
    temuan = set()
    if True:
        t = ast.parse(sumber)
        fns = {n.name: n for n in t.body if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))}
        peminjam = {k for k, v in fns.items() if isinstance(v, ast.AsyncFunctionDef) and _ambil_pool(v)}
        for fn in fns.values():
            for w in ast.walk(fn):
                if isinstance(w, ast.AsyncWith) and any("acquire" in ast.unparse(i.context_expr) for i in w.items):
                    for n in ast.walk(ast.Module(body=w.body, type_ignores=[])):
                        if (isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
                                and isinstance(n.value.func, ast.Name) and n.value.func.id in peminjam
                                and n.value.func.id != fn.name
                                and not any(k.arg == "conn" for k in n.value.keywords)):   # koneksi pemanggil diteruskan
                            temuan.add(f"{f}:{fn.name}->{n.value.func.id}")
    return temuan


TEMUAN = _pindai()


CONTOH = """
async def baca(request):
    pool = await get_pool()
    async with pool.acquire() as c:
        return 1

async def tulis(request):
    pool = await get_pool()
    async with pool.acquire() as conn:
        x = 1
        return await baca(request)

async def aman(request):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await baca(request, conn=conn)
"""


def test_pemindai_bisa_bicara():
    # Law 33: DIKENAL kini kosong -> buktikan pemindai tetap MELIHAT pola ini (contoh sintetis), dan meloloskan conn=
    assert _pindai_sumber("contoh.py", CONTOH) == {"contoh.py:tulis->baca"}


def test_tak_ada_pool_bersarang_baru():
    baru = sorted(TEMUAN - DIKENAL)
    assert not baru, "await fungsi-pengambil-pool di dalam blok acquire (Law 32, deadlock pool):\n" + "\n".join(baru)


def test_daftar_dikenal_hanya_menyusut():
    sembuh = sorted(DIKENAL - TEMUAN)
    assert not sembuh, "kasus lama sudah diperbaiki — HAPUS dari DIKENAL:\n" + "\n".join(sembuh)


def test_jurnal_melepas_koneksi_sebelum_get_journal():
    for nama in ("create_journal", "post_journal", "reverse_journal", "get_journal_by_source"):
        assert f"journals.py:{nama}->get_journal" not in TEMUAN
