"""BUG-102/106/103 (K-11, MASTER 7 Nov): penjualan BERSIH retur.
102 Profitabilitas mengurangi Nota Kredit posted/applied (qty dasar, subtotal, HPP balik restock NK) -> rekonsiliasi L/R match;
    HPP dibandingkan dengan HPP sumber PENJUALAN (retur pembelian/PPV di akun COGS diungkap terpisah, bukan disembunyikan).
106 GET /api/reports/sales-by-customer = SATU sumber (SQL_BARIS_JUAL) -> Σ == Pendapatan L/R, status rekonsiliasi dikirim.
103 dashboard v2 receivables.terbuka = SEMUA faktur terbuka (maks 10, urut jatuh tempo) + terbuka_count.
Angka nyata nyawit 1-7 Nov (9.912.300 / HPP jual 9.204.808,72 / Minyakita 67 pcs) diuji di salinan: scripts/ops/uji-salinan-jual-bersih.*"""
import asyncio
import re
from datetime import date
from decimal import Decimal

from app.services import dashboard_v2 as DV
from app.services import profitability_query as PQ
from app.services import profitability_reconciliation as PR


def _maks(sql):
    return max(int(x) for x in re.findall(r"\$(\d+)", sql))


def test_baris_jual_faktur_plus_nota_kredit_minus_bertenant():
    s = " ".join(PQ.SQL_BARIS_JUAL.split())
    assert "FROM credit_note_items cni JOIN credit_notes cn ON cn.id = cni.credit_note_id" in s
    assert "cn.status IN ('posted', 'applied')" in s and "cn.credit_note_date >= $2 AND cn.credit_note_date <= $3" in s
    assert "-cni.subtotal" in s and "-(" in s and "'retur'" in s and "'faktur'" in s
    assert s.count("tenant_id = $1") >= 2 and "si.status != 'void'" in s and "si.accounting_status = 'POSTED'" in s
    assert "cni." in PQ.QTY_DASAR_NK and "cn.tenant_id" in PQ.QTY_DASAR_NK and "sii." not in PQ.QTY_DASAR_NK


def test_hpp_jual_mengurangi_restock_nota_kredit():
    s = " ".join(PQ.SQL_HPP_JUAL.split())
    assert "il.source_type = 'CREDIT_NOTE'" in s and "-il.total_cost" in s and "cn.status IN ('posted', 'applied')" in s


def test_satu_sumber_semua_kueri():
    for q in (PQ.ITEMS_QUERY, PQ.ITEMS_COUNT_QUERY, PQ.SUMMARY_QUERY, PQ.SERVICES_QUERY, PQ.PELANGGAN_QUERY, PQ.PELANGGAN_RINGKAS_QUERY,
              PR.REPORT_REVENUE_QUERY):
        assert PQ.SQL_BARIS_JUAL in q
    for q in (PQ.ITEMS_QUERY, PQ.SUMMARY_QUERY, PR.REPORT_COGS_QUERY):
        assert PQ.SQL_HPP_JUAL in q
    assert "sales_invoice_items sii" not in PR.REPORT_REVENUE_QUERY.replace(PQ.SQL_BARIS_JUAL, "")  # tak ada jalur kedua


def test_placeholder_sesuai_argumen_pemanggil():
    assert {_maks(q) for q in (PQ.ITEMS_QUERY, PQ.SERVICES_QUERY, PQ.PELANGGAN_QUERY)} == {5}
    assert {_maks(q) for q in (PQ.ITEMS_COUNT_QUERY, PQ.SUMMARY_QUERY, PQ.PELANGGAN_RINGKAS_QUERY, PR.REPORT_REVENUE_QUERY,
                               PR.REPORT_COGS_QUERY, PR.PNL_TOTALS_QUERY)} == {3}


def test_rekonsiliasi_hpp_sumber_penjualan_dan_non_jual_diungkap():
    s = " ".join(PR.PNL_TOTALS_QUERY.split())
    assert "AS pnl_cogs_jual" in s and "'CREDIT_NOTE_COGS'" in s and "'INVOICE_FULFILLMENT'" in s and "'DELIVERY_INVOICED'" in s
    assert "VENDOR_CREDIT" not in " ".join(PR.HPP_JUAL_JURNAL) and "BILL" not in PR.HPP_JUAL_JURNAL

    class C:
        def __init__(self):
            self.n = 0

        async def fetchrow(self, sql, *a):
            if "pnl_cogs_jual" in sql:
                return {"pnl_revenue": Decimal("9912300"), "pnl_cogs": Decimal("9200033.44"), "pnl_cogs_jual": Decimal("9204808.72")}
            if "goods_revenue" in sql:
                return {"goods_revenue": Decimal("9912300"), "services_revenue": Decimal("0")}
            return {"goods_cogs": Decimal("9204808.72")}
    r = asyncio.run(PR.check_reconciliation(C(), "t", date(2026, 11, 1), date(2026, 11, 7)))
    assert r["severity"] == "match" and r["revenue_drift"] == 0 and r["cogs_drift"] == 0
    assert abs(r["pnl_cogs_non_jual"] - (-4775.28)) < 0.005 and abs(r["pnl_cogs_total"] - 9200033.44) < 0.005


def test_dashboard_piutang_terbuka_semua_urut_jatuh_tempo():
    def baris(no, due, sisa, iid=True):
        return {"invoice_id": f"id-{no}" if iid else None, "invoice_number": f"INV-{no}", "customer_name": "Bu Sri", "customer_id": "c1",
                "due_date": due, "outstanding": Decimal(sisa)}
    hari = date(2026, 11, 7)
    rows = [baris("0005", date(2026, 11, 20), "141300"), baris("0008", date(2026, 11, 14), "15700"), baris("K", None, "-5000", iid=False)]
    rows += [baris(f"9{i:02d}", date(2026, 12, i + 1), "1000") for i in range(12)]
    r = asyncio.run(DV.piutang(None, "t", hari, rows))
    assert [x["number"] for x in r["due_7d"]] == ["INV-0008"]  # perilaku lama tetap (≤ 7 hari)
    assert r["terbuka_count"] == 14 and len(r["terbuka"]) == 10
    assert [x["number"] for x in r["terbuka"][:2]] == ["INV-0008", "INV-0005"]  # INV-0005 (20 Nov) kini ikut, urut jatuh tempo


def test_rute_sales_by_customer_terdaftar_dan_izin_baca_laporan():
    from app.middleware.permission_middleware import izin_untuk_jalur
    from app.routers import reports as R
    assert any(getattr(r, "path", "") == "/sales-by-customer" and "GET" in r.methods for r in R.router.routes)
    assert izin_untuk_jalur("/api/reports/sales-by-customer", "GET") == ("reports", "R")
