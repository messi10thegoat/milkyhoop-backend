"""BF-1b penentu tebakan (KONTRAK-bankfeed §4 langkah 2 cocok + 5 bawaan BCA + 6 kosong): bagian MURNI (tanpa DB). Tebak cocok: nominal persis, arah sama,
tanggal bt − tanggal bank ∈ [−90, +20] (P5), keyakinan turun dengan jarak; nama kontak (token awalan 4 huruf) ↔ keterangan; gabungan hanya 'periksa'; K4
layak_massal = yakin DAN unik dua arah. Akun bawaan BCA (putusan F2, 12 Nov): BIAYA ADM→5-20850, BUNGA→4-90000, PAJAK BUNGA→5-80000."""
from datetime import date
from decimal import Decimal as D

import pytest

from app.services import bankfeed_tebak as TB

BANK = {"id": "m1", "tanggal": date(2026, 10, 10), "nominal": D("500000.00"), "arah": "masuk",
        "keterangan": "TRSF E-BANKING CR 1010/FTSCY/WS95031 500000.00 SUMBER REJEKI"}


def bt(id_, tgl=date(2026, 10, 10), nominal="500000.00", arah="masuk", kontak="PT Sumber Rejeki Abadi", nomor="RCV-1", sumber="RECEIVE_PAYMENT"):
    return {"id": id_, "tanggal": tgl, "nominal": D(nominal), "arah": arah, "kontak_nama": kontak, "nomor": nomor, "jenis": "payment_received", "sumber": sumber}


# ---------------------------------------------------------------- rentang tanggal P5
@pytest.mark.parametrize("hari,ok", [(-90, True), (-91, False), (0, True), (20, True), (21, False)])
def test_rentang_p5_tepat_di_batas(hari, ok):
    from datetime import timedelta
    assert TB.dalam_rentang(date(2026, 10, 10), date(2026, 10, 10) + timedelta(days=hari)) is ok


# ---------------------------------------------------------------- nama
def test_nama_cocok_token_awalan_empat_huruf():
    assert TB.nama_cocok("TRSF E-BANKING CR 1010/FTSCY/WS95031 SUMBER REJEKI", "PT Sumber Rejeki Abadi")
    assert TB.nama_cocok("TRSF E-BANKING CR 1010 SUMBERREJ", "Sumber Rejeki")          # nama bank terpotong 18 karakter
    assert not TB.nama_cocok("TRSF E-BANKING CR 1010/FTSCY/WS95031 ANDI", "Budi Santoso")


def test_kata_umum_bank_bukan_nama():
    # KONTROL: "transfer"/"bank"/"bca"/"trsf" bukan nama; tanpa penyaringan ini kontak "PT Transfer Jaya" akan 'cocok' ke tiap BI-FAST
    assert not TB.nama_cocok("BI-FAST CR BIF TRANSFER DR 535 N033 N034", "PT Transfer Jaya")
    assert not TB.nama_cocok("TRSF E-BANKING CR BANK BCA", "Bank Mandiri")
    assert not TB.nama_cocok("TRSF E-BANKING CR", None) and not TB.nama_cocok("", "Sumber")


def test_nama_anonim_sebagian_tetap_cocok():
    # golden: pengirim `N0123 N0456` vs pelanggan `N0456`
    assert TB.nama_cocok("BI-FAST CR BIF TRANSFER DR 535 N0123 N0456", "N0456")


# ---------------------------------------------------------------- putuskan: cocok
def test_satu_kandidat_nama_cocok_tiga_hari_yakin():
    t = TB.putuskan(BANK, [bt("b1", date(2026, 10, 12))])
    assert t["jenis"] == "cocok" and t["keyakinan"] == "yakin" and t["bank_transaction_ids"] == ["b1"] and t["nama_cocok"] is True
    assert t["dokumen"] == [{"jenis": "payment_received", "id": "b1", "nomor": "RCV-1"}] and "RCV-1" in t["kalimat"]


def test_satu_kandidat_empat_hari_hanya_periksa():
    t = TB.putuskan(BANK, [bt("b1", date(2026, 10, 14))])
    assert t["jenis"] == "cocok" and t["keyakinan"] == "periksa"


