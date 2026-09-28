"""D3 dashboard v2 (28 Sep 2026): tugas per jenis (fixture), ringkasan satu-sumber, kontrak JSON, rute.

Kontrak = salinan contracts/*.schema.json paket dashboard-sidebar (tests/unit/data/dashboard_v2/),
divalidasi dengan validator mini di bawah (image tak memuat jsonschema) — validator itu sendiri
dibuktikan BISA MERAH (test_validator_bisa_merah, Law 33).
"""
import inspect
import json
import os
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.services import dashboard_v2 as DV  # noqa: E402
from app.routers import dashboard_v2 as RV  # noqa: E402
from tests.unit.kontrak_json import _cocok, _skema  # noqa: E402

H = date(2026, 9, 28)  # Senin
DATA = Path(__file__).parent / "data" / "dashboard_v2"
C1, C2, C3 = (UUID(f"c000000{i}-0000-0000-0000-000000000000") for i in range(1, 4))


def _inv(no, due, sisa, cust=C1, nama="Budi"):
    return {"invoice_id": UUID(int=abs(hash(no)) % (1 << 64)), "invoice_number": no, "customer_id": cust,
            "customer_name": nama, "due_date": due, "outstanding": Decimal(str(sisa))}


def _bill(no, due, sisa):
    return {"bill_id": UUID(int=abs(hash(no)) % (1 << 64)), "bill_number": no, "vendor_id": C3,
            "vendor_name": "Toko Kain", "due_date": due, "outstanding": Decimal(str(sisa))}


def _susun(**kw):
    dasar = dict(hari_ini=H, ar_rows=[], ap_rows=[], so_kirim_rows=[], so_dp_rows=[], rekon_rows=[],
                 kontak={str(C1): {"nama": "Budi", "wa": "6281234567890"}, str(C2): {"nama": "Sari", "wa": None}},
                 rekening="BCA 1234567890", usaha="Grapgrap Clothing")
    dasar.update(kw)
    return DV.susun_tugas(**dasar)


def _per_jenis(tugas, jenis):
    return [t for t in tugas if t["type"] == jenis]


# ───────── tugas per jenis (06-GERBANG-UJI §B) ─────────

def test_jatuh_tempo_hari_ini_satu_kartu_per_faktur_lajur_now():
    t = _susun(ar_rows=[_inv("INV-1", H, 945000), _inv("INV-2", H, 100000, C2, "Sari")])
    kartu = _per_jenis(t, "ar_due_today")
    assert [k["amount"] for k in kartu] == ["945000.00", "100000.00"]  # nominal terbesar dulu
    assert all(k["lane"] == "now" for k in kartu)
    assert kartu[0]["key"] == "ar_due_today:INV-1:2026-09-28:945000.00"
    assert kartu[0]["actions"][0]["kind"] == "wa_remind" and kartu[0]["actions"][0]["primary"]
    assert kartu[0]["actions"][1]["kind"] == "record_payment"
    wa = kartu[0]["wa_targets"][0]
    assert wa["phone"] == "6281234567890" and "INV-1" in wa["message"] and "Rp 945.000" in wa["message"]
    assert "jatuh tempo hari ini" in wa["message"] and "BCA 1234567890" in wa["message"]
    assert kartu[1]["wa_targets"][0]["phone"] is None  # nomor kosong -> FE "Lengkapi nomor"


def test_telat_digabung_satu_kartu_sebut_maks_tiga_nama():
    ar = [_inv("INV-A", H - timedelta(days=3), 1000, C1, "Andi"), _inv("INV-B", H - timedelta(days=10), 2000, C2, "Bela"),
          _inv("INV-C", H - timedelta(days=1), 3000, C3, "Caca"), _inv("INV-D", H - timedelta(days=2), 4000, C3, "Dodi")]
    kartu = _per_jenis(_susun(ar_rows=ar), "ar_overdue")
    assert len(kartu) == 1
    k = kartu[0]
    assert k["lane"] == "now" and k["amount"] == "10000.00" and k["title_html"] == "<b>4 faktur telat</b>"
    assert k["subtitle"] == "Bela, Andi, Dodi +1 lainnya"
    assert "lewat jatuh tempo 10 hari" in [w for w in k["wa_targets"] if w["name"] == "Sari"][0]["message"]


