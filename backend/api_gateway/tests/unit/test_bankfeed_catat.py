"""BF-1c2 aksi CATAT (uang_masuk · uang_keluar · pribadi) dan BATALKAN-CATATAN (KONTRAK-bf1c2). Jurnal HANYA lewat penulis Uang Masuk/Keluar yang ada (KT.rencana_tx_manual +
KT.tulis_tx_manual hasil ekstraksi); batal = KT.rencana_void_manual + KT.tulis_void_manual (jurnal PEMBALIK, Law 2). Putusan pemilik LANGSUNG 12 Nov: pribadi masuk Cr 3-10100 Modal
Pemilik; pribadi keluar Dr 3-40000 Prive. TES MERAH DULU (bukti: log merah sebelum kode)."""
import asyncio
import inspect
import re
import uuid
from datetime import date
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app.services import bankfeed_aksi as BA
from app.services import kasbank_tulis as KT
from app.services.bayar_keluar_rencana import Blok

T = "t-uji"
REK = uuid.uuid4()
MID = uuid.uuid4()
BT = uuid.uuid4()
COA_BANK, LAWAN, PRIVE, MODAL = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
USER = str(uuid.uuid4())
CTX = {"tenant_id": T, "user_id": USER}


def _m(**k):
    d = {"id": MID, "bank_account_id": REK, "tanggal": date(2026, 10, 10), "keterangan": "TRSF E-BANKING DB 1010/FTSCY/WS95031 SETORAN KAS", "arah": "keluar",
         "nominal": D("500000.00"), "status": "untuk_ditinjau", "cara": None, "alasan": None, "diputus_oleh": None, "diputus_pada": None, "duplikat_dari": None, "rekonsiliasi_id": None}
    d.update(k)
    return d


def _tt():
    return {"id": uuid.uuid4(), "bank_transaction_id": BT, "nominal_alokasi": D("500000.00"), "nomor": "MT-AB12CD34", "jenis": "withdrawal", "rekon": False, "kontak_nama": None}


class _K:
    def __init__(self, mutasi=None, akun=None, tautan=(), putus=True, kembali=True):
        self.mutasi, self.akun, self.tautan, self.putus, self.kembali = mutasi, akun, list(tautan), putus, kembali
        self.sql, self.urut = [], []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        if sql.startswith("SELECT id, bank_account_id, tanggal"):
            self.urut.append("baca_mutasi")
            return self.mutasi
        if sql.startswith("SELECT id, account_code, name FROM chart_of_accounts"):
            return self.akun
        if sql.startswith("SELECT bt.id AS bt_id, je.source_type"):      # BF-1c3: dokumen hasil catatan dikenali menurut je.source_type
            return {"bt_id": BT, "source_type": "BANK_TRANSACTION", "source_id": str(BT), "nomor_jurnal": None, "reference_type": None, "reference_id": None}
        if sql.startswith("UPDATE mutasi_bank SET status = $4"):
            self.urut.append("putus")
            return {"id": MID} if self.putus else None
        if sql.startswith("UPDATE mutasi_bank SET status = $3"):
            self.urut.append("kembali")
            return {"id": MID} if self.kembali else None
        raise AssertionError("fetchrow SQL tak dikenal: " + sql[:80])

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM mutasi_bank_tautan t JOIN bank_transactions" in sql:
            return self.tautan
        if "FROM chart_of_accounts" in sql:
            return []
        raise AssertionError("fetch SQL tak dikenal: " + sql[:80])

    async def execute(self, sql, *a):
        self.sql.append((sql, a))
        if "pg_advisory_xact_lock" in sql:
            self.urut.append("kunci:" + str(a[0]).split(":")[0])
        elif sql.startswith("INSERT INTO mutasi_bank_tautan"):
            self.urut.append("tautan")
        elif sql.startswith("UPDATE mutasi_bank_tautan SET dilepas_oleh"):
            self.urut.append("lepas_tautan")
        elif sql.startswith("INSERT INTO audit_logs"):
            self.urut.append("riwayat")


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    async def nama(conn, ids, cadangan_email=True):
        return {str(i): "Sinta" for i in ids}

    async def zona(conn, tid):
        return ZoneInfo("Asia/Jakarta")
    monkeypatch.setattr(BA, "nama_untuk", nama)
    monkeypatch.setattr(BA, "zona_tenant", zona)


