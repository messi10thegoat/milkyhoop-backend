"""BF-1c1 aksi Bank Feed (KONTRAK-bf1c1): cocokkan · kecualikan · lepaskan · riwayat. NOL jurnal. Satu penentu untuk pratinjau dan tulis; urutan kunci tetap;
sumber kebenaran 'bertaut' = mutasi_bank_tautan aktif (matched_statement_line_id TIDAK ditulis)."""
import asyncio
import inspect
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app.services import bankfeed_aksi as BA
from app.services import bankfeed_tebak as TB
from app.services.bayar_keluar_rencana import Blok

T = "t-uji"
REK = uuid.uuid4()
MID = uuid.uuid4()
USER = str(uuid.uuid4())
CTX = {"tenant_id": T, "user_id": USER}


@pytest.fixture(autouse=True)
def _tanpa_basis_data(monkeypatch):
    async def nama(conn, ids, cadangan_email=True):
        return {str(i): "Sinta" for i in ids}

    async def zona(conn, tid):
        return ZoneInfo("Asia/Jakarta")
    monkeypatch.setattr(BA, "nama_untuk", nama)
    monkeypatch.setattr(BA, "zona_tenant", zona)


def _m(**k):
    d = {"id": MID, "bank_account_id": REK, "tanggal": date(2026, 10, 10), "keterangan": "TRSF E-BANKING CR 1010/FTSCY/WS95031 SUMBER REJEKI", "arah": "masuk",
         "nominal": D("500000.00"), "status": "untuk_ditinjau", "cara": None, "alasan": None, "diputus_oleh": None, "diputus_pada": None, "duplikat_dari": None, "rekonsiliasi_id": None}
    d.update(k)
    return d


def _bt(nominal="500000.00", sudah="0", jumlah=0, arah="masuk", nomor="RCV-1", id_=None, tgl=date(2026, 10, 11)):
    return {"id": id_ or uuid.uuid4(), "tanggal": tgl, "nominal": D(nominal), "arah": arah, "kontak_nama": "PT Sumber Rejeki", "nomor": nomor, "jenis": "payment_received",
            "sumber": "RECEIVE_PAYMENT", "kontak_jenis": "pelanggan", "kontak_id": uuid.uuid4(), "dokumen_id": uuid.uuid4(), "dibuat_oleh": None, "dibuat_pada": None,
            "sudah_alokasi": D(sudah), "jumlah_tautan": jumlah}


def _tt(bt_id=None, nominal="500000.00", rekon=False, nomor="RCV-1"):
    return {"id": uuid.uuid4(), "bank_transaction_id": bt_id or uuid.uuid4(), "nominal_alokasi": D(nominal), "nomor": nomor, "jenis": "payment_received", "rekon": rekon, "kontak_nama": None}


class _K:
    """Conn palsu: baca/tulis dicatat; tak mengenal SQL jurnal sama sekali."""
    def __init__(self, mutasi=None, bts=(), tautan=(), putus=True, kembali=True):
        self.mutasi, self.bts, self.tautan, self.putus, self.kembali = mutasi, list(bts), list(tautan), putus, kembali
        self.sql = []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        if sql.startswith("SELECT id, bank_account_id, tanggal"):
            return self.mutasi
        if sql.startswith("UPDATE mutasi_bank SET status = $4"):
            return {"id": MID} if self.putus else None
        if sql.startswith("UPDATE mutasi_bank SET status = $3"):
            return {"id": MID} if self.kembali else None
        raise AssertionError("fetchrow SQL tak dikenal: " + sql[:70])

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        if "bt.id = ANY($3::uuid[])" in sql:
            return self.bts
        if "FROM mutasi_bank_tautan t JOIN bank_transactions" in sql:
            return self.tautan
        raise AssertionError("fetch SQL tak dikenal: " + sql[:70])

    async def execute(self, sql, *a):
        self.sql.append((sql, a))

    def teks(self):
        return [s for s, _ in self.sql]


def _preview(k, aksi, body):
    return asyncio.run(BA.RENCANA[aksi](k, T, REK, MID, body, Blok(True), False))


