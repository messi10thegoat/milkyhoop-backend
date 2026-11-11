"""BF-1a parser mutasi bank (KONTRAK-bankfeed §3, MASTER 12 Nov: fixture golden TIDAK di repo -> kasus SINTETIS kecil di sini + kontrol koma
berkutip; berkas golden penuh dipakai harness di /root/fixtures/bankfeed_golden/ di luar repo)."""
from datetime import date
from decimal import Decimal as D

import pytest

from app.services import bankfeed_parser as P

KEPALA = "No. Rekening,=,'******2185\nNama,=,PEMILIK\nMata Uang,=,IDR\n\nTanggal,Keterangan,Cabang,Jumlah,,Saldo\n"


def klikbca(baris, saldo_awal="1000000.00", kredit=None, debet=None, saldo_akhir=None, kepala=KEPALA):
    """Berkas KlikBCA sintetis; kaki dihitung dari baris kecuali diberi eksplisit. baris = [(tgl, ket, jumlah, dbcr, saldo)]."""
    k = sum(D(j) for tgl, ket, j, a, saldo in baris if a == "CR" and tgl != "PEND")
    d = sum(D(j) for tgl, ket, j, a, saldo in baris if a == "DB" and tgl != "PEND")
    sa = D(saldo_awal)
    isi = kepala
    for tgl, ket, jumlah, dbcr, saldo in baris:
        isi += f"'{tgl},{ket},'0000,{jumlah},{dbcr},{saldo}\n"
    isi += f"Saldo Awal,=,{saldo_awal}\nKredit,=,{kredit or format(k, '.2f')}\nDebet,=,{debet or format(d, '.2f')}\n"
    isi += f"Saldo Akhir,=,{saldo_akhir or format(sa + k - d, '.2f')}\n"
    return isi.encode("latin-1")


BARIS = [
    ("01/08/2026", "TRSF E-BANKING CR 0108/FTSCY/WS95031 500000.00 N001 N002", "500000.00", "CR", "1500000.00"),
    ("02/08/2026", '"KARTU DEBIT 1,5 JT, ****0128"', "150000.00", "DB", "1350000.00"),
    ("PEND", "TRSF E-BANKING CR PENDING", "70000.00", "CR", "1420000.00"),
    ("03/08/2026", "BIAYA ADM", "25.00", "DB", "1349975.00"),
]


def test_baca_klikbca_dasar():
    r = P.baca(klikbca(BARIS), "mutasi.csv")
    assert r.format == "klikbca_csv" and r.rekening_berkas == "******2185" and not r.blocks
    assert [(b.tanggal, b.arah, b.nominal, b.saldo) for b in r.baris] == [
        (date(2026, 8, 1), "masuk", D("500000.00"), D("1500000.00")),
        (date(2026, 8, 2), "keluar", D("150000.00"), D("1350000.00")),
        (date(2026, 8, 3), "keluar", D("25.00"), D("1349975.00"))]
    assert r.saldo_awal == D("1000000.00") and r.saldo_akhir == D("1349975.00") and r.kredit == D("500000.00") and r.debet == D("150025.00")
    assert r.dilewati == [{"alasan": "PEND (belum dibukukan bank)", "n": 1}] and r.baris[0].baris_ke == 6


def test_koma_berkutip_terbaca_utuh_dan_kontrol_split_naif_meleset():
    r = P.baca(klikbca(BARIS), "mutasi.csv")
    assert r.baris[1].keterangan == "KARTU DEBIT 1,5 JT, ****0128"
    mentah = r.baris[1].mentah
    assert len(mentah.split(",")) > 6   # KONTROL: pemisah koma naif memecah baris ini jadi > 6 kolom (harus meleset)


def test_koma_tak_berkutip_disambung_defensif():
    isi = klikbca([("01/08/2026", "TRSF A, B, C", "100.00", "DB", "999900.00")])
    r = P.baca(isi, "mutasi.csv")
    assert not r.blocks and r.baris[0].keterangan == "TRSF A, B, C" and r.baris[0].nominal == D("100.00")


def test_kolom_id_baris_tambahan_golden_dikenali():
    kepala = KEPALA.replace("Saldo\n", "Saldo,id_baris\n")
    isi = kepala + "'01/08/2026,TRSF,'0000,100.00,DB,999900.00,BK0001\nSaldo Awal,=,1000000.00\nKredit,=,0.00\nDebet,=,100.00\nSaldo Akhir,=,999900.00\n"
    r = P.baca(isi.encode("latin-1"), "a.csv")
    assert not r.blocks and len(r.baris) == 1 and r.baris[0].keterangan == "TRSF" and r.baris[0].saldo == D("999900.00")


