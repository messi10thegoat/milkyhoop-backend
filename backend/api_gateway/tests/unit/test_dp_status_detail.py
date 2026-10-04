"""status_detail uang muka (MASTER GO 5 Okt, opsi B): turunan TAMPILAN; status tersimpan + penjaga uang tak disentuh.

partially_refunded = 'partial' + belum dipakai + ada refund ("Sebagian dikembalikan"); refunded = 'applied' + belum
dipakai + ada refund ("Dikembalikan"); selain itu = status. Python (baris) dan SQL (filter/summary) harus sama --
kesetaraan atas SEMUA uang muka nyata dibuktikan harness kaos/grapgrap (tx rollback)."""
import inspect

import pytest

from app.routers import customer_deposits as CD
from app.services import teks_galat as tg


@pytest.mark.parametrize("status,dipakai,kembali,harap", [
    ("partial", 0, 1000, "partially_refunded"), ("partial", 500, 1000, "partial"), ("partial", 500, 0, "partial"),
    ("applied", 0, 5000, "refunded"), ("applied", 4000, 1000, "applied"), ("applied", 5000, 0, "applied"),
    ("posted", 0, 0, "posted"), ("draft", 0, 0, "draft"), ("void", 0, 0, "void"), ("partial", None, 10, "partially_refunded"),
])
def test_tabel_kebenaran(status, dipakai, kembali, harap):
    assert CD.status_detail_dp(status, dipakai, kembali) == harap


def test_sql_sama_bentuk_dengan_python():
    s = CD._SQL_STATUS_DETAIL
    assert "status = 'partial' AND COALESCE(amount_applied, 0) = 0 AND COALESCE(amount_refunded, 0) > 0 THEN 'partially_refunded'" in s
    assert "status = 'applied' AND COALESCE(amount_applied, 0) = 0 AND COALESCE(amount_refunded, 0) > 0 THEN 'refunded'" in s
    assert s.endswith("ELSE status END)")


def test_filter_daftar_dan_summary_memakai_sql_yang_sama():
    d = inspect.getsource(CD.list_customer_deposits)
    assert 'conditions.append(f"{_SQL_STATUS_DETAIL} = ${param_idx}")' in d
    assert 'conditions.append(f"status = ${param_idx}")' not in d
    sm = inspect.getsource(CD.get_customer_deposits_summary)
    assert 'query = f"""' in sm  # f-string: kalau tidak, {_SQL_STATUS_DETAIL} terkirim mentah -> 500
    assert "FILTER (WHERE {_SQL_STATUS_DETAIL} = 'partially_refunded')" in sm and "FILTER (WHERE {_SQL_STATUS_DETAIL} = 'refunded')" in sm


def test_penjaga_uang_tidak_disentuh():
    for f in (CD.apply_deposit_core, CD.refund_deposit_core, CD._rencana_dp):
        s = inspect.getsource(f)
        assert 'not in ("posted", "partial")' in s, f.__name__
        assert "status_detail_dp(" in s  # hanya untuk TEKS pesan
    assert "status IN ('posted', 'partial')" in inspect.getsource(CD.linked_so_deposits)


def test_label_layar():
    assert tg.status_id("dp", "partially_refunded") == "Sebagian dikembalikan"
    assert tg.status_id("dp", "refunded") == "Dikembalikan"


def test_medan_ada_di_daftar_detail_pratinjau():
    for f in (CD.list_customer_deposits, CD.list_customer_deposits_by_customer, CD.get_customer_deposit, CD._keadaan_dp):
        assert '"status_detail": status_detail_dp(' in inspect.getsource(f), f.__name__
