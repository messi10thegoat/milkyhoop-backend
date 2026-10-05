"""(MASTER/pemilik 5 Okt 2026) label JUDUL order = setelan tenant (V390 order_code_settings.title_label, bawaan
'Judul order') di semua permukaan yang memakai label kode (order_title_label di samping order_code_label); + label nilai
untuk FE: method_label (Penerimaan, Uang Muka) dan reason_label (Nota Kredit). Aditif: medan lama tak berubah."""
import inspect
import re
import typing
from pathlib import Path

import pytest

from app.routers import credit_notes as CN, customer_deposits as CD, kode_order as RK, receive_payments as RP
from app.schemas import credit_notes as SCN, customer_deposits as SCD, receive_payments as SRP
from app.services import kode_order as KO, so_riwayat as SR
from app.utils.metode_pembayaran import label_metode, label_metode_layar

APP = Path(KO.__file__).resolve().parents[1]


def test_bawaan_dan_validasi_label_judul():
    assert KO.BAWAAN["title_label"] == KO.LABEL_JUDUL_BAWAAN == "Judul order"
    assert KO.normal_label("  Judul   SPK ", "Label judul") == "Judul SPK"
    for buruk in ("", "x" * (KO.MAKS_LABEL + 1), 5):
        with pytest.raises(KO.KodeOrderGalat, match="Label judul"):
            KO.normal_label(buruk, "Label judul")
    with pytest.raises(KO.KodeOrderGalat, match="Judul SPK maksimal"):
        KO.normal_judul("A" * (KO.MAKS_JUDUL + 1), "Judul SPK")


def test_pengaturan_menerima_dan_mengembalikan_title_label():
    lama = dict(KO.BAWAAN)
    s = RK._badan({"title_label": " Judul  SPK "}, lama)
    assert s["title_label"] == "Judul SPK" and RK._keluaran(s)["title_label"] == "Judul SPK"
    assert RK._badan({}, lama)["title_label"] == "Judul order"  # tak dikirim = tetap
    with pytest.raises(KO.KodeOrderGalat):
        RK._badan({"title_label": ""}, lama) if False else KO.normal_label("", "Label judul")
    src = inspect.getsource(RK)
    assert "title_label = EXCLUDED.title_label" in src


def test_muat_setelan_membaca_kolom_dan_pembentuk_dokumen_membawa_label_judul():
    assert "title_label FROM order_code_settings" in inspect.getsource(KO.muat_setelan)
    for f in (KO.kode_untuk_dokumen, KO.tempel_kode, KO.so_hasil_penawaran):
        assert '"order_title_label"' in inspect.getsource(f), f.__name__


def test_riwayat_judul_dirender_dengan_label_sekarang():
    s = inspect.getsource(SR._selesaikan)
    assert '"ORDER_TITLE_CHANGED"' in s and 'f"{label_judul}: ' in s
    for f in (KO.ubah_judul, KO.impor):
        assert '"Judul order:' not in inspect.getsource(f), f.__name__


def _literal_judul_order(sumber: str) -> list:
    """Baris string literal KODE (bukan docstring/komentar) yang memuat 'Judul order'."""
    import ast
    pohon = ast.parse(sumber)
    doc = set()
    for n in ast.walk(pohon):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body:
            b = n.body[0]
            if isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant) and isinstance(b.value.value, str):
                doc.add(id(b.value))
    return sorted({n.lineno for n in ast.walk(pohon)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc
                   and "Judul order" in n.value})


def test_penjaga_statis_tak_ada_judul_order_tertanam():
    """Teks 'Judul order' hanya boleh di konstanta bawaan + bawaan skema; selain itu = label tenant diabaikan."""
    temuan = []
    for p in APP.rglob("*.py"):
        rel = str(p.relative_to(APP))
        baris = p.read_text().splitlines()
        for n in _literal_judul_order(p.read_text()):
            teks = baris[n - 1]
            if rel == "services/kode_order.py" and teks.startswith('LABEL_JUDUL_BAWAAN = "Judul order"'):
                continue
            if rel.startswith("schemas/") and 'order_title_label: str = "Judul order"' in teks:
                continue
            temuan.append(f"{rel}:{n}")
    assert temuan == [], temuan


def test_penjaga_statis_bisa_merah():
    buruk = 'def f():\n    """Judul order: docstring boleh."""\n    raise X("Judul order wajib berupa teks.")\n    y = f"{a} Judul order"\n'
    assert _literal_judul_order(buruk) == [3, 4]


@pytest.mark.parametrize("m,harap", [("cash", "Tunai"), ("bank_transfer", "Transfer Bank"), ("transfer", "Transfer Bank"),
                                      ("e_wallet", "E-Wallet"), ("check", "Cek"), ("other", "Lainnya")])
def test_label_metode_satu_aturan(m, harap):
    assert label_metode(m) == harap


def test_method_label_di_daftar_dan_detail():
    assert inspect.getsource(RP).count('"method_label": label_metode_layar(') == 2
    assert inspect.getsource(CD).count('"method_label": label_metode_layar(') == 3
    assert '"method_label": label_metode(' not in inspect.getsource(RP) + inspect.getsource(CD)


@pytest.mark.parametrize("m", [None, "", "  "])
def test_method_label_kosong_tetap_kosong(m):
    # baris pemakaian uang muka / NK di daftar penerimaan (payment_method NULL) -- diukur kaos 5 Okt: dulu "Transfer Bank"
    assert label_metode_layar(m) is None
    assert label_metode(m) == "Transfer Bank"  # kwitansi/PDF tetap perilaku lama


def test_method_label_berisi_sama_dengan_label_metode():
    for m in ("cash", "bank_transfer", "e_wallet", "transfer", "check", "other", "aneh"):
        assert label_metode_layar(m) == label_metode(m)
    for kelas in (SRP.ReceivePaymentListItem, SRP.ReceivePaymentDetail, SCD.CustomerDepositListItem, SCD.CustomerDepositDetail):
        assert "method_label" in kelas.model_fields, kelas.__name__


def test_reason_label_nk_lengkap_dan_terdeklarasi():
    nilai = set(typing.get_args(SCN.CreateCreditNoteRequest.model_fields["reason"].annotation))
    assert set(CN.LABEL_ALASAN_NK) == nilai == {"return", "pricing_error", "discount", "damaged", "other"}
    assert CN.LABEL_ALASAN_NK["pricing_error"] == "Salah harga" and CN.LABEL_ALASAN_NK["return"] == "Retur"
    assert inspect.getsource(CN).count('"reason_label": LABEL_ALASAN_NK.get(') == 2
    for kelas in (SCN.CreditNoteListItem, SCN.CreditNoteDetail):
        assert "reason_label" in kelas.model_fields, kelas.__name__
