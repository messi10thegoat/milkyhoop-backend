"""U-BP-NOMOR (2 Nov 2026): /bills/{id}/payments/preview membawa payment_number_preview + payment_number_estimated dari
perkiraan_nomor_jurnal(conn, tenant, 'BP') -- rumus PERSIS get_next_journal_number (penulis record_payment), baca saja."""
import ast
import asyncio
import inspect

from app.routers import bills as RB
from app.services import bills_service as BS
from app.services import nomor_jurnal_perkiraan as NJ


def test_sql_meniru_fungsi_db_tanpa_tulis():
    s = NJ.SQL_PERKIRAAN_NOMOR_JURNAL
    for bagian in ("COALESCE($3::date, tanggal_bisnis($1))", "to_char(t.d, 'YYMM')", "EXTRACT(YEAR FROM t.d)", "EXTRACT(MONTH FROM t.d)",
                   "'^' || $2 || '-' || k.yymm || '-([0-9]+)$'", "GREATEST(COALESCE(s.last_number, 0) + 1, a.actual + 1)",
                   "lpad(", "4, '0')", "journal_number_sequences s ON s.tenant_id = $1 AND s.prefix = $2 AND s.year = k.y AND s.month = k.m"):
        assert bagian in s, bagian
    u = s.upper()
    assert "INSERT" not in u and "UPDATE" not in u and "FOR UPDATE" not in u and "DELETE" not in u


class _K:
    def __init__(self):
        self.log = []

    async def fetchval(self, sql, *a):
        self.log.append((sql, a))
        return "BP-2611-0007"


def test_perkiraan_meneruskan_tenant_prefix_tanggal():
    k = _K()
    assert asyncio.run(NJ.perkiraan_nomor_jurnal(k, "t", "BP")) == "BP-2611-0007"
    assert k.log[0][0] is NJ.SQL_PERKIRAAN_NOMOR_JURNAL and k.log[0][1] == ("t", "BP", None)


def test_pratinjau_bayar_tagihan_memakai_prefix_penulis():
    s = inspect.getsource(RB.preview_record_payment)
    pohon = ast.parse(s.lstrip())
    panggil = [ast.unparse(n) for n in ast.walk(pohon) if isinstance(n, ast.Call) and ast.unparse(n.func) == "perkiraan_nomor_jurnal"]
    assert panggil == ["perkiraan_nomor_jurnal(conn, ctx['tenant_id'], 'BP')"], panggil
    assert s.index("transaction(readonly=True)") < s.index("perkiraan_nomor_jurnal(") < s.index("tr.rollback()")
    assert '"payment_number_preview": nomor, "payment_number_estimated": True' in s
    # prefix SAMA dengan penulis record_payment
    assert "get_next_journal_number($1, 'BP')" in inspect.getsource(BS.BillsService.record_payment)
