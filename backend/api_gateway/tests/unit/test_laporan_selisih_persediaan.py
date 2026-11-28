"""Laporan Selisih saldo akun persediaan vs nilai barang (28 Nov 2026, ikut Accurate): pemasangan kartu stok <-> jurnal, hanya yang selisih."""
from datetime import date

from app.services import laporan_selisih_persediaan as LSP


def K(jid, sid, nilai, st="BILL", no="D-1"):
    return {"journal_id": jid, "source_type": st, "source_id": sid, "nomor": no, "tanggal": date(2026, 11, 2), "nilai": nilai}


def J(jid, sid, nilai, no="JV-1"):
    return {"id": jid, "journal_number": no, "journal_date": date(2026, 11, 2), "source_type": "BILL", "source_id": sid, "nilai": nilai}


def hasil(kartu, jurnal):
    return LSP.baris_selisih(LSP.pasangkan(kartu, jurnal), 0)


def test_cocok_tidak_tampil():
    assert hasil([K("j1", "s1", 100)], [J("j1", "s1", 100)]) == []
    assert hasil([K(None, "s1", 100)], [J("j1", "s1", 100)]) == []          # tanpa journal_id: dipasang lewat source_id


def test_beda_nilai_tanpa_jurnal_tanpa_kartu():
    b = hasil([K("j1", "s1", 100), K(None, "s2", 50, "ADJ", "PS-1")], [J("j1", "s1", 90), J("j3", "s3", 7, "JV-3")])
    jenis = {x["dokumen"]: (x["jenis"], x["selisih"]) for x in b}
    assert jenis == {"D-1": ("beda_nilai", 10.0), "PS-1": ("tanpa_jurnal", 50.0), "JV-3": ("tanpa_kartu", -7.0)}


def test_pemindahan_bersih_nol_tidak_tampil():
    assert hasil([K(None, "t1", 30, "STOCK_TRANSFER"), K(None, "t1", -30, "STOCK_TRANSFER")], []) == []


def test_rute_terdaftar_baca_saja():
    from pathlib import Path
    s = (Path(__file__).resolve().parents[2] / "app/routers/reports.py").read_text()
    i = s.index('@router.get("/inventory-gl-difference")')
    assert "readonly=True" in s[i:i + 1500]