def test_besok_per_faktur_dan_tiga_hari_digabung_per_hari_lajur_week():
    ar = [_inv("INV-T", H + timedelta(days=1), 500), _inv("INV-3a", H + timedelta(days=3), 700, C2, "Sari"),
          _inv("INV-3b", H + timedelta(days=3), 300, C3, "Caca"), _inv("INV-9", H + timedelta(days=8), 999)]
    kartu = _per_jenis(_susun(ar_rows=ar), "ar_due_soon")
    assert [k["key"] for k in kartu] == ["ar_due_soon:2026-10-01:2:1000.00", "ar_due_soon:INV-T:2026-09-29:500.00"]
    assert all(k["lane"] == "week" for k in kartu)
    assert kartu[0]["title_html"] == "<b>2 faktur</b> jatuh tempo Kamis, 1 Okt"
    assert kartu[1]["subtitle"] == "Jatuh tempo besok"
    # > 7 hari (H+8): bukan tugas
    assert not any("INV-9" in json.dumps(k) for k in kartu)


def test_faktur_lunas_tak_menjadi_tugas_dan_kunci_berubah_bila_nominal_berubah():
    assert _susun(ar_rows=[_inv("INV-1", H, 0)]) == []
    k1 = _susun(ar_rows=[_inv("INV-1", H, 945000)])[0]["key"]
    k2 = _susun(ar_rows=[_inv("INV-1", H, 500000)])[0]["key"]
    assert k1 != k2  # dibayar sebagian -> tugas yang di-"selesai"-kan muncul lagi


def test_hutang_lajur_now_bila_hari_ini_atau_telat_week_bila_dekat():
    ap = [_bill("B-1", H - timedelta(days=2), 100), _bill("B-2", H, 200), _bill("B-3", H + timedelta(days=5), 300),
          _bill("B-4", H + timedelta(days=8), 400)]
    kartu = _per_jenis(_susun(ap_rows=ap), "ap_due")
    assert {k["refs"][0]["number"]: k["lane"] for k in kartu} == {"B-1": "now", "B-2": "now", "B-3": "week"}
    assert [k for k in kartu if k["refs"][0]["number"] == "B-1"][0]["subtitle"] == "Telat 2 hari"
    assert kartu[0]["actions"][0]["kind"] == "record_bill_payment"


def test_so_harus_kirim_dan_dp_belum_dan_rekon():
    so = [{"id": UUID(int=1), "order_number": "SO-2", "expected_ship_date": H + timedelta(days=2)},
          {"id": UUID(int=2), "order_number": "SO-1", "expected_ship_date": H - timedelta(days=1)}]
    dp = [{"id": UUID(int=3), "order_number": "SO-9", "dp_wajib": Decimal("250000")}]
    rk = [{"id": UUID(int=4), "account_name": "BCA Operasional"}]
    t = _susun(so_kirim_rows=so, so_dp_rows=dp, rekon_rows=rk)
    kirim, = _per_jenis(t, "so_to_ship")
    assert kirim["lane"] == "week" and kirim["amount"] is None
    assert kirim["title_html"] == "<b>2 pesanan</b> harus dikirim"
    assert kirim["subtitle"] == "SO-1, SO-2 · 1 lewat tanggal kirim"
    assert kirim["actions"][0]["kind"] == "prepare_shipment"
    dpk, = _per_jenis(t, "so_dp_pending")
    assert dpk["lane"] == "any" and dpk["amount"] == "250000.00" and dpk["title_html"] == "<b>1 DP</b> belum diterima"
    rek, = _per_jenis(t, "bank_recon")
    assert rek["lane"] == "any" and rek["key"] == f"bank_recon:{UUID(int=4)}:2026-08"
    assert rek["title_html"] == "<b>Rekonsiliasi bank</b> Agustus 2026"


