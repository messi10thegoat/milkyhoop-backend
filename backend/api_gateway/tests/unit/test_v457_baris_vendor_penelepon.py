"""V457 (23 Okt 2026): compute_ap_outstanding kini punya baris tingkat VENDOR tanpa tagihan (bill_id NULL) -- 'OPENING-BALANCE'
(saldo awal vendor, bisa POSITIF) dan 'VENDOR-PAYMENT' (pembayaran yatim, negatif) -- di samping 'VENDOR-CREDIT' (V441).
Audit penelepon (permintaan MASTER): baris itu BENAR ikut TOTAL/saldo vendor (== GL hutang), tapi BUKAN tagihan: tak boleh muncul
di pemilih tagihan / daftar tagihan / kandidat pencocokan / kartu tugas bayar, dan tak boleh menambah hitungan tagihan.
Pemindai: setiap literal SQL yang membaca compute_ap_outstanding/compute_vendor_ap dengan saringan `outstanding > 0` (= daftar
tagihan terbuka) WAJIB menyebut bill_id, kecuali pengecualian bernama di bawah."""
import asyncio
import os
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

os.environ.setdefault("OPENAI_API_KEY", "boneka")

from app.services import dashboard_v2 as DV  # noqa: E402

H = date(2026, 10, 23)
APP = Path(__file__).resolve().parents[2] / "app"
V = UUID(int=7)

# Jawaban bot "utang ke vendor X" = SALDO vendor -> saldo awal positif memang utang (tanpa ini bot bilang "tak punya utang").
PENGECUALIAN = {("services/unified_agent/orchestrator.py", "SELECT bill_number AS doc_number, due_date, outstanding")}


def _bill(no, due, sisa, bill_id=True):
    return {"bill_id": UUID(int=abs(hash(no)) % (1 << 64)) if bill_id else None, "bill_number": no, "vendor_id": V,
            "vendor_name": "Toko Kain", "due_date": due, "outstanding": Decimal(str(sisa))}


def test_hutang_saldo_awal_positif_ikut_total_bukan_daftar_tagihan():
    rows = [_bill("B-1", H + timedelta(days=2), 500), _bill("OPENING-BALANCE", H, 1000, bill_id=False),
            _bill("VENDOR-PAYMENT", H, -300, bill_id=False)]
    out = asyncio.run(DV.hutang(None, "t", H, rows))
    assert out["total"] == "1200.00"  # == GL: semua baris
    assert out["vendor_count"] == 1 and out["overdue_count"] == 0
    assert [x["number"] for x in out["due_7d"]] == ["B-1"]


def test_tugas_bayar_hanya_tagihan():
    t = DV.susun_tugas(hari_ini=H, ar_rows=[], ap_rows=[_bill("OPENING-BALANCE", H, 1000, bill_id=False),
                                                         _bill("B-1", H, 200)],
                       so_kirim_rows=[], so_dp_rows=[], rekon_rows=[], kontak={}, rekening=None, usaha="u")
    kartu = [k for k in t if k["type"] == "ap_due"]
    assert [k["refs"][0]["number"] for k in kartu] == ["B-1"]
    assert all(k["refs"][0]["id"] != "None" for k in kartu)


def _literal_sql():
    for p in APP.rglob("*.py"):
        teks = p.read_text()
        for m in re.finditer(r'("""|\'\'\')(.*?)\1', teks, re.S):
            sql = m.group(2)
            if re.search(r"compute_ap_outstanding|compute_vendor_ap", sql):
                yield str(p.relative_to(APP)), sql


def test_pemindai_daftar_tagihan_terbuka_wajib_saring_bill_id():
    bocor = []
    for f, sql in _literal_sql():
        if not re.search(r"\boutstanding\s*>\s*0", sql) or "bill_id" in sql:
            continue
        if any(f == pf and kunci in sql for pf, kunci in PENGECUALIAN):
            continue
        bocor.append((f, " ".join(sql.split())[:120]))
    assert not bocor, f"daftar tagihan terbuka tanpa saringan bill_id (baris vendor V457 bocor): {bocor}"


def test_pemindai_tak_kosong_dan_kontrol_merah():
    semua = list(_literal_sql())
    assert len(semua) >= 15  # alat ukur benar-benar menemukan penelepon
    palsu = "SELECT bill_number FROM compute_ap_outstanding($1) WHERE outstanding > 0"
    assert re.search(r"\boutstanding\s*>\s*0", palsu) and "bill_id" not in palsu  # pola yang ditangkap


def test_paid_count_not_in_tahan_null():
    teks = (APP / "services/bills_service.py").read_text()
    assert "id NOT IN (SELECT bill_id FROM ap)" not in teks  # NOT IN + NULL (baris vendor) = selalu 0
    assert "id NOT IN (SELECT bill_id FROM ap WHERE bill_id IS NOT NULL)" in teks