def test_satu_kandidat_tanpa_nama_cocok_tak_pernah_yakin_walau_hari_sama():
    t = TB.putuskan(BANK, [bt("b1", kontak="Budi Santoso")])
    assert t["jenis"] == "cocok" and t["keyakinan"] == "periksa" and "nama" in t["kalimat"].lower()


def test_dua_kandidat_nominal_sama_periksa_dan_tak_dipilih_diam():
    t = TB.putuskan(BANK, [bt("b1"), bt("b2", nomor="RCV-2")])
    assert t["keyakinan"] == "periksa" and sorted(t["bank_transaction_ids"]) == ["b1", "b2"] and t["jenis"] == "cocok"
    assert t["jumlah_calon"] == 2


def test_arah_berlawanan_dan_nominal_beda_bukan_kandidat():
    t = TB.putuskan(BANK, [bt("b1", arah="keluar"), bt("b2", nominal="499999.00")])
    assert t["jenis"] == "kosong" and t["keyakinan"] == "kurang_data"


def test_di_luar_rentang_bukan_kandidat():
    assert TB.putuskan(BANK, [bt("b1", date(2026, 10, 31))])["jenis"] == "kosong"          # +21
    assert TB.putuskan(BANK, [bt("b1", date(2026, 7, 11))])["jenis"] == "kosong"           # −91
    assert TB.putuskan(BANK, [bt("b1", date(2026, 7, 12))])["jenis"] == "cocok"            # −90


def test_jauh_dalam_rentang_keyakinan_turun_kalimat_menyebut_jarak():
    t = TB.putuskan(BANK, [bt("b1", date(2026, 8, 20))])      # −51 hari (pencatatan susulan)
    assert t["keyakinan"] == "periksa" and "51" in t["kalimat"]


# ---------------------------------------------------------------- selisih biaya 2.500 (hanya /calon)
def test_selisih_biaya_tetap_periksa_walau_nama_dan_hari_cocok():
    c = TB.skor_calon(BANK, bt("b1", nominal="502500.00"))
    assert c["selisih_nominal"] == D("2500.00") and c["keyakinan"] == "periksa"
    assert "2.500" in c["kalimat"] and "salah input" in c["kalimat"]
    assert TB.putuskan(BANK, [bt("b1", nominal="502500.00")])["jenis"] == "kosong"          # tebak = nominal PERSIS


# ---------------------------------------------------------------- gabungan
def test_gabungan_dua_sampai_empat_bt_hanya_periksa():
    t = TB.putuskan(BANK, [bt("b1", nominal="300000.00"), bt("b2", nominal="200000.00", nomor="RCV-2")])
    assert t["jenis"] == "cocok" and t["keyakinan"] == "periksa" and sorted(t["bank_transaction_ids"]) == ["b1", "b2"] and t["gabungan"] is True


def test_gabungan_nama_beda_atau_lebih_dari_empat_bukan_tebakan():
    assert TB.putuskan(BANK, [bt("b1", nominal="300000.00", kontak="X Y"), bt("b2", nominal="200000.00", kontak="X Y")])["jenis"] == "kosong"
    lima = [bt(f"b{i}", nominal="100000.00", nomor=f"R{i}") for i in range(5)]
    assert TB.putuskan(BANK, lima)["jenis"] == "kosong"


def test_gabungan_di_luar_sepuluh_hari_bukan_tebakan():
    t = TB.putuskan(BANK, [bt("b1", date(2026, 10, 21), "300000.00"), bt("b2", date(2026, 10, 21), "200000.00")])
    assert t["jenis"] == "kosong"


def test_satu_bt_untuk_dua_sampai_tiga_baris_bank():
    bt1 = bt("b1", nominal="500000.00")
    baris = [{**BANK, "id": "m1", "nominal": D("300000.00")}, {**BANK, "id": "m2", "nominal": D("200000.00")}]
    hasil = TB.gabungan_baris(bt1, baris)
    assert sorted(hasil) == ["m1", "m2"] and all(h["keyakinan"] == "periksa" and h["bank_transaction_ids"] == ["b1"] for h in hasil.values())


# ---------------------------------------------------------------- bawaan BCA (F2)
def test_pemetaan_akun_bawaan_terkunci_putusan_f2():
    assert TB.AKUN_BAWAAN == {"admin": "5-20850", "bunga": "4-90000", "pajak_bunga": "5-80000"}