def test_urutan_lajur_lalu_nominal_terbesar():
    t = _susun(ar_rows=[_inv("INV-S", H + timedelta(days=1), 9_000_000), _inv("INV-H", H, 1000),
                        _inv("INV-L", H - timedelta(days=1), 5000)],
               so_dp_rows=[{"id": UUID(int=3), "order_number": "SO-9", "dp_wajib": Decimal("99999999")}])
    assert [x["lane"] for x in t] == ["now", "now", "week", "any"]
    assert [x["type"] for x in t[:2]] == ["ar_overdue", "ar_due_today"]  # 5000 > 1000


def test_judul_html_hanya_b_dan_nama_di_escape():
    t = _susun(ar_rows=[_inv("INV-<x>", H, 10, C1, "<script>a</script>")])
    judul = t[0]["title_html"]
    assert "<script>" not in judul and re.sub(r"</?b>", "", judul).count("<") == 0


def test_rangkum_dismiss_per_pengguna_sum_now_dan_total_today():
    t = _susun(ar_rows=[_inv("INV-1", H, 945000), _inv("INV-2", H - timedelta(days=4), 5000)],
               ap_rows=[_bill("B-9", H + timedelta(days=3), 77)])
    kunci = [x["key"] for x in t if x["type"] == "ar_due_today"][0]
    u1 = DV.rangkum_tugas(t, {kunci}, 1, "2026-09-28T09:00:00+07:00")
    u2 = DV.rangkum_tugas(t, set(), 0, "2026-09-28T09:00:00+07:00")
    assert kunci not in [x["key"] for x in u1["tasks"]] and kunci in [x["key"] for x in u2["tasks"]]
    assert u1["sum_now"] == "5000.00" and u2["sum_now"] == "950000.00"  # hanya lajur now
    assert u1["total_today"] == 1 + len(u1["tasks"])


# ───────── utilitas ─────────

@pytest.mark.parametrize("masuk,keluar", [
    (("0812-3456-7890",), "6281234567890"), (("+62 812 3456 7890",), "6281234567890"),
    (("81234567890",), "6281234567890"), ((None, "", "0812345678"), "62812345678"), (("12",), None),
])
def test_normalisasi_wa(masuk, keluar):
    assert DV.normalisasi_wa(*masuk) == keluar


def test_rentang_periode_kalender_penuh_minggu_senin():
    assert DV.rentang_periode("week", date(2026, 10, 1)) == (date(2026, 9, 28), date(2026, 10, 4))
    assert DV.rentang_periode("month", H) == (date(2026, 9, 1), date(2026, 9, 30))
    assert DV.rentang_periode("year", H) == (date(2026, 1, 1), date(2026, 12, 31))
    assert DV.rentang_periode("day", H) == (H, H)


def test_ember_tahun_data_kurang_tiga_bulan_per_minggu_bukan_jan_des():
    unit, e = DV.ember_masa_lalu("year", H, date(2026, 8, 20))
    assert unit == "week" and e[0][1] == date(2026, 8, 17) and e[-1][2] == H
    unit, e = DV.ember_masa_lalu("year", H, date(2025, 1, 1))
    assert unit == "month" and [x[0] for x in e][:2] == ["Jan", "Feb"] and len(e) == 9


def test_ember_bulan_per_minggu_sampai_hari_ini_dan_perkiraan_empat():
    unit, e = DV.ember_masa_lalu("month", H, None)
    assert unit == "week" and [(a.day, b.day) for _, a, b in e] == [(1, 7), (8, 14), (15, 21), (22, 28)]
    p = DV.ember_perkiraan("week", H)
    assert len(p) == 4 and p[0][1] == H + timedelta(days=1)


# ───────── ringkasan: sumber sama dengan laporan ─────────

class _Conn:
    def __init__(self, meta):
        self.meta = meta
        self.q = []

    async def fetch(self, sql, *a):
        self.q.append(sql)
        if "FROM chart_of_accounts c" in sql:
            return self.meta
        if "FROM chart_of_accounts WHERE" in sql:
            return []
        raise AssertionError(sql[:80])


