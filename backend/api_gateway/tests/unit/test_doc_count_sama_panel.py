"""doc_count daftar SO = jumlah dokumen non-rekap di panel /documents (gerbang 05 D3, 1 Okt 2026).
Diukur nyata di grapgrap: kode lama beda 6/74 SO (proforma DRAF ikut dihitung; proforma batal di SO batal tak
dihitung padahal panel menampilkannya ber-cap DIBATALKAN); sesudah perbaikan 0/74. Di sini: aturan yang sama dipaku
di kedua sisi supaya tak bercabang lagi."""
import inspect

from app.routers import dokumen as DK
from app.services import so_posisi as SP


def test_kwitansi_himpunan_sama_dengan_panel():
    daftar, panel = inspect.getsource(SP.fakta_daftar), inspect.getsource(DK.susun_dokumen)
    for sisi in (daftar, panel):
        assert "cd.journal_id IS NOT NULL" in sisi          # uang muka tanpa jurnal bukan kwitansi
        assert "customer_deposit_applications" in sisi       # uang muka lepas yang diterapkan ke faktur SO
        assert "rp.status = 'posted'" in sisi and "rpa.status = 'active'" in sisi
    assert "count(DISTINCT id)" in daftar                    # satu kwitansi per nomor (panel: dict per id)


def test_proforma_draf_tak_dihitung_batal_hanya_di_so_batal():
    daftar, panel = inspect.getsource(SP.fakta_daftar), inspect.getsource(DK.susun_dokumen)
    assert "status <> 'draft'" in panel and 'p["status"] != "cancelled" or batal' in panel
    assert 'p["status"] != "draft"' in daftar
    assert '(p["status"] != "cancelled" or r["status"] == "cancelled")' in daftar
