"""AS-GP4 (24 Okt 2026) UM Pembelian: idem PATCH/delete/unapply/void + kunci PATCH berurutan SAMA dengan posting + pratinjau ubah."""
import ast
import inspect
import textwrap

from app.routers import vendor_deposits as RV
from app.services import uang_muka_vendor_rencana as R


def _tubuh(fn):
    """Sumber pernyataan fungsi TANPA docstring/komentar antar-pernyataan -- urutan kunci diukur dari KODE (docstring yang menyebut
    "FOR UPDATE"/"VENDOR_DEPOSIT_VOID" dulu menipu .index())."""
    s = textwrap.dedent(inspect.getsource(fn))
    f = ast.parse(s).body[0]
    isi = f.body[1:] if ast.get_docstring(f) is not None else f.body
    return "\n".join(ast.get_source_segment(s, n) for n in isi)


def test_patch_kunci_urutan_sama_dengan_posting_dan_update_bersyarat_draf():
    p = _tubuh(RV.update_vendor_deposit)
    post = _tubuh(RV._posting_inti)
    kunci = 'f"VENDOR_DEPOSIT:{deposit_id}"'
    assert kunci in p and kunci in post  # advisory yang SAMA lebih dulu, lalu FOR UPDATE -- urutan kunci global sama, tanpa deadlock silang
    assert p.index("idem_buat.mulai_aksi(") < p.index(kunci) < p.index("FOR UPDATE") < p.index("R.rencana_ubah(")
    assert post.index(kunci) < post.index("FOR UPDATE")
    assert "AND status = 'draft'" in p  # UPDATE tak pernah mengenai UM yang sudah diposting


def test_hapus_kunci_sama_dan_idem():
    d = inspect.getsource(RV.delete_vendor_deposit)
    assert 'f"VENDOR_DEPOSIT:{deposit_id}"' in d and "FOR UPDATE" in d and '"UMV_DELETE"' in d
    assert d.index("idem_buat.mulai_aksi(") < d.index("pg_advisory_xact_lock")


def test_pratinjau_ubah_penentu_sama_read_only():
    pv = inspect.getsource(RV.preview_edit_vendor_deposit)
    assert "R.rencana_ubah(" in pv and "_baca_pratinjau(" in pv
    assert "R.rencana_ubah(" in inspect.getsource(RV.update_vendor_deposit)
    assert inspect.iscoroutinefunction(R.rencana_ubah)


def test_idem_void_unapply_sebelum_kunci():
    v = _tubuh(RV.void_vendor_deposit)
    assert v.index("idem_buat.mulai_aksi(") < v.index("VENDOR_DEPOSIT_VOID") and '"UMV_VOID"' in v
    u = _tubuh(RV.unapply_vendor_deposit)
    assert u.index("idem_buat.mulai_aksi(") < u.index("lepas_penerapan(") and '"UMV_UNAPPLY"' in u


def test_izin_pratinjau_ubah_nyata():
    from app.middleware.permission_middleware import izin_untuk_jalur
    assert izin_untuk_jalur("/api/vendor-deposits/6a263caa-0ea6-47e1-aa78-fc6575e38ff0/edit/preview", "POST") == ("vendor_deposit", "U")
    assert izin_untuk_jalur("/api/vendor-deposits/6a263caa-0ea6-47e1-aa78-fc6575e38ff0", "PATCH") == ("vendor_deposit", "U")


def test_tubuh_tanpa_docstring_kontrol():
    sumber = 'async def f():\n    """FOR UPDATE di docstring"""\n    # FOR UPDATE di komentar\n    x = 1\n    return "FOR UPDATE"\n'
    ns = {}
    exec(compile(sumber, "<kontrol>", "exec"), ns)
    import linecache
    linecache.cache["<kontrol>"] = (len(sumber), None, sumber.splitlines(True), "<kontrol>")
    t = _tubuh(ns["f"])
    assert t.count("FOR UPDATE") == 1 and t.index("x = 1") < t.index("FOR UPDATE")