def _rencana_stub(monkeypatch, blocks=None, selisih=D(0), saldo=D("2000000")):
    """Penulis manual dipalsukan: yang diuji di sini = orkestrasi aksi Bank Feed; penulis aslinya diuji test_kasbank_*. `selisih` != 0 = jurnal penulis TAK sama dgn baris bank."""
    panggil = {"rencana": [], "tulis": [], "void_rencana": [], "void_tulis": []}

    async def rencana_tx_manual(conn, tid, ba_id, body, pratinjau):
        conn.urut.append("rencana_tx_manual")
        panggil["rencana"].append((body, pratinjau))
        masuk, jumlah = body.direction == "in", D(body.amount)
        lawan, bank = uuid.UUID(body.contra_account_id), jumlah + selisih
        baris = [(COA_BANK, bank, D(0), body.description), (lawan, D(0), jumlah, body.description)] if masuk else \
                [(lawan, jumlah, D(0), body.description), (COA_BANK, D(0), bank, body.description)]
        a = {"id": REK, "account_name": "BCA UJI", "saldo": saldo, "coa_id": COA_BANK}
        return {"blocks": list(blocks or []), "akun": a, "baris": baris, "saldo": [KT._saldo(a, jumlah if masuk else -jumlah)]}

    async def tulis_tx_manual(conn, ctx, ba_id, body, r):
        conn.urut.append("tulis_tx_manual")
        panggil["tulis"].append(body)
        return {"bank_txn_id": BT, "journal_id": uuid.uuid4(), "journal_number": "MT-AB12CD34"}

    async def kunci_tx_manual(conn, ba_id):
        conn.urut.append("kunci:BANK_TX_MANUAL")

    async def rencana_void_manual(conn, tid, tx_id, alasan, bl, kunci=False):
        conn.urut.append("rencana_void_manual")
        panggil["void_rencana"].append((tx_id, alasan, bl.pratinjau, kunci))
        for b in blocks or []:
            bl.galat(b["code"], b.get("status", 400), b["message"])
        je = {"id": uuid.uuid4(), "journal_number": "MT-AB12CD34"}
        return {"blocks": bl.daftar, "baris": [(COA_BANK, D("500000"), D(0), "balik"), (LAWAN, D(0), D("500000"), "balik")], "saldo": [], "tx": {"journal_id": je["id"]}, "je": je, "jumlah": D("500000")}

    async def tulis_void_manual(conn, ctx, tx_id, alasan, r):
        conn.urut.append("tulis_void_manual")
        panggil["void_tulis"].append((tx_id, alasan))
        return {"reversal_journal_id": uuid.uuid4(), "reversal_number": "RV-MT-AB12CD34"}

    async def kunci_void(conn, tx_id):
        conn.urut.append("kunci:BANK_TX_VOID")
    monkeypatch.setattr(KT, "rencana_tx_manual", rencana_tx_manual)
    monkeypatch.setattr(KT, "tulis_tx_manual", tulis_tx_manual, raising=False)
    monkeypatch.setattr(KT, "kunci_tx_manual", kunci_tx_manual, raising=False)
    monkeypatch.setattr(KT, "rencana_void_manual", rencana_void_manual)
    monkeypatch.setattr(KT, "tulis_void_manual", tulis_void_manual, raising=False)
    monkeypatch.setattr(KT, "kunci_void_manual", kunci_void, raising=False)
    return panggil


def _prev(k, aksi, body):
    return asyncio.run(BA.RENCANA[aksi](k, T, REK, MID, body, Blok(True), False))


def _kode(h):
    return [b["code"] for b in h["blocks"]]


def _tulis(k, aksi, body):
    return asyncio.run(BA.tulis(k, CTX, REK, MID, aksi, body))


def _kodeg(e):
    return e.code if hasattr(e, "code") else e.detail["code"]


def _galat(k, aksi, body):
    with pytest.raises(HTTPException) as e:
        _tulis(k, aksi, body)
    return e.value


def _akun_row(kode, nama):
    return {"id": PRIVE if kode == "3-40000" else MODAL, "account_code": kode, "name": nama}


