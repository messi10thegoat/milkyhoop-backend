"""B0 (5 Nov 2026; putusan pemilik "saldo awal pelanggan = FAKTUR PEMBUKA", cermin tagihan pembuka vendor V458; desain disetujui MASTER):
POST /customers/{id}/opening-balance -> faktur pembuka (sales_invoices.is_opening_balance, V478) diterbitkan lewat _internal_post_invoice
YANG SAMA dengan cabang PEMBUKA (Cr Modal Saldo Awal, tanpa pendapatan/SJ/HPP) -> masuk compute_ar (umur, Terima pembayaran). Bukan
penjualan: disaring dari total penjualan pelanggan, ringkas omzet chat, daftar faktur harian dashboard. Badan mobile diterima."""
import ast
import uuid
from decimal import Decimal as D
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.routers import customers as RC
from app.services import faktur_terbit_rencana as FTR

APP = Path(FTR.__file__).resolve().parents[1]
MIG = APP.parents[1] / "migrations" / "V478__faktur_saldo_awal_pelanggan.sql"


def _fungsi(rel, nama):
    pohon = ast.parse((APP / rel).read_text())
    return ast.unparse(next(n for n in ast.walk(pohon) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nama))


def test_baris_jv_pembuka_kredit_modal_saldo_awal_label():
    b = FTR.baris_jv(D("500000"), 0, 0, "AR", "MODAL", None, None, "INV-1", FTR.LABEL_PEMBUKA)
    assert [(x["akun"], x["debit"], x["kredit"], x["memo"]) for x in b] == [
        ("AR", D("500000"), 0, "Piutang - INV-1"), ("MODAL", 0, D("500000"), "Modal Saldo Awal - INV-1")]
    assert FTR.baris_jv(D("1"), 0, 0, "AR", "DM", None, None, "INV-2")[1]["memo"] == "Pendapatan Diterima Dimuka - INV-2"  # biasa tak berubah


def test_penulis_satu_cabang_pembuka_kembali_sebelum_alokasi_dan_ekor_tanpa_salinan():
    src = (APP / "routers/sales_invoices.py").read_text()
    w = _fungsi("routers/sales_invoices.py", "_internal_post_invoice")
    assert "AccountRole.EQUITY_OPENING_BALANCE" in w and "is_opening_balance" in w
    i_return = w.index("if pembuka:")
    assert i_return < w.index("FTR.alokasi_baris(") and i_return < w.index("alokasikan_saat_faktur")
    assert w.count("await _tandai_terposting(") == 2 and src.count("UPDATE sales_invoices\n        SET status = 'posted'") == 1


def test_pratinjau_cabang_pembuka_sama():
    r = _fungsi("services/faktur_terbit_rencana.py", "rencana_terbit_ro")
    assert "EQUITY_OPENING_BALANCE" in r and "CABANG_PEMBUKA" in r and "LABEL_PEMBUKA" in r


def _req(user=True):
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": "t", "user_id": str(uuid.uuid4()), "role": "OWNER"}
    return r


@pytest.mark.asyncio
@pytest.mark.parametrize("badan,kata", [
    ({"amount": 100, "ar_opening_balance": 200}, "berbeda"),
    ({"ar_opening_balance": 100, "deposit_opening_balance": 5}, "uang muka"),
    ({"ar_opening_balance": 0}, "positive")])
async def test_badan_ditolak_berkalimat_sebelum_db(monkeypatch, badan, kata):
    async def tak_boleh(*a, **k):
        raise AssertionError("DB disentuh")
    monkeypatch.setattr(RC, "get_pool", tak_boleh)
    with pytest.raises(HTTPException) as e:
        await RC.set_customer_opening_balance(_req(), uuid.uuid4(), badan)
    assert e.value.status_code == 400 and kata in str(e.value.detail)


def test_penulis_pelanggan_cermin_vendor_idem_kunci_409_lalu_penulis_faktur_sama():
    f = _fungsi("routers/customers.py", "set_customer_opening_balance")
    i = [f.index(x) for x in ("_ib.mulai(", "CUSTOMER_OPENING:", "saldo_awal_pelanggan(", "INSERT INTO sales_invoices", "_internal_post_invoice(")]
    assert i == sorted(i) and "is_opening_balance" in f and "'OPENING'" not in f  # jurnal lepas OPENING lama tak lagi ditulis
    assert "source_type = 'OPENING'" in _fungsi("routers/customers.py", "saldo_awal_pelanggan")  # jurnal lama belum dibalik ikut menahan


def test_faktur_pembuka_bukan_penjualan():
    for rel, n in (("services/pelanggan_penjualan.py", 2), ("services/unified_agent/customer_sales.py", 2), ("routers/dashboard.py", 1)):
        assert (APP / rel).read_text().count("NOT si.is_opening_balance") == n, rel


def test_v478_kolom_bawaan_false_dan_satu_pembuka_aktif():
    s = MIG.read_text()
    assert "ADD COLUMN IF NOT EXISTS is_opening_balance boolean NOT NULL DEFAULT false" in s
    assert "UNIQUE INDEX IF NOT EXISTS uq_si_saldo_awal_pelanggan ON sales_invoices (tenant_id, customer_id) WHERE is_opening_balance AND status <> 'void'" in s
