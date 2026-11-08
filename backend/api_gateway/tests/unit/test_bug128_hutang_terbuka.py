"""BUG-128 (chat hutang): dashboard v2 payables.terbuka = SEMUA tagihan terbuka (maks 10, urut jatuh tempo lalu nomor) + terbuka_count,
bentuk SAMA dengan receivables.terbuka (BUG-103) lewat SATU penentu; days_overdue dihitung server dari tanggal tenant."""
import ast
import asyncio
import inspect
from datetime import date
from decimal import Decimal

from app.services import dashboard_v2 as DV

HARI = date(2026, 11, 8)


def _bill(no, due, sisa, bid=True, vendor="PT Minyak"):
    return {"bill_id": f"b-{no}" if bid else None, "bill_number": f"BILL-{no}", "vendor_name": vendor, "vendor_id": "v1",
            "due_date": due, "outstanding": Decimal(sisa)}


def _rows():
    rows = [_bill("2611-0007", date(2026, 10, 25), "250000"),  # jatuh tempo lewat > 7 hari (kasus BILL-2611-0007)
            _bill("2611-0003", date(2026, 11, 30), "120000"),  # jatuh tempo > 7 hari ke depan: dulu hilang dari due_7d
            _bill("2611-0002", date(2026, 11, 30), "80000")]
    rows += [_bill(f"2612-{i:04d}", date(2026, 12, i + 1), "1000") for i in range(10)]
    return rows


def test_hutang_terbuka_semua_urut_jatuh_tempo_lalu_nomor_maks_10():
    r = asyncio.run(DV.hutang(None, "t", HARI, _rows()))
    assert r["terbuka_count"] == 13 and len(r["terbuka"]) == 10
    assert [x["number"] for x in r["terbuka"][:3]] == ["BILL-2611-0007", "BILL-2611-0002", "BILL-2611-0003"]
    assert [x["number"] for x in r["due_7d"]] == ["BILL-2611-0007"]  # perilaku lama tetap


def test_hutang_terbuka_bentuk_dan_days_overdue_server():
    r = asyncio.run(DV.hutang(None, "t", HARI, _rows()))
    x = r["terbuka"][0]
    assert set(x) == {"bill_id", "number", "vendor", "due_date", "balance", "days_overdue"}
    assert x == {"bill_id": "b-2611-0007", "number": "BILL-2611-0007", "vendor": "PT Minyak", "due_date": "2026-10-25",
                 "balance": DV.uang(Decimal("250000")), "days_overdue": 14}
    assert r["terbuka"][1]["days_overdue"] == 0  # belum jatuh tempo


def test_sigma_terbuka_semua_sama_total_tanpa_saldo_awal_kredit():
    rows = _rows()[:3]
    r = asyncio.run(DV.hutang(None, "t", HARI, rows))
    assert sum(Decimal(str(x["balance"])) for x in r["terbuka"]) == Decimal(str(r["total"]))


def test_saldo_awal_dan_kredit_tak_masuk_terbuka_tapi_tetap_di_total():
    rows = _rows()[:3] + [_bill("SA", None, "50000", bid=False), _bill("ND", date(2026, 11, 1), "-7000")]
    r = asyncio.run(DV.hutang(None, "t", HARI, rows))
    assert r["terbuka_count"] == 3
    assert "BILL-SA" not in [x["number"] for x in r["terbuka"]] and "BILL-ND" not in [x["number"] for x in r["terbuka"]]


def test_piutang_terbuka_bentuk_sama_dengan_hutang():
    rows = [{"invoice_id": "i1", "invoice_number": "INV-1", "customer_name": "Bu Sri", "customer_id": "c1",
             "due_date": date(2026, 11, 1), "outstanding": Decimal("5000")}]
    p = asyncio.run(DV.piutang(None, "t", HARI, rows))["terbuka"][0]
    assert set(p) == {"invoice_id", "number", "customer", "due_date", "balance", "days_overdue"} and p["days_overdue"] == 7


def test_satu_penentu_terbuka_dipanggil_piutang_dan_hutang():
    # penjaga AST: kedua sisi memanggil penentu yang SAMA (tak ada salinan sort/filter/irisan di masing-masing fungsi)
    for fn in (DV.piutang, DV.hutang):
        pohon = ast.parse(inspect.getsource(fn).lstrip())
        dipanggil = {n.func.id for n in ast.walk(pohon) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "daftar_terbuka" in dipanggil, fn.__name__
    assert callable(DV.daftar_terbuka)


def test_days_overdue_selalu_int_juga_tanpa_jatuh_tempo():
    r = asyncio.run(DV.hutang(None, "t", HARI, [_bill("X", None, "5000")]))
    x = r["terbuka"][0]
    assert x["due_date"] is None and x["days_overdue"] == 0 and type(x["days_overdue"]) is int
    assert all(type(y["days_overdue"]) is int for y in asyncio.run(DV.hutang(None, "t", HARI, _rows()))["terbuka"])
    # bentuk = due_7d + days_overdue (kontrak FE CONVERSATIONAL)
    r = asyncio.run(DV.hutang(None, "t", HARI, _rows()))
    assert set(r["terbuka"][0]) == set(r["due_7d"][0]) | {"days_overdue"} and type(r["terbuka_count"]) is int