# ====================================================================== CATAT: validasi
def test_jenis_tak_dikenal_diblok_berkode_bukan_diam(monkeypatch):      # BF-1d: semua jenis kini tersedia; yang tak dikenal tetap diblok
    _rencana_stub(monkeypatch)
    h = _prev(_K(_m()), "catat", {"jenis": "jenis_ngawur"})
    assert _kode(h) == ["JENIS_TAK_SAH"]


def test_jenis_harus_sesuai_arah_baris(monkeypatch):
    _rencana_stub(monkeypatch)
    assert _kode(_prev(_K(_m(arah="keluar")), "catat", {"jenis": "uang_masuk", "akun_id": str(LAWAN)})) == ["JENIS_TAK_SESUAI_ARAH"]
    assert _kode(_prev(_K(_m(arah="masuk")), "catat", {"jenis": "uang_keluar", "akun_id": str(LAWAN)})) == ["JENIS_TAK_SESUAI_ARAH"]


def test_uang_masuk_keluar_wajib_akun_lawan(monkeypatch):
    _rencana_stub(monkeypatch)
    assert _kode(_prev(_K(_m()), "catat", {"jenis": "uang_keluar"})) == ["AKUN_WAJIB"]


def test_nominal_berkoma_diblok_karena_penulis_manual_rupiah_bulat(monkeypatch):
    _rencana_stub(monkeypatch)
    h = _prev(_K(_m(nominal=D("500000.50"))), "catat", {"jenis": "uang_keluar", "akun_id": str(LAWAN)})
    assert _kode(h) == ["NOMINAL_BERSEN"]


def test_baris_sudah_diputus_atau_tak_ada(monkeypatch):
    _rencana_stub(monkeypatch)
    assert _kode(_prev(_K(_m(status="dicatat", cara="cocok")), "catat", {"jenis": "uang_keluar", "akun_id": str(LAWAN)})) == ["SUDAH_DIPUTUS"]
    assert _kode(_prev(_K(None), "catat", {"jenis": "uang_keluar", "akun_id": str(LAWAN)})) == ["MUTASI_TAK_ADA"]


# ====================================================================== CATAT: pribadi (putusan pemilik 12 Nov)
def test_pribadi_keluar_ke_prive_3_40000_dan_masuk_ke_modal_pemilik_3_10100(monkeypatch):
    p = _rencana_stub(monkeypatch)
    k1 = _K(_m(arah="keluar"), akun=_akun_row("3-40000", "Prive"))
    h1 = _prev(k1, "catat", {"jenis": "pribadi"})
    assert h1["blocks"] == [] and h1["akun"]["kode"] == "3-40000" and p["rencana"][-1][0].contra_account_id == str(PRIVE) and p["rencana"][-1][0].direction == "out"
    sql, a = next((s, a) for s, a in k1.sql if s.startswith("SELECT id, account_code, name FROM chart_of_accounts"))
    assert a == (T, "3-40000") and "is_active = true" in sql and "is_header = false" in sql and "tenant_id = $1" in sql
    k2 = _K(_m(arah="masuk"), akun=_akun_row("3-10100", "Modal Pemilik"))
    h2 = _prev(k2, "catat", {"jenis": "pribadi"})
    assert h2["blocks"] == [] and h2["akun"]["kode"] == "3-10100" and p["rencana"][-1][0].contra_account_id == str(MODAL) and p["rencana"][-1][0].direction == "in"


def test_pribadi_akun_tak_ada_blok_berkode_tanpa_membuat_akun(monkeypatch):
    _rencana_stub(monkeypatch)
    assert _kode(_prev(_K(_m(arah="keluar"), akun=None), "catat", {"jenis": "pribadi"})) == ["AKUN_PRIVE_TAK_ADA"]
    h = _prev(_K(_m(arah="masuk"), akun=None), "catat", {"jenis": "pribadi"})
    assert _kode(h) == ["AKUN_SETORAN_TAK_ADA"] and "3-10100" in h["blocks"][0]["message"]


def test_pribadi_akun_ditetapkan_sistem_pengguna_tak_boleh_memilih(monkeypatch):
    _rencana_stub(monkeypatch)
    assert _kode(_prev(_K(_m(arah="keluar"), akun=_akun_row("3-40000", "Prive")), "catat", {"jenis": "pribadi", "akun_id": str(LAWAN)})) == ["AKUN_TETAP"]


def test_pemetaan_akun_pribadi_terkunci_putusan_pemilik():
    assert BA.AKUN_PRIBADI == {"keluar": "3-40000", "masuk": "3-10100"}


