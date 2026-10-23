"""neto_tagihan_terposting (23 Okt 2026, aturan D GRN; permintaan BACKEND2): nilai neto B baris tagihan TERPOSTING untuk sisi penerimaan
(pasangan 'tagih_dulu') == neto_baris dgn dasar grand_total - tax_amount tersimpan (SAMA dgn post_bill/rencana_posting). Baca saja, tanpa kunci."""
import ast
import asyncio
import inspect
import textwrap
import uuid
from decimal import Decimal as D

from app.services import tagihan_aksi as TA


class _KonnNeto:
    def __init__(self, bills, items):
        self.bills, self.items, self.sql = bills, items, []

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM bills" in sql:
            return [b for b in self.bills if b["id"] in a[1]]
        return self.items[a[0]]


def test_hasil_sama_dengan_neto_baris_dasar_posting_dan_lintas_tagihan():
    b1, b2 = uuid.uuid4(), uuid.uuid4()
    i1, i2, i3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    items = {b1: [{"id": i1, "net": D("1000")}, {"id": i2, "net": D("2000")}], b2: [{"id": i3, "net": D("500")}]}
    bills = [{"id": b1, "grand_total": D("2970"), "tax_amount": D("270")}, {"id": b2, "grand_total": D("555"), "tax_amount": D("55")}]
    k = _KonnNeto(bills, items)
    out = asyncio.run(TA.neto_tagihan_terposting(k, "t", [b1, b2]))
    assert out == {**TA.neto_baris(items[b1], D("2700")), **TA.neto_baris(items[b2], D("500"))}
    assert out[i1] + out[i2] == D("2700") and out[i3] == D("500")  # diskon kepala 300 terbagi; tanpa diskon = bi.total
    s, a = k.sql[0]
    assert a[0] == "t" and "tenant_id = $1" in s and "status_v2 NOT IN ('draft', 'void')" in s
    assert "ORDER BY bi.line_number" in k.sql[1][0]  # urutan SAMA dgn rencana_grn (sisa sen ke baris terakhir bernilai)


def test_baca_saja_tanpa_kunci():
    pohon = ast.parse(textwrap.dedent(inspect.getsource(TA.neto_tagihan_terposting)))
    fn = pohon.body[0]
    fn.body = fn.body[1:]  # buang docstring
    kode = ast.unparse(pohon).upper()
    for t in ("FOR UPDATE", "PG_ADVISORY", "INSERT", "UPDATE ", "DELETE"):
        assert t not in kode, t