def _kode(h):
    return [b["code"] for b in h["blocks"]]


def _tulis(k, aksi, body):
    return asyncio.run(BA.tulis(k, CTX, REK, MID, aksi, body))


def _kodeg(e):
    """Kode galat: GalatBerkode (blok penentu) menyimpan `.code`; HTTPException dgn detail dict menyimpan di detail['code']."""
    return e.code if hasattr(e, "code") else e.detail["code"]


def _tulisan(k):
    """Hanya pernyataan TULIS (bacaan boleh menyentuh tabel jurnal untuk definisi kandidat)."""
    return [s for s in k.teks() if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]


def _galat(k, aksi, body):
    with pytest.raises(HTTPException) as e:
        _tulis(k, aksi, body)
    return e.value


# ---------------------------------------------------------------- cocokkan: pratinjau
def test_cocokkan_satu_dokumen_nominal_sama_alokasi_penuh_dan_nol_jurnal():
    bt = _bt()
    h = _preview(_K(_m(), [bt]), "cocokkan", {"bank_transaction_ids": [str(bt["id"])]})
    assert h["blocks"] == [] and len(h["pasangan"]) == 1 and h["alokasi"][0]["nominal"] == D("500000.00")
    assert "RCV-1" in h["kalimat"] and "Tidak ada jurnal baru" in h["kalimat"] and h["versi"]
    out = BA._keluaran(h, "cocokkan", {"bank_transaction_ids": [str(bt["id"])]})
    assert out["ok"] is True and out["jurnal"] == [] and out["payload"]["versi"] == h["versi"]
    assert out["pasangan"][0]["alokasi_tampil"] == "Rp 500.000" and out["mutasi"]["status_text"] == "Untuk ditinjau"


def test_cocokkan_satu_dokumen_lebih_besar_dialokasikan_sebesar_baris():
    bt = _bt("800000.00")   # satu dokumen untuk beberapa baris bank (n:m)
    h = _preview(_K(_m(), [bt]), "cocokkan", {"bank_transaction_ids": [str(bt["id"])]})
    assert h["blocks"] == [] and h["alokasi"][0]["nominal"] == D("500000.00")


def test_cocokkan_gabungan_dua_dokumen_jumlah_sama_dengan_baris():
    a, b = _bt("300000.00", nomor="RCV-1"), _bt("200000.00", nomor="RCV-2")
    h = _preview(_K(_m(), [a, b]), "cocokkan", {"bank_transaction_ids": [str(a["id"]), str(b["id"])]})
    assert h["blocks"] == [] and [x["nominal"] for x in h["alokasi"]] == [D("300000.00"), D("200000.00")] and "RCV-1, RCV-2" in h["kalimat"]


def test_cocokkan_nominal_beda_diblok_dengan_selisih():
    a, b = _bt("300000.00"), _bt("150000.00", nomor="RCV-2")
    h = _preview(_K(_m(), [a, b]), "cocokkan", {"bank_transaction_ids": [str(a["id"]), str(b["id"])]})
    assert _kode(h) == ["NOMINAL_BEDA"] and "50.000" in h["blocks"][0]["message"] and "selisih" in h["blocks"][0]["message"]


def test_cocokkan_alokasi_eksplisit_dan_tak_boleh_melebihi_sisa():
    bt = _bt("800000.00", sudah="400000.00", jumlah=1)    # sisa 400.000
    body = {"bank_transaction_ids": [str(bt["id"])], "alokasi": [{"bank_transaction_id": str(bt["id"]), "nominal": "500000.00"}]}
    assert _kode(_preview(_K(_m(), [bt]), "cocokkan", body)) == ["ALOKASI_MELEBIHI_SISA"]
    kecil = _m(nominal=D("400000.00"))
    assert _preview(_K(kecil, [bt]), "cocokkan", {"bank_transaction_ids": [str(bt["id"])]})["blocks"] == []   # bawaan = sisa >= baris -> baris


