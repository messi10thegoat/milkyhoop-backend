"""Pesan galat Penjualan berbahasa Indonesia (MASTER 4 Okt 2026) -- helper services/teks_galat + penjaga AST.

FE CW menampilkan `detail` (string) / `detail.message` (dict) APA ADANYA. Penjaga di sini memastikan detail 4xx
(selain 401) di modul Penjualan tidak berbahasa Inggris dan angka uang/qty selalu lewat tg.rp/tg.qty.
Di luar cakupan (sengaja): 401, pesan sukses 200, 422 Pydantic; credit_notes.py + deliveries.py menyusul
sesudah Unit D BACKEND2 (U5) merge.
"""
import ast
import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.services import teks_galat as tg

APP = Path(tg.__file__).resolve().parents[1]
BERKAS = ["routers/sales_orders.py", "routers/sales_invoices.py", "routers/quotes.py", "routers/proformas.py",
          "routers/receive_payments.py", "routers/customer_deposits.py", "routers/sales_invoice_link_order.py",
          "routers/sales_order_lampiran.py", "services/so_pelunasan.py", "services/so_pengiriman.py",
          "services/sales_doc_calc.py", "routers/credit_notes.py", "routers/deliveries.py"]
INGGRIS = re.compile(r"\b(not found|cannot|can't|must|only|already|exceeds?|invalid|required|failed|please|"
                     r"unable|allowed|should|successfully)\b", re.I)
UANG_QTY = re.compile(r"(amount|total|balance|outstanding|sisa|remaining|paid|price|harga|nilai|saldo|available|"
                      r"subtotal|disc|qty|quantity|_rem\b)", re.I)
PEMFORMAT = ("tg.rp", "tg.qty", "_rp_dp", "tg.status_id", "tg.tak_bisa_status", "tg.periode_tertutup",
             "_status_dp")  # _status_dp = label status (lewat tg.status_id), bukan angka


# ---------- helper ----------
@pytest.mark.parametrize("x,harap", [
    (100000, "Rp 100.000"), (Decimal("1250.5"), "Rp 1.250,50"), ("100000.40", "Rp 100.000,40"), (0, "Rp 0"),
    (None, "Rp 0"), (Decimal("1234567.005"), "Rp 1.234.567,01"), (-5000, "Rp -5.000"), (Decimal("0.004"), "Rp 0"),
])
def test_rp_decimal_sen_hanya_bila_ada(x, harap):
    assert tg.rp(x) == harap


def test_rp_tak_memakai_float():
    src = Path(tg.__file__).read_text()
    assert "float(" not in src  # Law 25/9: Decimal + ROUND_HALF_UP


@pytest.mark.parametrize("x,harap", [(5, "5"), ("2.50", "2,5"), (1250, "1.250"), ("3.0000", "3"),
                                     (Decimal("1250.75"), "1.250,75"), (5.0, "5")])
def test_qty(x, harap):
    assert tg.qty(x) == harap


# Label = layar CW (FE recipes/*.ts, diukur 4 Okt 2026; uang muka = putusan MASTER 4 Okt). Ubah salah satu sisi = ubah keduanya.
LABEL_FE = {
    ("so", "draft"): "Draf", ("so", "confirmed"): "Dikonfirmasi", ("so", "partial_shipped"): "Dikonfirmasi",
    ("so", "shipped"): "Dikirim", ("so", "partial_invoiced"): "Ditagih", ("so", "invoiced"): "Ditagih",
    ("so", "completed"): "Selesai", ("so", "cancelled"): "Batal",
    ("si", "draft"): "Draf", ("si", "void"): "Batal", ("si", "paid"): "Lunas", ("si", "overdue"): "Jatuh tempo",
    ("si", "partial"): "Sebagian", ("si", "posted"): "Belum lunas",
    ("quote", "draft"): "Draf", ("quote", "sent"): "Terkirim", ("quote", "viewed"): "Terkirim",
    ("quote", "accepted"): "Diterima", ("quote", "declined"): "Ditolak", ("quote", "expired"): "Kedaluwarsa",
    ("quote", "converted"): "Dikonversi", ("quote", "void"): "Batal",
    ("proforma", "draft"): "Draf", ("proforma", "issued"): "Terbit", ("proforma", "cancelled"): "Batal",
    ("proforma", "void"): "Batal", ("proforma", "expired"): "Kedaluwarsa",
    ("rp", "draft"): "Draf", ("rp", "posted"): "Diterima", ("rp", "voided"): "Batal", ("rp", "void"): "Batal",
    ("dp", "draft"): "Draf", ("dp", "posted"): "Diterima", ("dp", "partial"): "Sebagian terpakai", ("dp", "applied"): "Terpakai",
    ("dp", "void"): "Batal", ("dp", "refunded"): "Dikembalikan",
    ("cn", "draft"): "Draf", ("cn", "posted"): "Terbit", ("cn", "partial"): "Sebagian terpakai",
    ("cn", "applied"): "Terpakai", ("cn", "void"): "Batal", ("cn", "partially_refunded"): "Sebagian dikembalikan",
    ("cn", "refunded"): "Dikembalikan",
}