async def test_kas_bank_dipecah_dari_rekening_bukan_kategori(monkeypatch):
    # regresi D0: 'Toko Manado' category NULL + rekening petty_cash -> KAS (dashboard lama: bank)
    rinci = [{"account_code": "1-10201", "account_name": "BCA", "balance": Decimal("133040294")},
             {"account_code": "1-10202", "account_name": "BCA Keluar", "balance": Decimal("1366900")},
             {"account_code": "1-10203", "account_name": "Kas Operasional", "balance": Decimal("286500")},
             {"account_code": "1-10204", "account_name": "Toko  Manado", "balance": Decimal("261000")}]
    meta = [{"id": UUID(int=i), "account_code": r["account_code"], "category": c, "jenis_rek": j}
            for i, (r, c, j) in enumerate(zip(rinci, ["bank", "bank", "kas", None], ["bank", "bank", "petty_cash", "petty_cash"]))]

    async def cb(conn, t, f, start_date=None, end_date=None):
        assert f == {"is_cash": True} and end_date == H and start_date is None
        return Decimal("134954694")

    async def cbd(conn, t, f, start_date=None, end_date=None):
        assert f == {"is_cash": True} and end_date == H
        return rinci

    monkeypatch.setattr(DV, "compute_balance", cb)
    monkeypatch.setattr(DV, "compute_balance_detail", cbd)
    k = await DV.kas_bank(_Conn(meta), "t", H)
    assert (k["total"], k["cash_total"], k["bank_total"]) == ("134954694.00", "547500.00", "134407194.00")
    monkeypatch.setattr(DV, "compute_balance", lambda *a, **k: _async(Decimal("1")))
    with pytest.raises(RuntimeError):
        await DV.kas_bank(_Conn(meta), "t", H)


async def _async(v):
    return v


async def test_laba_rugi_rumus_laporan_dan_lainnya_menjumlah_total(monkeypatch):
    saldo = {"REVENUE": Decimal("-1000000"), "OTHER_INCOME": Decimal("-50000")}
    beban = {"COGS": [("5-1", 300000), ("5-2", -10000)],
             "EXPENSE": [(f"6-{i}", 10000 * i) for i in range(1, 8)], "OTHER_EXPENSE": [("8-1", 5000)]}
    dipanggil = []

    async def cb(conn, t, f, start_date=None, end_date=None):
        dipanggil.append(f["account_type"])
        return saldo[f["account_type"]]

    async def cbd(conn, t, f, start_date=None, end_date=None):
        assert (start_date, end_date) == (date(2026, 9, 1), date(2026, 9, 30))
        return [{"account_code": k, "account_name": k, "balance": Decimal(v)} for k, v in beban[f["account_type"]]]

    monkeypatch.setattr(DV, "compute_balance", cb)
    monkeypatch.setattr(DV, "compute_balance_detail", cbd)
    pnl, ex = await DV.laba_rugi_dan_beban(_Conn([]), "t", date(2026, 9, 1), date(2026, 9, 30))
    total_beban = 300000 - 10000 + sum(10000 * i for i in range(1, 8)) + 5000
    assert pnl["revenue"] == "1050000.00"  # pendapatan lain IKUT (laba_bersih laporan)
    assert pnl["expense"] == f"{total_beban}.00" and pnl["profit"] == f"{1050000 - total_beban}.00"
    assert len(ex["categories"]) == 7 and ex["categories"][-1]["name"] == "Lainnya"
    assert sum(Decimal(c["amount"]) for c in ex["categories"]) == Decimal(total_beban)
    assert ex["categories"][0]["name"] == "5-1"  # HPP ikut sebagai biaya (baris L/R), terbesar dulu
    assert set(dipanggil) == {"REVENUE", "OTHER_INCOME"}