def test_cocokkan_arah_berlawanan_dokumen_penuh_dan_dokumen_hilang():
    bt = _bt(arah="keluar")
    assert _kode(_preview(_K(_m(), [bt]), "cocokkan", {"bank_transaction_ids": [str(bt["id"])]})) == ["ARAH_BEDA"]
    penuh = _bt(jumlah=3)
    assert _kode(_preview(_K(_m(), [penuh]), "cocokkan", {"bank_transaction_ids": [str(penuh["id"])]})) == ["DOKUMEN_PENUH"]
    assert _kode(_preview(_K(_m(), []), "cocokkan", {"bank_transaction_ids": [str(uuid.uuid4())]})) == ["DOKUMEN_TAK_BISA_DICOCOKKAN"]


@pytest.mark.parametrize("ids,kode", [([], "DOKUMEN_JUMLAH"), ([str(uuid.uuid4()) for _ in range(5)], "DOKUMEN_JUMLAH"), (["bukan-uuid"], "UUID_TIDAK_SAH")])
def test_cocokkan_jumlah_dan_uuid_dokumen_divalidasi(ids, kode):
    assert _kode(_preview(_K(_m(), []), "cocokkan", {"bank_transaction_ids": ids})) == [kode]


def test_cocokkan_baris_sudah_diputus_dan_mutasi_tak_ada():
    diputus = _m(status="dicatat", cara="cocok", diputus_oleh=uuid.uuid4(), diputus_pada=datetime(2026, 11, 10, 7, 5, tzinfo=timezone.utc))
    h = _preview(_K(diputus), "cocokkan", {"bank_transaction_ids": [str(uuid.uuid4())]})
    assert _kode(h) == ["SUDAH_DIPUTUS"] and "Sinta" in h["blocks"][0]["message"] and "14.05" in h["blocks"][0]["message"]
    assert _kode(_preview(_K(None), "cocokkan", {"bank_transaction_ids": [str(uuid.uuid4())]})) == ["MUTASI_TAK_ADA"]


def test_cocokkan_mungkin_duplikat_boleh_dicocokkan():
    bt = _bt()
    assert _preview(_K(_m(status="mungkin_duplikat"), [bt]), "cocokkan", {"bank_transaction_ids": [str(bt["id"])]})["blocks"] == []


# ---------------------------------------------------------------- cocokkan: tulis
def test_tulis_cocokkan_urutan_kunci_lalu_tautan_lalu_putus_lalu_riwayat_tanpa_jurnal():
    a, b = _bt("300000.00", nomor="RCV-1"), _bt("200000.00", nomor="RCV-2")
    body = {"bank_transaction_ids": [str(b["id"]), str(a["id"])]}
    body["versi"] = _preview(_K(_m(), [a, b]), "cocokkan", body)["versi"]
    k = _K(_m(), [a, b])
    hasil = _tulis(k, "cocokkan", body)
    t = k.teks()
    i_mid = next(i for i, s in enumerate(t) if "pg_advisory_xact_lock" in s and k.sql[i][1] == (f"MUTASI_BANK:{MID}",))
    i_baca = next(i for i, s in enumerate(t) if "FOR UPDATE" in s)
    i_bt = [i for i, (s, a_) in enumerate(k.sql) if "pg_advisory_xact_lock" in s and str(a_[0]).startswith(f"MUTASI_BT:{T}:")]
    i_ins = [i for i, s in enumerate(t) if s.startswith("INSERT INTO mutasi_bank_tautan")]
    i_putus = next(i for i, s in enumerate(t) if s.startswith("UPDATE mutasi_bank SET status = $4"))
    i_audit = next(i for i, s in enumerate(t) if s.startswith("INSERT INTO audit_logs"))
    assert i_mid < i_baca < min(i_bt) and max(i_bt) < min(i_ins) and max(i_ins) < i_putus < i_audit      # MUTASI_BANK -> FOR UPDATE -> MUTASI_BT -> tautan -> putus -> riwayat
    ids_kunci = [k.sql[i][1][0].split(":")[-1] for i in i_bt]
    assert len(ids_kunci) == 2 and ids_kunci == sorted(ids_kunci)               # kunci dokumen urut id (cegah deadlock)
    assert len(i_ins) == 2 and not any("journal" in s.lower() for s in _tulisan(k))          # NOL jurnal (tulisan; bacaan kandidat boleh membaca jurnal)
    sql_putus, arg = k.sql[i_putus]
    assert arg[3:6] == ("dicatat", "cocok", None) and hasil["id"] == str(MID) and "RCV-1" in hasil["kalimat"]


