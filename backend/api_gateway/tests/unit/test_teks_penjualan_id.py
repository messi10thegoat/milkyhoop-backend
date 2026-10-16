"""TEKS-PENJUALAN-ID (16 Okt 2026): sisa teks Inggris di skema/router Penjualan. FE membuang awalan "Value error, "
(validationErrorMessage.ts) dan menampilkan sisanya; tak ada pencocokan teks FE ke kalimat-kalimat ini."""
import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
SKEMA = ["credit_notes", "customer_deposits", "quotes", "sales_orders", "sales_invoices", "receive_payments"]
LAMA = ["Description is required", "Customer name is required", "Expiry date cannot be before quote date",
        "At least one item is required", "recognize_at must be", "source_deposit_id is required", "source_deposit_id must be null"]


def test_valueerror_skema_penjualan_indonesia():
    for nama in SKEMA:
        src = (APP / "schemas" / f"{nama}.py").read_text()
        for lama in LAMA:
            assert lama not in src, (nama, lama)


def test_pesan_baru_ada_dan_nilai_validator_tak_berubah():
    from pydantic import ValidationError
    from app.schemas.quotes import QuoteItemCreate
    try:
        QuoteItemCreate(description="  ", quantity=1, unit_price=1)
        assert False, "harus ditolak"
    except ValidationError as e:
        assert "Keterangan wajib diisi." in str(e)


def test_struk_penjualan_detail_indonesia_status_tetap():
    tree = ast.parse((APP / "routers" / "sales_receipts.py").read_text())
    hasil = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "HTTPException":
            kw = {k.arg: k.value for k in n.keywords}
            if isinstance(kw.get("detail"), ast.Constant):
                hasil.setdefault(kw["detail"].value, set()).add(kw["status_code"].value)
    assert hasil["Struk penjualan tidak ditemukan."] == {404}
    assert hasil["Jumlah diterima kurang dari total."] == {400}
    assert hasil["Struk sudah dibatalkan."] == {400}
    for inggris in ("Sales receipt not found", "Amount received is less than total", "Receipt already voided"):
        assert inggris not in hasil