def test_bawaan_biaya_adm_keluar_ke_biaya_administrasi_bank():
    b = TB.bawaan_bca("BIAYA ADM", "keluar")
    assert b == {"kunci": "admin", "kode": "5-20850"}


def test_bawaan_bunga_masuk_pajak_bunga_keluar_dan_urutan_tidak_tertukar():
    assert TB.bawaan_bca("BUNGA", "masuk") == {"kunci": "bunga", "kode": "4-90000"}
    assert TB.bawaan_bca("PAJAK BUNGA", "keluar") == {"kunci": "pajak_bunga", "kode": "5-80000"}      # bukan 'bunga'


@pytest.mark.parametrize("ket,arah", [("BUNGA", "keluar"), ("BIAYA ADM", "masuk"), ("PAJAK BUNGA", "masuk"),
                                      ("BI-FAST DB BIF BIAYA TXN KE 002 N048 N049 M-BCA", "keluar"),   # biaya BI-FAST 2.500 BUKAN admin bank
                                      ("KARTU DEBIT N021 T41H N022 N023 ************0128", "keluar"), ("TRSF E-BANKING DB", "keluar"), ("", "keluar")])
def test_bawaan_tidak_menebak_di_luar_kata_kunci_atau_arah_salah(ket, arah):
    assert TB.bawaan_bca(ket, arah) is None


def test_tanpa_kandidat_bawaan_bca_jadi_catat_periksa_dengan_akun():
    t = TB.putuskan({**BANK, "keterangan": "BIAYA ADM", "arah": "keluar", "nominal": D("11000.00")}, [], akun={"admin": {"id": "a1", "kode": "5-20850", "nama": "Biaya Administrasi Bank"}})
    assert t["jenis"] == "catat" and t["keyakinan"] == "periksa" and t["akun"] == {"id": "a1", "kode": "5-20850", "nama": "Biaya Administrasi Bank"}
    assert t["dasar"] == "bawaan_bca"


def test_bawaan_bca_akun_tak_ada_blok_berkode_bukan_buat_diam():
    t = TB.putuskan({**BANK, "keterangan": "BIAYA ADM", "arah": "keluar", "nominal": D("11000.00")}, [], akun={})
    assert t["jenis"] == "kosong" and t["blok"]["code"] == "AKUN_BAWAAN_TAK_ADA" and "5-20850" in t["blok"]["message"]


def test_cocok_didahulukan_atas_bawaan():
    # urutan §4: cocok (2) sebelum bawaan (5)
    baris = {**BANK, "keterangan": "BIAYA ADM", "arah": "keluar", "nominal": D("11000.00")}
    t = TB.putuskan(baris, [bt("b1", nominal="11000.00", arah="keluar", kontak="Biaya Adm")], akun={"admin": {"id": "a1", "kode": "5-20850", "nama": "x"}})
    assert t["jenis"] == "cocok"


# ---------------------------------------------------------------- K4 layak_massal
def _t(**k):
    d = {"jenis": "cocok", "keyakinan": "yakin", "bank_transaction_ids": ["b1"], "gabungan": False, "jumlah_calon": 1}
    d.update(k)
    return d


def test_layak_massal_hanya_yakin_unik_dua_arah():
    peta = {"m1": _t(), "m2": _t(bank_transaction_ids=["b2"])}
    hasil = TB.tandai_layak_massal(peta, {"m1": "untuk_ditinjau", "m2": "untuk_ditinjau"})
    assert hasil["m1"]["layak_massal"] is True and hasil["m2"]["layak_massal"] is True


def test_dua_baris_memilih_dokumen_sama_tak_ada_yang_massal():
    peta = {"m1": _t(), "m2": _t()}
    hasil = TB.tandai_layak_massal(peta, {"m1": "untuk_ditinjau", "m2": "untuk_ditinjau"})
    assert hasil["m1"]["layak_massal"] is False and hasil["m2"]["layak_massal"] is False
    assert "layak_massal_text" in hasil["m1"] and hasil["m1"]["layak_massal_text"]