async def test_piutang_ember_sama_dengan_umur_piutang():
    ar = [_inv("A", H, 100), _inv("B", H - timedelta(days=1), 200), _inv("C", None, 50), _inv("D", H + timedelta(days=30), 400)]
    p = await DV.piutang(None, "t", H, ar)
    # 'current' laporan umur = hari_ini <= due; due NULL -> ELSE '90+' (telat)
    assert (p["total"], p["not_due"], p["overdue_total"], p["overdue_count"]) == ("750.00", "500.00", "250.00", 2)
    assert [x["number"] for x in p["due_7d"]] == ["C", "B", "A"]


# ───────── kontrak JSON ─────────

def test_tugas_valid_terhadap_kontrak():
    t = _susun(ar_rows=[_inv("INV-1", H, 945000), _inv("INV-2", H - timedelta(days=2), 5000),
                        _inv("INV-3", H + timedelta(days=1), 10), _inv("INV-4", H + timedelta(days=3), 10)],
               ap_rows=[_bill("B-1", H, 1)],
               so_kirim_rows=[{"id": UUID(int=1), "order_number": "SO-1", "expected_ship_date": H}],
               so_dp_rows=[{"id": UUID(int=3), "order_number": "SO-9", "dp_wajib": Decimal("1")}],
               rekon_rows=[{"id": UUID(int=4), "account_name": "BCA"}])
    badan = json.loads(json.dumps(DV.rangkum_tugas(t, set(), 0, "2026-09-28T09:00:00+07:00")))
    assert {x["type"] for x in badan["tasks"]} == set(DV.JENIS_TUGAS)  # semua 7 jenis tercakup
    s = _skema("dashboard-tasks.schema.json")
    assert _cocok(badan, s, s) == []


async def test_ringkasan_valid_terhadap_kontrak(monkeypatch):
    async def cb(conn, t, f, start_date=None, end_date=None):
        return {"is_cash": Decimal("300"), "REVENUE": Decimal("-1000"), "OTHER_INCOME": NOL}.get(
            "is_cash" if "is_cash" in f else f["account_type"])
    NOL = Decimal("0")

    async def cbd(conn, t, f, start_date=None, end_date=None):
        if "is_cash" in f:
            return [{"account_code": "1-1", "account_name": "BCA", "balance": Decimal("300")}]
        return [{"account_code": "6-1", "account_name": "Listrik", "balance": Decimal("250.5")}] if f["account_type"] == "EXPENSE" else []

    class C:
        async def fetch(self, sql, *a):
            if "compute_ar_outstanding" in sql:
                return [_inv("INV-1", H, 100)]
            if "compute_ap_outstanding" in sql:
                return [_bill("B-1", H + timedelta(days=40), 50)]
            if "FROM chart_of_accounts c" in sql:
                return [{"id": UUID(int=9), "account_code": "1-1", "category": None, "jenis_rek": "bank"}]
            if "FROM chart_of_accounts WHERE" in sql:
                return [{"id": UUID(int=8), "account_code": "6-1"}]
            if "WITH neto" in sql:
                return [{"tgl": date(2026, 9, 3), "masuk": Decimal("500"), "keluar": Decimal("0")}]
            raise AssertionError(sql[:60])

        async def fetchval(self, sql, *a):
            if "MIN(journal_date)" in sql:
                return date(2025, 1, 1)
            return Decimal("280")

    monkeypatch.setattr(DV, "compute_balance", cb)
    monkeypatch.setattr(DV, "compute_balance_detail", cbd)
    for per in DV.PERIODE:
        badan = json.loads(json.dumps(await DV.ringkasan(C(), "t", per, H, "Asia/Jakarta", "2026-09-28T09:00:00+07:00")))
        s = _skema("dashboard-summary.schema.json")
        assert _cocok(badan, s, s) == [], per
        assert badan["cashflow"]["buckets"][-1]["is_projection"] is True


