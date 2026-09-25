"""Q-017 (26 Sep 2026): SATU aturan penawaran kedaluwarsa — tanggal BISNIS, sent+viewed (putusan MASTER).

Dulu: is_expired (daftar/detail) = expiry_date < date.today() (UTC server) AND status=='sent' (viewed tak
pernah); /expiring pakai CURRENT_DATE UTC & hanya 'sent'; summary sent_count/pending_value ikut menghitung
yang kedaluwarsa (medan lama DIBIARKAN, medan baru pending_active_* / expired_pending_*).
"""
import ast
from datetime import date
from pathlib import Path

import pytest

from app.schemas.quotes import QuoteSummary
from app.services import penawaran_kedaluwarsa as PK

APP = Path(__file__).resolve().parents[2] / "app"
SRC = (APP / "routers/quotes.py").read_text()
H = date(2026, 9, 26)            # tanggal bisnis WIB; UTC server masih 25 Sep pukul 00-07 WIB


@pytest.mark.parametrize("status,exp,harap", [
    ("sent", date(2026, 9, 25), True),
    ("viewed", date(2026, 9, 25), True),      # viewed IKUT kedaluwarsa
    ("sent", H, False),                      # habis HARI INI = belum kedaluwarsa
    ("sent", None, False),
    ("accepted", date(2026, 9, 1), False),
    ("expired", date(2026, 9, 1), False),    # status tersimpan; bukan 'menunggu'
])
def test_aturan(status, exp, harap):
    assert PK.kedaluwarsa(status, exp, H) is harap


def test_predikat_sql_setara():
    assert PK.sql_kedaluwarsa("$2") == "(status IN ('sent', 'viewed') AND expiry_date IS NOT NULL AND expiry_date < $2::date)"
    assert PK.sql_menunggu_aktif("$2") == "(status IN ('sent', 'viewed') AND (expiry_date IS NULL OR expiry_date >= $2::date))"


def _fungsi(nama):
    for n in ast.walk(ast.parse(SRC)):
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == nama:
            return n
    raise AssertionError(nama)


def _panggil(n, nama):
    return any(isinstance(c, ast.Call) and getattr(c.func, "id", getattr(c.func, "attr", None)) == nama for c in ast.walk(n))


@pytest.mark.parametrize("nama", ["list_quotes", "get_quote_detail", "get_expiring_quotes", "get_quote_summary"])
def test_tanpa_tanggal_server_dan_pakai_tanggal_bisnis(nama):
    n = _fungsi(nama)
    seg = ast.get_source_segment(SRC, n)
    assert "date.today()" not in seg and "CURRENT_DATE" not in seg, nama
    assert _panggil(n, "tanggal_dokumen"), nama


@pytest.mark.parametrize("nama,fn", [("list_quotes", "kedaluwarsa"), ("get_quote_detail", "kedaluwarsa"),
                                     ("get_expiring_quotes", "sql_menunggu_aktif"),
                                     ("get_quote_summary", "sql_kedaluwarsa"), ("get_quote_summary", "sql_menunggu_aktif")])
def test_memakai_satu_aturan(nama, fn):
    assert _panggil(_fungsi(nama), fn), (nama, fn)


def test_summary_medan_baru_dan_lama():
    f = QuoteSummary.model_fields
    for k in ("pending_active_count", "pending_active_value", "expired_pending_count", "expired_pending_value",
              "sent_count", "viewed_count", "pending_value", "expired_count"):
        assert k in f, k
    seg = " ".join(ast.get_source_segment(SRC, _fungsi("get_quote_summary")).split())
    assert "COUNT(*) FILTER (WHERE status = 'sent') as sent_count" in seg                     # arti lama tetap
    assert "COALESCE(SUM(total_amount) FILTER (WHERE status = 'sent'), 0) as pending_value" in seg


def test_summary_memetakan_medan_baru_ke_respons():
    seg = ast.get_source_segment(SRC, _fungsi("get_quote_summary"))
    for k in ("pending_active_count", "pending_active_value", "expired_pending_count", "expired_pending_value"):
        assert f'"{k}": row["{k}"]' in seg, k