@pytest.mark.parametrize("ubah", [{"keyakinan": "periksa"}, {"gabungan": True}, {"jumlah_calon": 2}, {"jenis": "catat"}, {"mungkin_dobel_dengan": ["m9"]}])
def test_yang_bukan_satu_satu_yakin_tak_pernah_massal(ubah):
    hasil = TB.tandai_layak_massal({"m1": _t(**ubah)}, {"m1": "untuk_ditinjau"})
    assert hasil["m1"]["layak_massal"] is False


def test_mungkin_duplikat_atau_sudah_diputus_tak_massal():
    assert TB.tandai_layak_massal({"m1": _t()}, {"m1": "mungkin_duplikat"})["m1"]["layak_massal"] is False
    assert TB.tandai_layak_massal({"m1": _t()}, {"m1": "dicatat"})["m1"]["layak_massal"] is False


def test_baris_dicatat_yang_sama_dokumen_tak_memblok_massal_baris_lain_tapi_untuk_ditinjau_lain_ya():
    # lawan dua arah dihitung hanya di antara baris untuk_ditinjau yang memilih dokumen itu
    peta = {"m1": _t(), "m2": _t()}
    hasil = TB.tandai_layak_massal(peta, {"m1": "untuk_ditinjau", "m2": "dikecualikan"})
    assert hasil["m1"]["layak_massal"] is True


# ---------------------------------------------------------------- sumber dokumen: yakin hanya untuk dokumen PEMBAYARAN (terukur golden: 3/3 biaya tak punya pasangan bank)
@pytest.mark.parametrize("sumber", ["EXPENSE", "BANK_TRANSACTION", "ADJUSTMENT", "BANK_OPENING_BALANCE", None, "SUMBER_BARU_TAK_DIKENAL"])
def test_bukan_dokumen_pembayaran_paling_tinggi_periksa_walau_nominal_nama_hari_cocok(sumber):
    t = TB.putuskan(BANK, [bt("b1", sumber=sumber)])
    assert t["jenis"] == "cocok" and t["keyakinan"] == "periksa" and t["bank_transaction_ids"] == ["b1"]
    assert "biaya/manual" in t["kalimat"]


@pytest.mark.parametrize("sumber", sorted(TB.SUMBER_YAKIN))
def test_dokumen_pembayaran_boleh_yakin(sumber):
    assert TB.putuskan(BANK, [bt("b1", sumber=sumber)])["keyakinan"] == "yakin"


def test_sumber_yakin_himpunan_tetap_tanpa_biaya_atau_manual():
    assert TB.SUMBER_YAKIN == frozenset({"RECEIVE_PAYMENT", "PAYMENT_RECEIVED", "CUSTOMER_DEPOSIT", "BILL_PAYMENT", "PAYMENT_BILL", "PAYMENT_MADE", "VENDOR_DEPOSIT"})


# ---------------------------------------------------------------- persaingan: dokumen sama dipilih dua baris bernama sama -> tak ada yang yakin
def _yakin(**k):
    d = {"jenis": "cocok", "keyakinan": "yakin", "kalimat": "x", "bank_transaction_ids": ["b1"], "dokumen": [{"jenis": "j", "id": "b1", "nomor": "RCV-1"}],
         "gabungan": False, "jumlah_calon": 1, "nama_cocok": True, "mungkin_dobel_dengan": None, "mungkin_dobel_jumlah": 0}
    d.update(k)
    return d


def test_dua_baris_bernama_sama_pilih_dokumen_sama_keduanya_turun_ke_periksa():
    st = {"m1": "untuk_ditinjau", "m2": "untuk_ditinjau"}
    h = TB.turunkan_persaingan({"m1": _yakin(), "m2": _yakin()}, st)
    assert h["m1"]["keyakinan"] == "periksa" and h["m2"]["keyakinan"] == "periksa"
    assert h["m1"]["mungkin_dobel_dengan"] == ["m2"] and h["m1"]["mungkin_dobel_jumlah"] == 1 and "ganda" in h["m1"]["kalimat"]


