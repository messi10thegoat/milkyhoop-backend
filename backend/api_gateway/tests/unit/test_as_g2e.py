"""AS-G2e (25 Okt 2026, MASTER; Milkybook paket SO): pratinjau ubah SO DRAF READ ONLY -- penentu _rencana_ubah_draf (BACA + hitung) SAMA dgn
PATCH draf; penulis _tulis_ubah_draf satu-satunya tempat tulis jalur draf; dulu pratinjau draf = 409 SO_IS_DRAFT."""
import ast
import inspect
import textwrap

from app.routers import sales_orders as SO


def _fn(f):
    s = textwrap.dedent(inspect.getsource(f))
    return ast.parse(s).body[0], s


def _konstanta(f):
    n, _ = _fn(f)
    doc = ast.get_docstring(n)
    return [x.value for x in ast.walk(n) if isinstance(x, ast.Constant) and isinstance(x.value, str) and x.value != doc]


def _panggil(f):
    n, _ = _fn(f)
    return {getattr(x.func, "id", getattr(x.func, "attr", None)) for x in ast.walk(n) if isinstance(x, ast.Call)}


def test_penentu_draf_hanya_baca():
    sql = " ".join(_konstanta(SO._rencana_ubah_draf)).upper()
    assert not any(k in sql for k in ("INSERT ", "UPDATE ", "DELETE ")), "penentu draf MENULIS"
    assert not {"catat_riwayat", "ubah_judul"} & _panggil(SO._rencana_ubah_draf)
    assert {"_so_doc", "tolak_ppn_bila_non_pkp", "hitung_dp", "pelanggan_kanonik_tenant"} <= _panggil(SO._rencana_ubah_draf)


def test_penulis_draf_satu_tempat_tulis_dan_patch_memakai_keduanya():
    sql = " ".join(_konstanta(SO._tulis_ubah_draf))
    assert "DELETE FROM sales_order_items" in sql and "INSERT INTO sales_order_items" in sql and "UPDATE sales_orders SET" in sql
    assert "catat_riwayat" in _panggil(SO._tulis_ubah_draf)
    _, s = _fn(SO.update_sales_order)
    assert s.index("_rencana_ubah_draf(conn, ctx, order, body)") < s.index("_tulis_ubah_draf(conn, ctx, order, body, _r)")
    assert "DELETE FROM sales_order_items" not in s and "INSERT INTO sales_order_items" not in s  # tak ada penulis kedua
    assert s.index("_terapkan_nomor_so(conn, ctx, order, body)") < s.index('if order["status"] != "draft":')


def test_pratinjau_draf_read_only_tanpa_kunci_penentu_sama():
    _, s = _fn(SO.preview_edit_sales_order)
    assert s.index("transaction(readonly=True)") < s.index("_pratinjau_ubah_draf(conn, ctx, draf, body)") < s.index("_so_untuk_ubah(conn, ctx, oid)")
    _, p = _fn(SO._pratinjau_ubah_draf)
    assert "_terapkan_nomor_so(conn, ctx, order, body)" in p and "_rencana_ubah_draf(conn, ctx, order, body)" in p
    assert "FOR UPDATE" not in p and "pg_advisory" not in p and "_tulis_ubah_draf" not in p
    blok = s[s.index("transaction(readonly=True)"):s.index("_so_untuk_ubah(conn, ctx, oid)")]
    assert "FOR UPDATE" not in blok and "pg_advisory" not in blok