@pytest.mark.parametrize("kunci,harap", list(LABEL_FE.items()))
def test_label_status_sama_dengan_layar_cw(kunci, harap):
    assert tg.status_id(*kunci) == harap


def test_status_tak_dikenal_apa_adanya():
    assert tg.status_id("so", "aneh") == "aneh" and tg.status_id("dp", "CONFIRMED") == "CONFIRMED"
    assert tg.status_id("so", "CONFIRMED") == "Dikonfirmasi"


def test_kalimat():
    assert tg.tak_bisa_status("quote", "accepted", "dikirim", "QUO-1") == "Penawaran QUO-1 berstatus Diterima — tidak bisa dikirim."
    assert tg.tak_bisa_status("so", "draft", "ditagih") == "Pesanan berstatus Draf — tidak bisa ditagih."
    assert tg.periode_tertutup("Okt 2026", "LOCKED").startswith("Periode Okt 2026 sudah dikunci. Buka kembali di Akuntansi › Tutup Buku")
    assert tg.periode_tertutup(None, "CLOSED").startswith("Periode akuntansi sudah ditutup.")


# ---------- penjaga AST ----------
def _kode(call):
    for kw in call.keywords:
        if kw.arg == "status_code":
            v = kw.value
            if isinstance(v, ast.Constant):
                return v.value
            m = re.search(r"(\d{3})", ast.unparse(v))
            return int(m.group(1)) if m else None
    if call.args and isinstance(call.args[0], ast.Constant):
        return call.args[0].value
    return None


def _detail(call):
    for kw in call.keywords:
        if kw.arg == "detail":
            return kw.value
    return call.args[1] if len(call.args) > 1 else None


def _langgar_teks(node) -> list:
    """Pelanggaran pada satu ekspresi teks: kata Inggris di bagian literal; angka uang/qty tanpa pemformat."""
    out = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if INGGRIS.search(node.value):
            out.append(f"Inggris: {node.value[:80]!r}")
    elif isinstance(node, ast.JoinedStr):
        for p in node.values:
            if isinstance(p, ast.Constant):
                if INGGRIS.search(p.value):
                    out.append(f"Inggris: {p.value[:80]!r}")
            else:
                e = ast.unparse(p.value)
                if UANG_QTY.search(e) and not e.startswith(PEMFORMAT):
                    out.append(f"angka mentah: {{{e}}}")
    elif isinstance(node, ast.BinOp):
        out += _langgar_teks(node.left) + _langgar_teks(node.right)
    elif isinstance(node, ast.Dict):
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and k.value in ("message", "detail"):
                out += _langgar_teks(v)
    return out


def pindai(sumber: str) -> list:
    hasil = []
    for n in ast.walk(ast.parse(sumber)):
        if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", None)) == "HTTPException":
            k = _kode(n)
            if k == 401 or (isinstance(k, int) and k >= 500):
                continue
            hasil += [(n.lineno, x) for x in _langgar_teks(_detail(n))]
        elif isinstance(n, ast.Dict):  # blok penghalang {code, message|detail}
            kunci = {k.value for k in n.keys if isinstance(k, ast.Constant)}
            if "code" in kunci and kunci & {"message", "detail"}:
                hasil += [(n.lineno, x) for x in _langgar_teks(n)]
        elif isinstance(n, ast.Call) and getattr(n.func, "id", None) in ("_blok_void", "_blok", "_blok_dp", "_blok_nk"):
            hasil += [(n.lineno, x) for a in n.args[2:] for x in _langgar_teks(a)]
    return sorted(set(hasil))  # dict ber-code di dalam HTTPException terpindai dua jalur -> satu temuan


def test_pemindai_bisa_merah():
    """Kontrol merah (Law 33): pemindai WAJIB menangkap pola lama yang sudah diganti."""
    buruk = '''
from fastapi import HTTPException
def f(x, remaining, order):
    raise HTTPException(status_code=404, detail="Invoice not found")
    raise HTTPException(400, f"Jumlah {remaining} terlalu besar")
    raise HTTPException(status_code=400, detail={"code": "X", "message": "Cannot void"})
    raise HTTPException(status_code=400, detail="Payment amount exceeds remaining balance of Rp " + "1")
    b = {"code": "SO_X", "message": "ok", "detail": f"Cannot cancel order with status '{order['status']}'"}
    _blok_void("RP_X", 400, "Payment already voided")
    raise HTTPException(status_code=401, detail="Authentication required")
'''
    temuan = pindai(buruk)
    assert len(temuan) == 6, temuan
    assert not any(ln == 11 for ln, _ in temuan)  # 401 dikecualikan


def test_pemindai_hijau_pada_bentuk_benar():
    baik = '''
def f(remaining, s):
    raise HTTPException(status_code=404, detail="Faktur tidak ditemukan.")
    raise HTTPException(400, f"Jumlah bayar melebihi sisa tagihan {tg.rp(remaining)}.")
    raise HTTPException(400, detail=tg.tak_bisa_status("so", s, "ditagih"))
'''
    assert pindai(baik) == []


@pytest.mark.parametrize("rel", BERKAS)
def test_galat_penjualan_berbahasa_indonesia(rel):
    temuan = pindai((APP / rel).read_text())
    assert temuan == [], f"{rel}: {temuan}"