# ====================================================================== CATAT: penulis manual dipanggil dgn isian yang benar
def test_isian_ke_penulis_manual_arah_nominal_tanggal_deskripsi_kontak(monkeypatch):
    p = _rencana_stub(monkeypatch)
    _prev(_K(_m(arah="keluar")), "catat", {"jenis": "uang_keluar", "akun_id": str(LAWAN), "kontak": "Budi", "catatan": "beli lakban"})
    body, pratinjau = p["rencana"][-1]
    assert (body.direction, body.amount, body.transaction_date, body.description, body.contra_account_id, body.contact_name) == ("out", 500000, date(2026, 10, 10), "beli lakban", str(LAWAN), "Budi")
    assert pratinjau is True
    _prev(_K(_m(arah="masuk")), "catat", {"jenis": "uang_masuk", "akun_id": str(LAWAN)})
    body, _ = p["rencana"][-1]
    assert body.direction == "in" and body.description.startswith("TRSF E-BANKING") and len(body.description) <= 500


def test_pratinjau_memuat_jurnal_saldo_dan_kalimat_dari_penulis_yang_sama(monkeypatch):
    _rencana_stub(monkeypatch)
    h = _prev(_K(_m(arah="keluar")), "catat", {"jenis": "uang_keluar", "akun_id": str(LAWAN)})
    out = asyncio.run(BA._keluaran_async(_K(_m()), T, h, "catat", {"jenis": "uang_keluar"}))
    assert out["ok"] is True and len(out["jurnal"]) == 2 and out["jurnal"][0]["debit"] == "500000.00" and out["saldo"][0]["sesudah"] == "1500000.00"
    assert "Uang keluar" in out["kalimat"] and "Rp 500.000" in out["kalimat"]


def test_blok_penulis_manual_diteruskan_di_pratinjau(monkeypatch):
    _rencana_stub(monkeypatch, blocks=[{"code": "PERIODE_TERTUTUP", "message": "Periode ditutup.", "status": 400}])
    h = _prev(_K(_m(arah="keluar")), "catat", {"jenis": "uang_keluar", "akun_id": str(LAWAN)})
    assert "PERIODE_TERTUTUP" in _kode(h)


def test_nominal_jurnal_diverifikasi_ulang_terhadap_nominal_baris(monkeypatch):
    _rencana_stub(monkeypatch, selisih=D("-1"))        # penulis menghasilkan jurnal yang tak sama dgn baris bank
    h = _prev(_K(_m(arah="keluar")), "catat", {"jenis": "uang_keluar", "akun_id": str(LAWAN)})
    assert "NOMINAL_JURNAL_TAK_SAMA" in _kode(h)


# ====================================================================== CATAT: tulis
def test_tulis_catat_urutan_kunci_penulis_tautan_putus_riwayat(monkeypatch):
    _rencana_stub(monkeypatch)
    k = _K(_m(arah="keluar"))
    body = {"jenis": "uang_keluar", "akun_id": str(LAWAN)}
    body["versi"] = _prev(_K(_m(arah="keluar")), "catat", body)["versi"]
    r = _tulis(k, "catat", body)
    u = k.urut
    assert u.index("kunci:MUTASI_BANK") < u.index("baca_mutasi") < u.index("kunci:BANK_TX_MANUAL") < u.index("rencana_tx_manual") < u.index("tulis_tx_manual") < u.index("tautan") < u.index("putus") < u.index("riwayat")
    t_sql, t_arg = next((s, a) for s, a in k.sql if s.startswith("INSERT INTO mutasi_bank_tautan"))
    assert t_arg[1:4] == (MID, BT, D("500000.00"))                     # tautan ke bt HASIL penulis, sebesar nominal baris
    p_sql, p_arg = next((s, a) for s, a in k.sql if s.startswith("UPDATE mutasi_bank SET status = $4"))
    assert p_arg[3:5] == ("dicatat", "catat") and r["id"] == str(MID) and "MT-AB12CD34" in r["kalimat"]


def test_tulis_pribadi_cara_pribadi_dan_akun_tersimpan(monkeypatch):
    _rencana_stub(monkeypatch)
    k = _K(_m(arah="keluar"), akun=_akun_row("3-40000", "Prive"))
    _tulis(k, "catat", {"jenis": "pribadi"})
    _, p_arg = next((s, a) for s, a in k.sql if s.startswith("UPDATE mutasi_bank SET status = $4"))
    assert p_arg[3:5] == ("dicatat", "pribadi")