def test_kaki_seimbang_tanpa_blok():
    assert P.baca(klikbca(BARIS), "x.csv").blocks == []


@pytest.mark.parametrize("ubah,kata", [
    (dict(kredit="600000.00"), "Kredit"),
    (dict(debet="1.00"), "Debet"),
    (dict(saldo_akhir="1349974.00"), "Saldo akhir"),
])
def test_kaki_tak_seimbang_blok_berkalimat(ubah, kata):
    r = P.baca(klikbca(BARIS, **ubah), "x.csv")
    assert [b["code"] for b in r.blocks] == ["BERKAS_TAK_SEIMBANG"] and kata in r.blocks[0]["message"]


def test_saldo_per_baris_meleset_menunjuk_baris_pertama():
    rusak = [BARIS[0], ("02/08/2026", "TRSF X", "150000.00", "DB", "1300000.00"), BARIS[3]]   # seharusnya 1350000.00
    r = P.baca(klikbca(rusak, saldo_akhir="1349975.00"), "x.csv")
    assert r.blocks[0]["code"] == "BERKAS_TAK_SEIMBANG" and "baris 7" in r.blocks[0]["message"] and "50.000" in r.blocks[0]["message"]


def test_pend_tak_ikut_hitungan_kaki():
    # kaki bank belum menghitung PEND: Σ baris tanpa PEND == kaki; bila PEND ikut dihitung, kaki meleset -> terbukti dari berkas yang seimbang
    assert P.baca(klikbca(BARIS), "x.csv").blocks == []


def test_berkas_kosong_dan_format_tak_dikenal():
    kosong = P.baca(klikbca([]), "x.csv")
    assert [b["code"] for b in kosong.blocks] == ["BERKAS_KOSONG"]
    asing = P.baca(b"\x00\x01 bukan csv", "x.bin")
    assert asing.blocks and asing.blocks[0]["code"] in ("FORMAT_TAK_DIKENALI", "KOLOM_TAK_DIKENALI")


def test_baris_tak_terbaca_dilaporkan_bukan_dibuang_diam():
    isi = KEPALA + "'01/08/2026,TRSF,'0000,abc,DB,100.00\nSaldo Awal,=,100.00\nKredit,=,0.00\nDebet,=,0.00\nSaldo Akhir,=,100.00\n"
    r = P.baca(isi.encode("latin-1"), "x.csv")
    assert "BARIS_TAK_TERBACA" in [b["code"] for b in r.blocks] and "baris 6" in r.blocks[0]["message"]


def test_mata_uang_bukan_rupiah_diblok():
    r = P.baca(klikbca(BARIS, kepala=KEPALA.replace("IDR", "USD")), "x.csv")
    assert "MATA_UANG_BUKAN_IDR" in [b["code"] for b in r.blocks]


def test_batas_baris_per_impor():
    banyak = [("01/08/2026", f"TRSF {i}", "1.00", "CR", format(D(1000000) + i + 1, ".2f")) for i in range(P.MAKS_BARIS + 1)]
    r = P.baca(klikbca(banyak), "x.csv")
    assert "BERKAS_TERLALU_BESAR" in [b["code"] for b in r.blocks]


# ---------------------------------------------------------------- normalisasi + uang + tanggal
@pytest.mark.parametrize("mentah,harap", [
    ("TRSF E-BANKING DB 0108/FTSCY/WS95031 3900000.00 N001 N002", "trsf e-banking db 3900000.00 n001 n002"),
    ("TRSF  E-BANKING   CR 2008/FTSCY/WS95271   N0001", "trsf e-banking cr n0001"),
    ("QR 00088001234567 PEMBAYARAN TOKO", "pembayaran toko"),
    ("  BIAYA ADM  ", "biaya adm"),
    ("", ""),
])
def test_normalisasi_keterangan(mentah, harap):
    assert P.normalisasi_keterangan(mentah) == harap


@pytest.mark.parametrize("teks,harap", [
    ("3900000.00", D("3900000.00")), ("3.900.000,00", D("3900000.00")), ("Rp 1.250.000", D("1250000")), ("1,250.50", D("1250.50")),
    ("1.5", D("1.5")), ("150,5", D("150.5")), ("0.00", D("0.00")), ("-5.000", D("-5000")),
])
def test_parse_uang(teks, harap):
    assert P.parse_uang(teks) == harap


def test_parse_uang_menolak_sampah():
    for jelek in ("abc", "", "1.2.3,4,5", None):
        with pytest.raises(ValueError):
            P.parse_uang(jelek)


@pytest.mark.parametrize("teks,harap", [
    ("01/08/2026", date(2026, 8, 1)), ("'01/08/2026", date(2026, 8, 1)), ("2026-08-01", date(2026, 8, 1)), ("1-8-2026", date(2026, 8, 1)),
    ("01/08/26", date(2026, 8, 1)),
])
def test_parse_tanggal(teks, harap):
    assert P.parse_tanggal(teks) == harap


