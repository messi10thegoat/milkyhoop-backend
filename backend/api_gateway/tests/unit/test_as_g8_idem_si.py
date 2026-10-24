"""AS-G8 (24 Okt 2026): idempotensi aksi Faktur Penjualan -- PATCH /{id}, POST /{id}/post, POST /{id}/void, DELETE /{id}. Kunci klien saja
(idem_buat.mulai_aksi) = LANGKAH PERTAMA di transaksi, sebelum kunci domain / pra-cek; dicatat hanya di jalur sukses; awalan PER AKSI.
Tanpa kunci = respons lama apa adanya. PATCH & hapus: cek kini di DALAM transaksi di bawah FOR UPDATE; hapus = DELETE bersyarat tenant+draf."""
import ast
from pathlib import Path

import pytest

SRC = (Path(__file__).resolve().parents[2] / "app" / "routers" / "sales_invoices.py").read_text()
TREE = ast.parse(SRC)
AKSI = {"update_invoice": "SI_UPDATE", "post_invoice": "SI_POST", "void_invoice": "SI_VOID", "delete_invoice": "SI_DELETE"}


def _fn(nama):
    return next(n for n in TREE.body if isinstance(n, ast.AsyncFunctionDef) and n.name == nama)


def _calls(fn, attr=None, nid=None):
    return [n for n in ast.walk(fn) if isinstance(n, ast.Call)
            and ((attr and getattr(n.func, "attr", "") == attr) or (nid and getattr(n.func, "id", "") == nid))]


def _doc(fn):
    return fn.body[0].value if isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant) else None


@pytest.mark.parametrize("nama", list(AKSI))
def test_idem_pertama_di_transaksi_sebelum_cek_dan_kunci(nama):
    fn, awal = _fn(nama), AKSI[nama]
    m = _calls(fn, attr="mulai_aksi")
    assert len(m) == 1 and ast.get_source_segment(SRC, m[0].args[3]) == f'"{awal}"'
    assert ast.get_source_segment(SRC, m[0].args[2]) == "idem_buat.kunci_dari(request)"
    tx = [c.lineno for c in _calls(fn, attr="transaction")]
    doc = _doc(fn)
    sql = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str) and n is not doc
           and any(k in n.value for k in ("FROM sales_invoices", "pg_advisory_xact_lock", "DELETE FROM", "UPDATE sales_order_items"))]
    assert tx and sql and min(tx) <= m[0].lineno < min(sql), (nama, tx, m[0].lineno, sql)
    s = _calls(fn, attr="simpan")
    assert len(s) == 1 and ast.get_source_segment(SRC, s[0].args[4]) == f'"{awal}"'
    assert any(a.arg == "response" for a in fn.args.args)


def test_awalan_unik():
    assert len(set(AKSI.values())) == 4


def test_tanpa_kunci_respons_lama_apa_adanya():
    for nama in ("update_invoice", "post_invoice", "void_invoice"):
        seg = ast.get_source_segment(SRC, _fn(nama))
        assert f'"{AKSI[nama]}", resp, invoice_id)) if kp else resp' in seg, nama
    seg = ast.get_source_segment(SRC, _fn("delete_invoice"))
    assert "if kp:\n                    resp = await idem_buat.simpan(" in seg


def test_hapus_race_ditutup_kunci_baris_dan_delete_bersyarat():
    fn = _fn("delete_invoice")
    s = [n for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    fu = [n.lineno for n in s if "FROM sales_invoices" in n.value and "FOR UPDATE" in n.value]
    pagar = [n.lineno for n in s if n.value == "Faktur yang sudah diterbitkan tidak bisa dihapus. Batalkan fakturnya."]
    dele = [n for n in s if n.value.startswith("DELETE FROM sales_invoices")]
    assert fu and pagar and fu[0] < pagar[0]
    assert len(dele) == 1 and "tenant_id = $2" in dele[0].value and "status = 'draft'" in dele[0].value and "RETURNING id" in dele[0].value
    assert any(isinstance(n, ast.Constant) and n.value == "Faktur sudah berubah status. Muat ulang halaman." for n in ast.walk(fn))
    # objek berkas tetap SESUDAH commit (di luar blok transaksi), replay kembali lebih dulu
    w = next(n for n in ast.walk(fn) if isinstance(n, ast.AsyncWith) and "transaction" in ast.get_source_segment(SRC, n.items[0].context_expr))
    h = _calls(fn, nid="hapus_objek_sesudah_commit")
    assert h and h[0].lineno > w.end_lineno


def test_ubah_cek_di_bawah_kunci_baris_if_match_sesudah_idem():
    fn = _fn("update_invoice")
    m = _calls(fn, attr="mulai_aksi")[0].lineno
    fu = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)
          and "FROM sales_invoices" in n.value and "FOR UPDATE" in n.value]
    im = [c.lineno for c in _calls(fn, nid="assert_if_match")]
    status = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Constant) and n.value == "Faktur berstatus Batal tidak bisa diubah."]
    assert fu and im and status and m < fu[0] < im[0] < status[0]
    luar = [n for n in ast.walk(fn) if isinstance(n, ast.AsyncWith) and "transaction" in ast.get_source_segment(SRC, n.items[0].context_expr)]
    assert min(w.lineno for w in luar) < fu[0]


def test_posting_pracek_di_dalam_transaksi_sesudah_idem():
    fn = _fn("post_invoice")
    m = _calls(fn, attr="mulai_aksi")[0].lineno
    pracek = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Constant) and n.value == "Hanya faktur berstatus Draf yang bisa diterbitkan."]
    tb = [c.lineno for c in _calls(fn, nid="terbitkan_faktur")]
    assert m < pracek[0] < tb[0]


def test_kontrol_merah_delete_tanpa_syarat_tertangkap():
    palsu = 'await conn.execute("DELETE FROM sales_invoices WHERE id = $1", invoice_id)'
    n = next(x for x in ast.walk(ast.parse(palsu)) if isinstance(x, ast.Constant) and isinstance(x.value, str))
    assert not ("status = 'draft'" in n.value and "tenant_id" in n.value)