def test_tulis_catat_versi_basi_409_nol_tulisan(monkeypatch):
    p = _rencana_stub(monkeypatch)
    k = _K(_m(arah="keluar"))
    e = _galat(k, "catat", {"jenis": "uang_keluar", "akun_id": str(LAWAN), "versi": "basi"})
    assert e.status_code == 409 and _kodeg(e) == "RENCANA_BERUBAH" and p["tulis"] == [] and "tautan" not in k.urut


def test_tulis_catat_blok_apa_pun_nol_tulisan(monkeypatch):
    p = _rencana_stub(monkeypatch)
    k = _K(_m(arah="masuk"))
    e = _galat(k, "catat", {"jenis": "uang_keluar", "akun_id": str(LAWAN)})
    assert _kodeg(e) == "JENIS_TAK_SESUAI_ARAH" and p["tulis"] == [] and "putus" not in k.urut


def test_tulis_catat_kalah_balapan_status_409(monkeypatch):
    _rencana_stub(monkeypatch)
    k = _K(_m(arah="keluar"), putus=False)
    e = _galat(k, "catat", {"jenis": "uang_keluar", "akun_id": str(LAWAN)})
    assert _kodeg(e) == "SUDAH_DIPUTUS"


# ====================================================================== BATALKAN-CATATAN
def test_batalkan_hanya_untuk_catat_atau_pribadi(monkeypatch):
    _rencana_stub(monkeypatch)
    assert _kode(_prev(_K(_m(status="dicatat", cara="cocok")), "batalkan", {"alasan": "salah catat"})) == ["PAKAI_LEPASKAN"]
    assert _kode(_prev(_K(_m(status="dikecualikan", cara="kecualikan")), "batalkan", {"alasan": "salah catat"})) == ["PAKAI_LEPASKAN"]
    assert _kode(_prev(_K(_m()), "batalkan", {"alasan": "salah catat"})) == ["BELUM_DIPUTUS"]


def test_batalkan_alasan_wajib_tiga_huruf(monkeypatch):
    _rencana_stub(monkeypatch)
    assert _kode(_prev(_K(_m(status="dicatat", cara="catat"), tautan=[_tt()]), "batalkan", {"alasan": "ab"})) == ["ALASAN_WAJIB"]


def test_batalkan_tanpa_dokumen_tertaut_diblok(monkeypatch):
    _rencana_stub(monkeypatch)
    assert _kode(_prev(_K(_m(status="dicatat", cara="catat"), tautan=[]), "batalkan", {"alasan": "salah catat"})) == ["CATATAN_TANPA_DOKUMEN"]


def test_pratinjau_batalkan_menampilkan_tiga_hal_terpisah_dan_menyaring_blok_tautan_sendiri(monkeypatch):
    p = _rencana_stub(monkeypatch, blocks=[{"code": "BANK_TX_BERTAUT", "message": "Lepaskan dulu", "status": 409}])
    h = _prev(_K(_m(status="dicatat", cara="catat"), tautan=[_tt()]), "batalkan", {"alasan": "salah catat"})
    assert h["blocks"] == []                                   # tautan baris INI dilepas lebih dulu saat tulis -> bukan penghalang
    assert p["void_rencana"][-1][2] is True and p["void_rencana"][-1][3] is False        # pratinjau READ ONLY, tanpa kunci
    assert h["kalimat_tiga"] == ["Dokumen MT-AB12CD34 dibatalkan", "Jurnal pembalik RV-MT-AB12CD34 dibuat", "Baris kembali ke Untuk ditinjau"]


def test_pratinjau_batalkan_blok_lain_tetap_tampil(monkeypatch):
    _rencana_stub(monkeypatch, blocks=[{"code": "TEREKONSILIASI", "message": "sudah rekon", "status": 400}, {"code": "PERIODE_TERTUTUP", "message": "ditutup", "status": 400}])
    h = _prev(_K(_m(status="dicatat", cara="catat"), tautan=[_tt()]), "batalkan", {"alasan": "salah catat"})
    assert _kode(h) == ["TEREKONSILIASI", "PERIODE_TERTUTUP"]