def test_parse_tanggal_menolak_sampah():
    for jelek in ("31/02/2026", "PEND", "", "x"):
        with pytest.raises(ValueError):
            P.parse_tanggal(jelek)


# ---------------------------------------------------------------- sidik
def _b(tgl=date(2026, 8, 1), ket="TRSF A", nominal=D("100.00"), arah="keluar", saldo=D("900.00"), ke=1):
    return P.BarisBank(baris_ke=ke, tanggal=tgl, keterangan=ket, nominal=nominal, arah=arah, saldo=saldo, mentah="x")


def test_sidik_deterministik_dan_peka_ke_tiap_bagian():
    dasar = P.beri_sidik("t1", "r1", [_b()])[0]
    assert dasar == P.beri_sidik("t1", "r1", [_b()])[0] and len(dasar) == 64
    for ubah in (dict(tgl=date(2026, 8, 2)), dict(ket="TRSF B"), dict(nominal=D("100.01")), dict(arah="masuk"), dict(saldo=D("901.00"))):
        assert P.beri_sidik("t1", "r1", [_b(**ubah)])[0] != dasar, ubah
    assert P.beri_sidik("t2", "r1", [_b()])[0] != dasar and P.beri_sidik("t1", "r2", [_b()])[0] != dasar


def test_sidik_abaikan_kode_bervariasi_di_keterangan():
    a = P.beri_sidik("t", "r", [_b(ket="TRSF 0108/FTSCY/WS95031 N001")])[0]
    b = P.beri_sidik("t", "r", [_b(ket="TRSF 0108/FTSCY/WS99999 N001")])[0]
    assert a == b


def test_dua_transaksi_kembar_sehari_tetap_terpisah_berkat_saldo():
    s = P.beri_sidik("t", "r", [_b(saldo=D("900.00"), ke=1), _b(saldo=D("800.00"), ke=2)])
    assert s[0] != s[1]


def test_tanpa_saldo_pakai_kemunculan_ke_n_dan_tetap_terpisah():
    s = P.beri_sidik("t", "r", [_b(saldo=None, ke=1), _b(saldo=None, ke=2), _b(saldo=None, ke=3, ket="LAIN")])
    assert len({s[0], s[1], s[2]}) == 3
    assert P.beri_sidik("t", "r", [_b(saldo=None, ke=9), _b(saldo=None, ke=10)]) == s[:2]   # urutan kemunculan, bukan nomor baris


# ---------------------------------------------------------------- CSV generik
def test_csv_generik_header_dikenal_format_indonesia():
    isi = "Tanggal;Keterangan;Debit;Kredit;Saldo\n01/08/2026;SETORAN;;3.900.000,00;3.900.000,00\n02/08/2026;BAYAR LISTRIK;150.000,00;;3.750.000,00\n"
    r = P.baca(isi.encode("utf-8"), "bank.csv")
    assert r.format == "csv" and not r.blocks
    assert [(b.arah, b.nominal, b.saldo) for b in r.baris] == [("masuk", D("3900000.00"), D("3900000.00")), ("keluar", D("150000.00"), D("3750000.00"))]
    assert r.peta_kolom["tanggal"] == 0 and r.peta_kolom["debit"] == 2


def test_csv_generik_kolom_jumlah_dengan_arah():
    isi = "date,description,amount,type\n2026-08-01,Sale,100.00,CR\n2026-08-02,Fee,5.00,DB\n"
    r = P.baca(isi.encode("utf-8"), "x.csv")
    assert [(b.arah, b.nominal) for b in r.baris] == [("masuk", D("100.00")), ("keluar", D("5.00"))] and r.baris[0].saldo is None


def test_csv_tak_dikenal_beri_usulan_peta_dan_peta_dari_pengguna_dipakai():
    isi = "kolA,kolB,kolC\n01/08/2026,Setor,100.00\n"
    r = P.baca(isi.encode("utf-8"), "x.csv")
    assert [b["code"] for b in r.blocks] == ["KOLOM_TAK_DIKENALI"] and r.peta_kolom is not None
    r2 = P.baca(isi.encode("utf-8"), "x.csv", peta_kolom={"tanggal": 0, "keterangan": 1, "kredit": 2})
    assert not r2.blocks and r2.baris[0].arah == "masuk" and r2.baris[0].nominal == D("100.00")


def test_pemeriksaan_struktural_kode():
    import inspect
    s = inspect.getsource(P)
    assert "csv.reader" in s and "float(" not in s            # pembaca berkutip; uang selalu Decimal (tak pernah float)