def test_validator_bisa_merah():
    s = _skema("dashboard-tasks.schema.json")
    rusak = {"as_of": "x", "sum_now": "1", "done_today": "1", "total_today": 0,
             "tasks": [{"key": "k", "type": "ar_lain", "lane": "now", "title_html": "", "subtitle": "", "actions": []}]}
    g = _cocok(rusak, s, s)
    assert any("done_today" in x for x in g) and any("ar_lain" in x for x in g)
    s2 = _skema("dashboard-summary.schema.json")
    assert _cocok(12.5, {"$ref": "#/$defs/money"}, s2)  # angka float, bukan string Decimal
    assert _cocok("12.345", {"$ref": "#/$defs/money"}, s2)  # 3 desimal
    assert _cocok("12.34", {"$ref": "#/$defs/money"}, s2) == []


# ───────── rute & SQL (tes RUTE, bukan hanya fungsi) ─────────

def test_rute_terdaftar_ke_fungsi_yang_benar_dan_dipasang_di_main():
    peta = {(r.path, tuple(sorted(r.methods))): r.endpoint.__name__ for r in RV.router.routes}
    assert peta[("/v2/summary", ("GET",))] == "ringkasan_v2"
    assert peta[("/tasks", ("GET",))] == "tugas"
    assert peta[("/tasks/{key:path}/dismiss", ("POST",))] == "tandai_selesai"
    assert peta[("/tasks/{key:path}/dismiss", ("DELETE",))] == "urungkan_selesai"
    main = (Path(RV.__file__).parents[1] / "main.py").read_text()
    assert re.search(r'include_router\(dashboard_v2\.router, prefix="/api/dashboard"', main)
    # rute LAMA /summary tetap milik dashboard lama (flag OFF identik)
    from app.routers import dashboard as LAMA
    assert any(r.path == "/summary" for r in LAMA.router.routes)


def test_sql_tugas_tenant_eksplisit_dan_tanpa_quantity_shipped():
    kirim = inspect.getsource(DV.so_harus_kirim)
    assert "quantity_shipped" not in kirim and "COALESCE(soi.perlu_kirim, p.track_inventory, false)" in kirim
    assert "terkirim_per_baris" in kirim and "so.tenant_id = $1" in kirim
    dp = inspect.getsource(DV.so_dp_belum)
    assert "COALESCE(cd.sales_order_id, pf.sales_order_id) = so.id" in dp and "cd.tenant_id = so.tenant_id" in dp
    assert "GROUP BY je.id" in DV.SQL_KAS_PER_HARI  # neto PER JURNAL (transfer internal = 0), bukan bruto
    rek = inspect.getsource(DV.rekening_belum_rekon)
    assert "if not pernah" in rek  # putusan pemilik: hanya bila pernah rekonsiliasi
    for fn in (RV.tandai_selesai, RV.urungkan_selesai, RV._dismissed):
        src = inspect.getsource(fn)
        assert "tenant_id = $1" in src or "VALUES ($1, $2, $3" in src
        assert "user_id" in src


def test_ringkasan_tidak_memakai_get_by_basis_atau_total_minus_paid():
    src = inspect.getsource(DV)
    assert "get_revenue_by_basis" not in src.split('"""', 2)[2]
    assert "paid_amount" not in src and "amount_paid" not in src


@pytest.mark.parametrize("kunci,ok", [("ar_due_today:INV-1:2026-09-28:945000.00", True),
                                      ("bank_recon:abc:2026-08", True), ("hapus:semua", False), ("", False),
                                      ("ar_overdue:" + "x" * 400, False)])
def test_kunci_dismiss_divalidasi(kunci, ok):
    if ok:
        assert RV._kunci_sah(kunci) == kunci
    else:
        with pytest.raises(HTTPException) as e:
            RV._kunci_sah(kunci)
        assert e.value.status_code == 422


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(s):
                return pool.conn

            async def __aexit__(s, *a):
                return False
        return _Ctx()