def test_tulis_cocokkan_versi_basi_409_tanpa_menulis():
    bt = _bt()
    k = _K(_m(), [bt])
    e = _galat(k, "cocokkan", {"bank_transaction_ids": [str(bt["id"])], "versi": "basi"})
    assert e.status_code == 409 and e.detail["code"] == "RENCANA_BERUBAH"
    assert not any(s.startswith(("INSERT", "UPDATE")) for s in k.teks())


def test_tulis_cocokkan_status_dibaca_ulang_dalam_kunci_dan_balapan_409():
    diputus = _m(status="dicatat", cara="cocok")
    k = _K(diputus, [_bt()])
    e = _galat(k, "cocokkan", {"bank_transaction_ids": [str(uuid.uuid4())]})
    assert e.status_code == 409 and _kodeg(e) == "SUDAH_DIPUTUS" and any("FOR UPDATE" in s for s in k.teks())
    bt = _bt()
    k2 = _K(_m(), [bt], putus=False)   # kalah balapan: UPDATE bersyarat status tak mengenai baris
    e2 = _galat(k2, "cocokkan", {"bank_transaction_ids": [str(bt["id"])]})
    assert _kodeg(e2) == "SUDAH_DIPUTUS"


def test_tulis_cocokkan_blok_apa_pun_nol_tulisan():
    bt = _bt("300000.00")
    k = _K(_m(), [bt])
    e = _galat(k, "cocokkan", {"bank_transaction_ids": [str(bt["id"])]})
    assert e.status_code == 422 and _kodeg(e) == "NOMINAL_BEDA" and _tulisan(k) == []


def test_pratinjau_memakai_transaksi_read_only():
    catatan = {}

    class _P:
        def acquire(self):
            return self

        async def __aenter__(self):
            return _KT()

        async def __aexit__(self, *a):
            return False

    class _KT(_K):
        def __init__(self):
            super().__init__(_m(), [_bt()])

        def transaction(self, readonly=False):
            catatan["readonly"] = readonly
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False
    h = asyncio.run(BA.pratinjau(_P(), T, REK, MID, "kecualikan", {"alasan": "duplikat"}))
    assert catatan["readonly"] is True and h["data"]["ok"] is True and h["data"]["jurnal"] == []


# ---------------------------------------------------------------- kecualikan
def test_kecualikan_alasan_dan_catatan_wajib_untuk_lain():
    assert _kode(_preview(_K(_m()), "kecualikan", {"alasan": "ngawur"})) == ["ALASAN_TAK_SAH"]
    assert _kode(_preview(_K(_m()), "kecualikan", {"alasan": "lain"})) == ["CATATAN_WAJIB"]
    h = _preview(_K(_m()), "kecualikan", {"alasan": "lain", "catatan": "salah impor bulan lalu"})
    assert h["blocks"] == [] and "Lain-lain" in h["kalimat"] and "Tidak ada jurnal" in h["kalimat"]


def test_kecualikan_tulis_status_cara_alasan_dan_riwayat():
    k = _K(_m(status="mungkin_duplikat"))
    r = _tulis(k, "kecualikan", {"alasan": "lain", "catatan": "salah impor"})
    sql, a = next((s, a) for s, a in k.sql if s.startswith("UPDATE mutasi_bank SET status = $4"))
    assert a[3:6] == ("dikecualikan", "kecualikan", "lain: salah impor") and a[6] == list(BA.DITINJAU)
    assert any(s.startswith("INSERT INTO audit_logs") for s in k.teks()) and r["id"] == str(MID)


def test_kecualikan_baris_sudah_diputus_diblok():
    assert _kode(_preview(_K(_m(status="dikecualikan", cara="kecualikan")), "kecualikan", {"alasan": "duplikat"})) == ["SUDAH_DIPUTUS"]