def test_pembayar_lain_bernominal_sama_tapi_nama_beda_bukan_saingan():
    st = {"m1": "untuk_ditinjau", "m2": "untuk_ditinjau"}
    h = TB.turunkan_persaingan({"m1": _yakin(), "m2": _yakin(keyakinan="periksa", nama_cocok=False)}, st)
    assert h["m1"]["keyakinan"] == "yakin" and h["m1"]["mungkin_dobel_dengan"] is None and h["m2"]["keyakinan"] == "periksa"


def test_saingan_yang_sudah_diputus_atau_gabungan_tak_dihitung():
    h = TB.turunkan_persaingan({"m1": _yakin(), "m2": _yakin(), "m3": _yakin(gabungan=True)}, {"m1": "untuk_ditinjau", "m2": "dicatat", "m3": "untuk_ditinjau"})
    assert h["m1"]["keyakinan"] == "yakin"


def test_daftar_saingan_dipotong_tapi_jumlah_utuh():
    banyak = {f"m{i:02d}": _yakin() for i in range(12)}
    h = TB.turunkan_persaingan(banyak, {k: "untuk_ditinjau" for k in banyak})
    assert len(h["m00"]["mungkin_dobel_dengan"]) == TB.MAKS_DOBEL == 5 and h["m00"]["mungkin_dobel_jumlah"] == 11
    assert all(t["keyakinan"] == "periksa" for t in h.values())


def test_persaingan_mengubah_massal_karena_bukan_yakin_lagi():
    st = {"m1": "untuk_ditinjau", "m2": "untuk_ditinjau"}
    peta = TB.turunkan_persaingan({"m1": _yakin(), "m2": _yakin()}, st)
    assert all(not t["layak_massal"] for t in TB.tandai_layak_massal(peta, st).values())


# ====================================================================== bagian DB (fake conn; BACA SAJA)
import asyncio  # noqa: E402
import inspect  # noqa: E402
import re  # noqa: E402
import uuid  # noqa: E402

from fastapi import HTTPException  # noqa: E402

from app.services import kasbank_ringkas as KR  # noqa: E402

T_ = "t-uji"
REK = uuid.uuid4()


class _KT:
    """Conn palsu BACA SAJA: tak punya execute/transaction -> menulis = AttributeError."""
    def __init__(self, kand=(), akun=(), mutasi=None):
        self.kand, self.akun, self.mutasi, self.sql = list(kand), list(akun), mutasi, []

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM chart_of_accounts" in sql:
            return self.akun
        if "FROM bank_transactions bt" in sql:
            return self.kand
        raise AssertionError("SQL tak dikenal: " + sql[:80])

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        return self.mutasi


def _k(id_, tgl=date(2026, 10, 10), nominal="500000.00", arah="masuk", kontak="PT Sumber Rejeki Abadi", nomor="RCV-1"):
    return {"id": id_, "tanggal": tgl, "nominal": D(nominal), "arah": arah, "kontak_nama": kontak, "nomor": nomor, "jenis": "payment_received", "sumber": "RECEIVE_PAYMENT"}


def _b(id_="m1", tgl=date(2026, 10, 10), nominal="500000.00", arah="masuk", ket="TRSF E-BANKING CR 1010/FTSCY/WS95031 SUMBER REJEKI", status="untuk_ditinjau"):
    return {"id": id_, "tanggal": tgl, "nominal": D(nominal), "arah": arah, "keterangan": ket, "status": status}


def test_sql_kandidat_pagar_lengkap():
    s = TB.SQL_KANDIDAT
    assert "bt.tenant_id = $1" in s and "ba.tenant_id = bt.tenant_id" in s and "je.tenant_id = bt.tenant_id" in s
    assert "bt.status = 'POSTED'" in s and "je.status = 'POSTED'" in s and "COALESCE(bt.is_reconciled, false) = false" in s
    assert KR.sql_bt_tanpa_batal("bt", "$1") in s                                           # predikat batal SATU (BUG-141), bukan definisi baru
    assert "mutasi_bank_tautan" in s and "dilepas_pada IS NULL" in s and "t.tenant_id = $1" in s     # belum bertaut aktif
    assert "ba.coa_id" in s and "j.net <> 0" in s and "abs(j.net)" in s                      # nominal+arah dari JURNAL, bukan tanda amount
    assert "bt.amount" not in s and "je.source_type AS sumber" in s