def test_tulis_batalkan_urutan_lepas_tautan_dulu_lalu_void_lalu_baris_kembali(monkeypatch):
    p = _rencana_stub(monkeypatch)
    k = _K(_m(status="dicatat", cara="catat"), tautan=[_tt()])
    body = {"alasan": "salah catat"}
    body["versi"] = _prev(_K(_m(status="dicatat", cara="catat"), tautan=[_tt()]), "batalkan", body)["versi"]
    r = _tulis(k, "batalkan", body)
    u = k.urut
    assert u.index("kunci:MUTASI_BANK") < u.index("baca_mutasi") < u.index("lepas_tautan") < u.index("kunci:BANK_TX_VOID") < u.index("rencana_void_manual") < u.index("tulis_void_manual") < u.index("kembali") < u.index("riwayat")
    assert p["void_rencana"][-1][3] is True and p["void_tulis"][-1] == (BT, "salah catat")
    assert r["id"] == str(MID) and "RV-MT-AB12CD34" in r["kalimat"]


def test_tulis_batalkan_blok_void_nol_tulisan_pada_baris(monkeypatch):
    _rencana_stub(monkeypatch, blocks=[{"code": "PERIODE_TERTUTUP", "message": "ditutup", "status": 400}])
    k = _K(_m(status="dicatat", cara="catat"), tautan=[_tt()])
    e = _galat(k, "batalkan", {"alasan": "salah catat"})
    assert _kodeg(e) == "PERIODE_TERTUTUP" and "kembali" not in k.urut and "tulis_void_manual" not in k.urut


# ====================================================================== kebersihan, rute, izin, inventaris
def test_aksi_baru_terdaftar_dan_tak_menulis_jurnal_sendiri():
    assert {"catat", "batalkan"} <= set(BA.RENCANA) and {"catat", "batalkan"} <= set(BA.TULIS)
    assert BA.EVENT["catat"] == "BANK_FEED_CATAT" and BA.EVENT["batalkan"] == "BANK_FEED_BATALKAN"
    src = inspect.getsource(BA)
    assert "INSERT INTO journal" not in src and "INSERT INTO bank_transactions" not in src and "float(" not in src and "matched_statement_line_id" not in re.sub(r'""".*?"""', "", src, flags=re.S)
    for nama, nilai in vars(BA).items():
        if nama.startswith("SQL_"):
            assert "tenant_id" in nilai, nama


def test_rute_catat_dan_batalkan_catatan_terdaftar_bersekema():
    from app.routers import mutasi_bank as MR
    r = {(x.path, tuple(sorted(x.methods))): x for x in MR.router.routes}
    for aksi in ("catat", "batalkan-catatan"):
        for akhir in ("", "/preview"):
            k = (f"/{{bank_account_id}}/mutasi-bank/{{mutasi_id}}/{aksi}{akhir}", ("POST",))
            assert k in r and r[k].response_model is not None, k


def test_izin_catat_c_dan_batalkan_catatan_v_serta_inventaris():
    from app.middleware.permission_middleware import izin_untuk_jalur
    rek, mid = uuid.uuid4(), uuid.uuid4()
    assert izin_untuk_jalur(f"/api/bank-accounts/{rek}/mutasi-bank/{mid}/catat", "POST") == ("kas_bank", "C")
    assert izin_untuk_jalur(f"/api/bank-accounts/{rek}/mutasi-bank/{mid}/catat/preview", "POST") == ("kas_bank", "C")
    assert izin_untuk_jalur(f"/api/bank-accounts/{rek}/mutasi-bank/{mid}/batalkan-catatan", "POST") == ("kas_bank", "V")
    assert izin_untuk_jalur(f"/api/bank-accounts/{rek}/mutasi-bank/{mid}/batalkan-catatan/preview", "POST") == ("kas_bank", "V")
    from app.routers import mutasi_bank as MR
    inv = open(inspect.getsourcefile(MR).replace("app/routers/mutasi_bank.py", "tests/unit/inventaris_rute_tulis.txt")).read()
    for aksi in ("catat", "batalkan-catatan"):
        for akhir in ("", "/preview"):
            assert re.search(rf"^POST /api/bank-accounts/\{{bank_account_id\}}/mutasi-bank/\{{mutasi_id\}}/{aksi}{re.escape(akhir)}$", inv, re.M), (aksi, akhir)