# ---------------------------------------------------------------- lepaskan
def test_lepaskan_hanya_keputusan_bank_feed():
    assert _kode(_preview(_K(_m()), "lepaskan", {})) == ["BELUM_DIPUTUS"]
    assert _kode(_preview(_K(_m(status="dicatat", cara="catat")), "lepaskan", {})) == ["PAKAI_BATALKAN"]
    assert _kode(_preview(_K(_m(status="dicatat", cara="pribadi")), "lepaskan", {})) == ["PAKAI_BATALKAN"]
    assert _kode(_preview(_K(_m(status="dicatat", cara="cocok", rekonsiliasi_id=uuid.uuid4())), "lepaskan", {})) == ["SUDAH_DIREKONSILIASI"]
    assert _kode(_preview(_K(_m(status="dicatat", cara="cocok"), tautan=[_tt(rekon=True)]), "lepaskan", {})) == ["SUDAH_DIREKONSILIASI"]


def test_lepaskan_cocok_kembali_ke_untuk_ditinjau_dan_dokumen_tetap_tercatat():
    h = _preview(_K(_m(status="dicatat", cara="cocok"), tautan=[_tt(nomor="RCV-9")]), "lepaskan", {})
    assert h["blocks"] == [] and h["kembali_ke"] == "untuk_ditinjau" and "RCV-9 tetap tercatat" in h["kalimat"] and "Untuk ditinjau" in h["kalimat"]
    asal_dup = _preview(_K(_m(status="dikecualikan", cara="kecualikan", duplikat_dari=uuid.uuid4())), "lepaskan", {})
    assert asal_dup["kembali_ke"] == "mungkin_duplikat" and asal_dup["tautan"] == []


def test_lepaskan_tulis_melepas_tautan_bukan_menghapus_lalu_mengembalikan_status():
    k = _K(_m(status="dicatat", cara="cocok"), tautan=[_tt()])
    _tulis(k, "lepaskan", {})
    t = k.teks()
    i_lepas = next(i for i, s in enumerate(t) if s.startswith("UPDATE mutasi_bank_tautan SET dilepas_oleh"))
    i_kembali = next(i for i, s in enumerate(t) if s.startswith("UPDATE mutasi_bank SET status = $3"))
    assert i_lepas < i_kembali < next(i for i, s in enumerate(t) if s.startswith("INSERT INTO audit_logs"))
    assert not any("DELETE" in s.upper() for s in t) and not any("journal" in s.lower() for s in _tulisan(k))


# ---------------------------------------------------------------- riwayat
def test_riwayat_urut_diimpor_dulu_nama_dan_waktu_dari_server():
    pada = datetime(2026, 11, 10, 7, 5, tzinfo=timezone.utc)

    class _R(_K):
        async def fetchrow(self, sql, *a):
            if sql.startswith("SELECT m.dibuat_pada"):
                return {"pada": datetime(2026, 11, 9, 2, 0, tzinfo=timezone.utc), "oleh": uuid.uuid4()}
            return await super().fetchrow(sql, *a)

        async def fetch(self, sql, *a):
            if "FROM audit_logs" in sql:
                return [{"user_id": USER, "event": "BANK_FEED_COCOKKAN", "metadata": '{"ringkas": "Dicocokkan dengan RCV-1"}', "pada": pada},
                        {"user_id": USER, "event": "BANK_FEED_LEPASKAN", "metadata": {"ringkas": "Keputusan dilepas (cocok)"}, "pada": pada}]
            return await super().fetch(sql, *a)
    h = asyncio.run(BA.riwayat(_R(_m()), T, REK, MID))
    assert [x["aksi"] for x in h] == ["diimpor", "cocokkan", "lepaskan"] and h[1]["oleh_text"] == "Sinta" and h[1]["ringkas"] == "Dicocokkan dengan RCV-1"
    assert h[1]["pada_text"].endswith("14.05") and h[0]["aksi_text"] == "Diimpor"
    with pytest.raises(HTTPException) as e:
        asyncio.run(BA.riwayat(_K(None), T, REK, MID))
    assert e.value.status_code == 404 and e.value.detail["code"] == "MUTASI_TAK_ADA"