def test_satu_definisi_potongan_dokumen_untuk_register_dan_penentu():
    assert KR.SQL_JOIN_DOKUMEN in KR.SQL_MUTASI and KR.SQL_JOIN_DOKUMEN in TB.SQL_KANDIDAT


def test_modul_tebak_tanpa_float_set_config_dan_semua_sql_bertenant():
    src = inspect.getsource(TB)
    assert "float(" not in src and "set_config" not in src and "INSERT" not in src and "UPDATE" not in src and "DELETE" not in src
    for nama, nilai in vars(TB).items():
        if nama.startswith("SQL_"):
            assert "tenant_id" in nilai, nama


def test_tebak_baca_saja_rentang_param_dan_hanya_status_yang_ditebak():
    k = _KT([_k("b1", date(2026, 10, 11))])
    peta = asyncio.run(TB.tebak(k, T_, REK, [_b("m1"), _b("m2", status="dicatat"), _b("m3", tgl=date(2026, 11, 2), nominal="1.00", status="mungkin_duplikat")]))
    assert sorted(peta) == ["m1", "m3"] and peta["m1"]["jenis"] == "cocok" and peta["m1"]["keyakinan"] == "yakin" and peta["m1"]["layak_massal"] is True
    sql, a = [(s, a) for s, a in k.sql if "FROM bank_transactions bt" in s][0]
    assert a == (T_, REK, date(2026, 10, 10) - TB.timedelta(days=90), date(2026, 11, 2) + TB.timedelta(days=20))
    assert not any("execute" in s.lower() for s, _ in k.sql)


def test_tebak_tanpa_baris_ditebak_tak_menyentuh_basis_data():
    k = _KT()
    assert asyncio.run(TB.tebak(k, T_, REK, [_b(status="dicatat")])) == {} and k.sql == []


def test_tebak_dua_baris_memilih_dokumen_sama_tak_ada_yang_layak_massal():
    k = _KT([_k("b1")])
    peta = asyncio.run(TB.tebak(k, T_, REK, [_b("m1"), _b("m2")]))
    assert peta["m1"]["layak_massal"] is False and peta["m2"]["layak_massal"] is False
    assert peta["m1"]["keyakinan"] == "periksa" and peta["m1"]["mungkin_dobel_dengan"] == ["m2"]          # persaingan: tak ada yang yakin


def test_tebak_bawaan_bca_resolve_akun_runtime_hanya_bila_perlu():
    ada = [{"account_code": "5-20850", "id": uuid.uuid4(), "name": "Biaya Administrasi Bank"}]
    k = _KT([], ada)
    peta = asyncio.run(TB.tebak(k, T_, REK, [_b("m1", nominal="11000.00", arah="keluar", ket="BIAYA ADM")]))
    assert peta["m1"]["jenis"] == "catat" and peta["m1"]["akun"]["kode"] == "5-20850" and peta["m1"]["layak_massal"] is False
    k2 = _KT([_k("b1")])
    asyncio.run(TB.tebak(k2, T_, REK, [_b("m1")]))
    assert not any("chart_of_accounts" in s for s, _ in k2.sql)
    k3 = _KT([], [])
    p3 = asyncio.run(TB.tebak(k3, T_, REK, [_b("m1", nominal="11000.00", arah="keluar", ket="BIAYA ADM")]))
    assert p3["m1"]["blok"]["code"] == "AKUN_BAWAAN_TAK_ADA"


def test_akun_bawaan_hanya_aktif_bukan_header_dan_bertenant():
    assert "tenant_id = $1" in TB.SQL_AKUN and "is_active = true" in TB.SQL_AKUN and "is_header = false" in TB.SQL_AKUN
    k = _KT([], [{"account_code": "4-90000", "id": uuid.uuid4(), "name": "Pendapatan Lain-lain"}, {"account_code": "5-80000", "id": uuid.uuid4(), "name": "Beban Pajak"}])
    hasil = asyncio.run(TB.akun_bawaan(k, T_))
    assert sorted(hasil) == ["bunga", "pajak_bunga"] and hasil["pajak_bunga"]["kode"] == "5-80000" and hasil["bunga"]["kode"] == "4-90000"


