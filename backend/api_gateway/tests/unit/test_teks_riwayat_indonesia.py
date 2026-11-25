"""BUG-287/244/107/264: kalimat riwayat & keterangan yang dirakit server tak boleh memakai kata teknis/Inggris ("(void)", "diposting", "Payment to", "Customer Deposit -",
"already reconciled"). Kode peristiwa (event code) boleh tetap (TAGIHAN_DIPOSTING dst.). Penanda internal "[VOID]" (awalan pembalik yang dibaca kode lain) TIDAK termasuk."""
import re
from pathlib import Path

APP = Path(__file__).parents[2] / "app"
RIWAYAT = ["services/tagihan_riwayat.py", "services/bayar_keluar_riwayat.py", "services/biaya_riwayat.py", "services/nota_debit_riwayat.py",
           "services/kasbank_riwayat.py", "services/um_vendor_riwayat.py", "services/so_riwayat.py"]


def _kalimat(src: str):
    # argumen ke-3 k.tambah(waktu, KODE, f"kalimat", ...) dan nilai kamus SALES_INVOICE_VOIDED-style
    for m in re.finditer(r'k\.tambah\([^,]+,\s*"[A-Z_]+",\s*(f?"[^"]*")', src):
        yield m.group(1)


def test_kalimat_riwayat_tanpa_kata_teknis():
    salah = []
    for f in RIWAYAT:
        for k in _kalimat((APP / f).read_text()):
            if "(void)" in k or "diposting" in k.lower():
                salah.append((f, k))
    assert not salah, salah


def test_kamus_peristiwa_so_tanpa_void():
    assert '"Faktur dibatalkan (void)"' not in (APP / "services/so_riwayat.py").read_text()


def test_keterangan_bank_dan_409_tutup_bulan_indonesia():
    assert "Payment to {vendor_name}" not in (APP / "routers/bill_payments.py").read_text()
    assert 'f"Customer Deposit - ' not in (APP / "routers/customer_deposits.py").read_text()
    prod = (APP / "routers/production.py").read_text()
    assert "already reconciled" not in prod and "Void it first" not in prod
    assert "sudah ditutup" in prod


def test_catatan_dimuka_di_pratinjau_terbit():
    src = (APP / "services/faktur_terbit_rencana.py").read_text()
    assert "CATATAN_DIMUKA" in src and "catatan.append(CATATAN_DIMUKA)" in src
    assert "Surat Jalan" in src
