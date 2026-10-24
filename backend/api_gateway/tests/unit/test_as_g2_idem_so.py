"""AS-G2 (24 Okt 2026): idempotensi aksi Pesanan Penjualan -- POST /{id}/confirm, /cancel, /close, DELETE /{id}. Kunci klien saja
(idem_buat.mulai_aksi): LANGKAH PERTAMA di transaksi (sebelum kunci baris/advisory/UPDATE bersyarat), respons dicatat di transaksi yang SAMA
hanya di jalur sukses; awalan PER AKSI (SO_CONFIRM/SO_CANCEL/SO_CLOSE/SO_DELETE). Respons batal/hapus kini disusun di DALAM transaksi."""
import ast
from pathlib import Path

import pytest

SRC = (Path(__file__).resolve().parents[2] / "app" / "routers" / "sales_orders.py").read_text()
TREE = ast.parse(SRC)
AKSI = {"confirm_sales_order": ("SO_CONFIRM", "_konfirmasi_so"), "cancel_sales_order": ("SO_CANCEL", "_rencana_batal_so"),
        "close_sales_order": ("SO_CLOSE", "_rencana_tutup_so"), "delete_sales_order": ("SO_DELETE", "hapus_so_draf_core")}


def _fn(nama):
    return next(n for n in TREE.body if isinstance(n, ast.AsyncFunctionDef) and n.name == nama)


def _panggil(fn, pred):
    return [n for n in ast.walk(fn) if isinstance(n, ast.Call) and pred(n)]


@pytest.mark.parametrize("nama", list(AKSI))
def test_idem_langkah_pertama_di_transaksi_lalu_simpan_di_jalur_sukses(nama):
    awal, inti = AKSI[nama]
    fn = _fn(nama)
    mulai = _panggil(fn, lambda c: getattr(c.func, "attr", "") == "mulai_aksi")
    assert len(mulai) == 1 and ast.get_source_segment(SRC, mulai[0].args[3]) == f'"{awal}"'
    assert ast.get_source_segment(SRC, mulai[0].args[2]) == "idem_buat.kunci_dari(request)"
    tx = [c.lineno for c in _panggil(fn, lambda c: getattr(c.func, "attr", "") == "transaction")]
    domain = [c.lineno for c in _panggil(fn, lambda c: getattr(c.func, "id", "") == inti)]
    kunci = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and ("FOR UPDATE" in n.value or "UPDATE sales_orders" in n.value)]
    assert tx and domain and min(tx) <= mulai[0].lineno < min(domain + kunci), (nama, tx, mulai[0].lineno, domain, kunci)
    simpan = _panggil(fn, lambda c: getattr(c.func, "attr", "") == "simpan")
    assert len(simpan) == 1 and ast.get_source_segment(SRC, simpan[0].args[4]) == f'"{awal}"'
    assert simpan[0].lineno > max(domain)
    # setiap jawaban SUKSES lewat simpan (replay = bentuk sama); tak ada `return SalesOrderResponse(` telanjang tersisa
    telanjang = [n for n in ast.walk(fn) if isinstance(n, ast.Return) and isinstance(n.value, ast.Call)
                 and getattr(n.value.func, "id", "") == "SalesOrderResponse"]
    assert not telanjang
    assert any(a.arg == "response" for a in fn.args.args)


def test_respons_batal_dan_hapus_di_dalam_transaksi():
    for nama in ("cancel_sales_order", "delete_sales_order"):
        fn = _fn(nama)
        withs = [n for n in ast.walk(fn) if isinstance(n, ast.AsyncWith)
                 and any("transaction" in ast.get_source_segment(SRC, i.context_expr) for i in n.items)]
        simpan = _panggil(fn, lambda c: getattr(c.func, "attr", "") == "simpan")[0]
        assert any(w.lineno < simpan.lineno <= w.end_lineno for w in withs), nama


def test_awalan_unik_per_aksi():
    assert len({a for a, _ in AKSI.values()}) == 4


def test_isi_sidik_alasan_untuk_batal_dan_tutup():
    for nama, var in (("cancel_sales_order", "alasan_batal"), ("close_sales_order", "reason")):
        m = _panggil(_fn(nama), lambda c: getattr(c.func, "attr", "") == "mulai_aksi")[0]
        assert ast.get_source_segment(SRC, m.args[5]) == f'{{"reason": {var}}}'


def test_pratinjau_tak_berkunci_idempotensi():
    for nama in ("preview_confirm_sales_order", "preview_cancel_sales_order", "preview_close_sales_order"):
        seg = ast.get_source_segment(SRC, _fn(nama))
        assert "mulai_aksi" not in seg and "idem_buat.simpan" not in seg, nama


def test_kontrol_merah_simpan_sesudah_blok_tx_tertangkap():
    palsu = '''
async def delete_sales_order(request, order_id, response=None):
    async with pool.acquire() as conn:
        async with conn.transaction():
            kp, sidik, lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "SO_DELETE", order_id, {}, response)
            data = await hapus_so_draf_core(conn, ctx, order_id)
        return await idem_buat.simpan(conn, ctx, kp, sidik, "SO_DELETE", data, order_id)
'''
    t = ast.parse(palsu); fn = t.body[0]
    w = next(n for n in ast.walk(fn) if isinstance(n, ast.AsyncWith) and "transaction" in ast.get_source_segment(palsu, n.items[0].context_expr))
    s = next(n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "simpan")
    assert not (w.lineno < s.lineno <= w.end_lineno)