def test_tebak_gabungan_satu_bt_untuk_dua_baris():
    k = _KT([_k("b1", nominal="500000.00")])
    peta = asyncio.run(TB.tebak(k, T_, REK, [_b("m1", nominal="300000.00"), _b("m2", nominal="200000.00")]))
    assert peta["m1"]["jenis"] == "cocok" and peta["m1"]["keyakinan"] == "periksa" and peta["m2"]["bank_transaction_ids"] == ["b1"]
    assert peta["m1"]["layak_massal"] is False


# ---------------------------------------------------------------- /calon
def test_calon_bawaan_nominal_persis_dan_selisih_biaya_periksa():
    mutasi = {"id": uuid.uuid4(), "tanggal": date(2026, 10, 10), "keterangan": BANK["keterangan"], "arah": "masuk", "nominal": D("500000.00"), "status": "untuk_ditinjau"}
    k = _KT([_k("b1"), _k("b2", nominal="502500.00", nomor="RCV-2"), _k("b3", nominal="123.00", nomor="RCV-3"), _k("b4", arah="keluar", nomor="RCV-4")], mutasi=mutasi)
    h = asyncio.run(TB.calon(k, T_, REK, mutasi["id"]))
    assert [c["id"] for c in h["data"]] == ["b1", "b2"] and h["jumlah"] == 2
    assert h["data"][0]["keyakinan"] == "yakin" and h["data"][1]["keyakinan"] == "periksa" and h["data"][1]["selisih_nominal"] == "2500.00"
    assert h["data"][0]["tanggal"] == "2026-10-10" and h["data"][0]["nominal"] == "500000.00"


def test_calon_q_melebar_ke_seluruh_rentang_nomor_nama_nominal():
    mutasi = {"id": uuid.uuid4(), "tanggal": date(2026, 10, 10), "keterangan": "X", "arah": "masuk", "nominal": D("500000.00"), "status": "untuk_ditinjau"}
    kand = [_k("b1", nomor="RCV-1"), _k("b3", nominal="123456.00", nomor="DEP-9", kontak="Budi Santoso")]
    assert [c["id"] for c in asyncio.run(TB.calon(_KT(kand, mutasi=mutasi), T_, REK, mutasi["id"], "dep-9"))["data"]] == ["b3"]
    assert [c["id"] for c in asyncio.run(TB.calon(_KT(kand, mutasi=mutasi), T_, REK, mutasi["id"], "santoso"))["data"]] == ["b3"]
    assert [c["id"] for c in asyncio.run(TB.calon(_KT(kand, mutasi=mutasi), T_, REK, mutasi["id"], "123456"))["data"]] == ["b3"]


def test_calon_mutasi_tak_ada_404_dan_sql_bertenant_rekening():
    k = _KT(mutasi=None)
    with pytest.raises(HTTPException) as e:
        asyncio.run(TB.calon(k, T_, REK, uuid.uuid4()))
    assert e.value.status_code == 404 and e.value.detail["code"] == "MUTASI_TAK_ADA"
    assert "tenant_id = $1" in TB.SQL_MUTASI_SATU and "bank_account_id = $2" in TB.SQL_MUTASI_SATU and "status <> 'dipisah'" in TB.SQL_MUTASI_SATU


def test_ringkas_tebakan_bentuk_tetap():
    t = TB.ringkas_tebakan({**TB._kosong(), "layak_massal": False, "layak_massal_text": "x"})
    assert sorted(t) == sorted(["jenis", "keyakinan", "kalimat", "dasar", "bank_transaction_ids", "dokumen", "gabungan", "akun", "blok", "layak_massal", "layak_massal_text",
                                "mungkin_dobel_dengan", "mungkin_dobel_jumlah"])


def test_rute_calon_terdaftar_dan_izin_baca_kas_bank():
    from app.middleware.permission_middleware import izin_untuk_jalur
    from app.routers import mutasi_bank as MR
    r = {(x.path, tuple(sorted(x.methods))): x for x in MR.router.routes}
    k = ("/{bank_account_id}/mutasi-bank/{mutasi_id}/calon", ("GET",))
    assert k in r and r[k].response_model is not None
    assert izin_untuk_jalur(f"/api/bank-accounts/{REK}/mutasi-bank/{uuid.uuid4()}/calon", "GET") == ("kas_bank", "R")