# ---------------------------------------------------------------- kebersihan modul + SQL kandidat bersama
def test_semua_sql_bertenant_tanpa_hapus_jurnal_float_set_config_dan_kolom_rekon_lama():
    src = inspect.getsource(BA)
    assert "float(" not in src and "set_config" not in src
    for nama, nilai in vars(BA).items():
        if nama.startswith("SQL_"):
            assert "tenant_id" in nilai, nama
            assert "DELETE" not in nilai.upper() and "journal" not in nilai.lower() and "matched_statement_line_id" not in nilai, nama


def test_sql_bt_pilihan_memakai_definisi_kandidat_yang_sama_dengan_tebak():
    from app.services import kasbank_ringkas as KR
    s = TB.SQL_BT_PILIHAN
    assert KR.sql_bt_tanpa_batal("bt", "$1") in s and KR.sql_bt_tanpa_batal("bt", "$1") in TB.SQL_KANDIDAT
    assert "bt.tenant_id = $1" in s and "bt.status = 'POSTED'" in s and "abs(j.net) AS nominal" in s and "bt.amount" not in s
    assert "bt.id = ANY($3::uuid[])" in s and "dilepas_pada IS NULL" in s and "sudah_alokasi" in s and "jumlah_tautan" in s
    assert "NOT EXISTS (SELECT 1 FROM mutasi_bank_tautan" not in s      # cocokkan menghitung kapasitas, bukan menyaring


def test_rute_aksi_terdaftar_bersekema_izin_dan_inventaris():
    from app.middleware.permission_middleware import izin_untuk_jalur
    from app.routers import mutasi_bank as MR
    r = {(x.path, tuple(sorted(x.methods))): x for x in MR.router.routes}
    dasar = "/{bank_account_id}/mutasi-bank"
    for aksi in ("cocokkan", "kecualikan", "lepaskan"):
        for akhir in ("", "/preview"):
            kunci = (f"{dasar}/{{mutasi_id}}/{aksi}{akhir}", ("POST",))
            assert kunci in r and r[kunci].response_model is not None, kunci
    for kunci in ((f"{dasar}/tak-di-bank", ("GET",)), (f"{dasar}/{{mutasi_id}}/riwayat", ("GET",))):
        assert kunci in r and r[kunci].response_model is not None, kunci
    mid, rek = uuid.uuid4(), uuid.uuid4()
    for aksi in ("cocokkan", "kecualikan", "lepaskan"):
        assert izin_untuk_jalur(f"/api/bank-accounts/{rek}/mutasi-bank/{mid}/{aksi}", "POST") == ("kas_bank", "C")
        assert izin_untuk_jalur(f"/api/bank-accounts/{rek}/mutasi-bank/{mid}/{aksi}/preview", "POST") == ("kas_bank", "C")
    assert izin_untuk_jalur(f"/api/bank-accounts/{rek}/mutasi-bank/tak-di-bank", "GET") == ("kas_bank", "R")
    assert izin_untuk_jalur(f"/api/bank-accounts/{rek}/mutasi-bank/{mid}/riwayat", "GET") == ("kas_bank", "R")
    inv = open(inspect.getsourcefile(MR).replace("app/routers/mutasi_bank.py", "tests/unit/inventaris_rute_tulis.txt")).read()
    for aksi in ("cocokkan", "kecualikan", "lepaskan"):
        for akhir in ("", "/preview"):
            assert re.search(rf"^POST /api/bank-accounts/\{{bank_account_id\}}/mutasi-bank/\{{mutasi_id\}}/{aksi}{re.escape(akhir)}$", inv, re.M), (aksi, akhir)


def test_rute_tulis_memakai_idempotensi_per_aksi_dan_tanpa_str_e():
    from app.routers import mutasi_bank as MR
    src = inspect.getsource(MR._tulis_aksi)
    assert "idem_buat.mulai_aksi" in src and 'f"MUTASI_{aksi.upper()}"' in src and "idem_buat.simpan" in src and "str(e)" not in src
    assert "request.json()" not in inspect.getsource(MR)