async def test_rute_tugas_saring_izin_dan_dismiss_per_pengguna(monkeypatch):
    from zoneinfo import ZoneInfo
    tugas = _susun(ar_rows=[_inv("INV-1", H, 10)], so_dp_rows=[{"id": UUID(int=3), "order_number": "SO-9", "dp_wajib": Decimal("5")}])
    kunci = tugas[0]["key"]

    class C:
        def __init__(self):
            self.q = []

        async def fetch(self, sql, *a):
            self.q.append((sql, a))
            assert "dashboard_task_state" in sql and "tenant_id = $1 AND user_id = $2" in sql
            assert a[0] == "t" and a[1] == "u1"
            return [{"task_key": kunci, "hari_ini": True}]

    conn = C()

    async def _pool():
        return _Pool(conn)

    async def _zona(c, t):
        return ZoneInfo("Asia/Jakarta")

    async def _tgl(c, t):
        return H

    async def _cache(key, fn, ttl, stale_ttl):
        assert key == "dashboard:v2tasks:t:2026-09-28" and ttl == 60
        return json.loads(json.dumps(tugas))

    async def _boleh(req, modul):
        return modul != ("sales_order",)

    monkeypatch.setattr(RV, "get_db_pool", _pool)
    monkeypatch.setattr(RV, "zona_tenant", _zona)
    monkeypatch.setattr(RV, "tanggal_dokumen", _tgl)
    monkeypatch.setattr(RV, "cached_fetch", _cache)
    monkeypatch.setattr(RV, "boleh_semua", _boleh)
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": "t", "user_id": "u1"}))
    badan = await RV.tugas(req)
    assert badan["tasks"] == []  # ar_due_today di-dismiss; so_dp_pending tanpa izin sales_order
    assert badan["done_today"] == 1 and badan["total_today"] == 1


async def test_rute_dismiss_tulis_dan_hapus_milik_pengguna_sendiri(monkeypatch):
    q = []

    class C:
        async def execute(self, sql, *a):
            q.append((sql, a))

    async def _pool():
        return _Pool(C())

    monkeypatch.setattr(RV, "get_db_pool", _pool)
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": "t", "user_id": "u1"}))
    k = "ar_due_today:INV-1:2026-09-28:945000.00"
    assert (await RV.tandai_selesai(req, k)) == {"key": k, "state": "dismissed"}
    assert (await RV.urungkan_selesai(req, k)) == {"key": k, "state": None}
    ins, dele = q
    assert "ON CONFLICT (tenant_id, user_id, task_key) DO NOTHING" in ins[0] and ins[1] == ("t", "u1", k)
    assert "tenant_id = $1 AND user_id = $2 AND task_key = $3" in dele[0] and dele[1] == ("t", "u1", k)
    with pytest.raises(HTTPException):
        await RV.tandai_selesai(SimpleNamespace(state=SimpleNamespace(user={"tenant_id": "t"})), k)


def test_pesan_wa_rekening_faktur_dengan_pemilik_seperti_pdf():
    f = _inv("INV-W", H, 945000)
    rek = {str(f["invoice_id"]): DV.teks_rekening("BCA", "1234567890", "BCA Anthonius Iwan Adhipraja")}
    assert rek[str(f["invoice_id"])] == "BCA 1234567890 a.n. Anthonius Iwan Adhipraja"  # awalan bank dibuang
    pesan = _susun(ar_rows=[f], rek_faktur=rek)[0]["wa_targets"][0]["message"]
    assert "Pembayaran ke BCA 1234567890 a.n. Anthonius Iwan Adhipraja." in pesan
    # faktur tanpa rekening -> cadangan rekening tenant
    assert "Pembayaran ke BCA 1234567890." in _susun(ar_rows=[f])[0]["wa_targets"][0]["message"]
    assert DV.teks_rekening(None, None, "X") is None and DV.teks_rekening("BCA", "1", None) == "BCA 1"
    src = inspect.getsource(DV.teks_rekening)
    assert "faktur_cetak.pemilik_rekening" in src  # SATU sumber dengan PDF faktur
    assert "sales_invoices" in inspect.getsource(DV.rekening_per_faktur) and "tenant_id = $1" in inspect.getsource(DV.rekening_per_faktur)
