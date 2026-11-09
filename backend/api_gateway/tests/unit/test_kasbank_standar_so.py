"""Kas&Bank standar SO gelombang 1 (kontrak backend/docs/KONTRAK-kasbank-standar-so-20261109.md, MASTER 9 Nov 2026).

Penentu bersama void (pratinjau == tulis), teks posisi / aksi / dokumen per baris, bentuk rute baru, izin. Paritas angka daftar vs saldo
jurnal dan pratinjau vs tulis diuji di SALINAN (scripts/ops/uji-salinan-kasbank.sh). K3: batal tidak dikunci saldo -> minus = isyarat."""
import ast
import asyncio
import datetime as dt
import os
import uuid
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

from app.routers import bank_accounts as BA  # noqa: E402
from app.routers import bank_transactions as BTX  # noqa: E402
from app.routers import bank_transfers as BT  # noqa: E402
from app.routers import kasbank_v2 as V2  # noqa: E402
from app.schemas.bank_transfers import VoidBankTransferRequest  # noqa: E402
from app.schemas.kasbank_v2 import VoidTransactionRequest  # noqa: E402
from app.services import kasbank_daftar as KD  # noqa: E402
from app.services import kasbank_tulis as KT  # noqa: E402
from app.services.bayar_keluar_rencana import Blok  # noqa: E402
from app.services.galat_berkode import GalatBerkode  # noqa: E402

T = "kaos-biru-konveksi"
TX, JE, BA_ID, COA_BANK, COA_LAWAN, TRF, DARI, KE, COA_KE = (uuid.uuid4() for _ in range(9))
HARI = dt.date(2026, 11, 9)
APP = Path(V2.__file__).resolve().parents[1]


# ---------------------------------------------------------------- koneksi tiruan (kolom = kolom DB nyata)
class Kon:
    def __init__(self, tx=None, je=None, periode=None, saldo=D("1000"), transfer=None, lines=None):
        self.tx = {"id": TX, "journal_id": JE, "bank_account_id": BA_ID, "bank_coa_id": COA_BANK, "asal_sah": True, "status": "POSTED",
                   "transaction_type": "deposit", "reconciliation_status": "UNRECONCILED", "transaction_date": dt.date(2026, 11, 5),
                   "amount": D("1000"), **(tx or {})}
        self.je = {"id": JE, "journal_number": "MT-ABCD1234", "source_type": "BANK_TRANSACTION", "source_id": TX, "reversed_by_id": None,
                   **(je or {})}
        self.periode, self.saldo = periode, saldo
        self.transfer = transfer
        self.lines = lines if lines is not None else [
            {"account_id": COA_BANK, "debit": D("1000"), "credit": D("0"), "memo": "masuk"},
            {"account_id": COA_LAWAN, "debit": D("0"), "credit": D("1000"), "memo": "modal"}]
        self.exe, self.sql = [], []

    def transaction(self, **kw):
        kon = self

        class Tx:
            async def __aenter__(self):
                kon.sql.append(("TX", kw))
                return kon

            async def __aexit__(self, *a):
                return False
        return Tx()

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        if "asal_sah" in sql:
            return self.tx
        if "FROM journal_entries WHERE id = $1 AND tenant_id = $2" in sql:
            return self.je
        if "fiscal_periods" in sql:
            return self.periode
        if "FROM bank_accounts ba WHERE ba.id = $1 AND ba.tenant_id = $2" in sql:
            return {"id": BA_ID, "account_name": "Kas Toko", "account_type": "cash", "coa_id": COA_BANK, "is_active": True, "saldo": self.saldo}
        if "FROM bank_transfers bt" in sql:
            return self.transfer
        if sql.startswith("SELECT * FROM bank_transactions"):
            return {**self.tx, "transaction_number": None, "running_balance": 0, "description": "x", "origin_type": "MANUAL",
                    "status": "VOIDED", "created_at": None}
        return None

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM journal_lines" in sql:
            return self.lines
        return []

    async def fetchval(self, sql, *a):
        self.sql.append((sql, a))
        return 0

    async def execute(self, sql, *a):
        self.exe.append((" ".join(sql.split()), a))
        return "OK"


class Pool:
    def __init__(self, kon):
        self.kon = kon

    def acquire(self):
        kon = self.kon

        class Cm:
            async def __aenter__(self):
                return kon

            async def __aexit__(self, *a):
                return False
        return Cm()


@pytest.fixture(autouse=True)
def _hari(monkeypatch):
    async def hari(*a):
        return HARI
    for m in (KT, V2, BT, BA, BTX):
        monkeypatch.setattr(m, "tanggal_dokumen", hari, raising=False)


def req(headers=None):
    return SimpleNamespace(headers=headers or {}, state=SimpleNamespace(user={"tenant_id": T, "user_id": str(uuid.uuid4())}))


def jalan(aw):
    return asyncio.run(aw)


TRANSFER = {"id": TRF, "status": "posted", "transfer_number": "TRF-2611-0003", "transfer_date": dt.date(2026, 11, 7), "journal_id": JE,
            "total_amount": D("7500"), "amount": D("5000"), "fee_amount": D("2500"), "from_bank_id": DARI, "to_bank_id": KE,
            "from_name": "Kas Toko", "from_coa_id": COA_BANK, "from_type": "cash", "from_balance": D("100"),
            "to_name": "BCA", "to_coa_id": COA_KE, "to_balance": D("3000")}


# ---------------------------------------------------------------- penentu void mutasi manual
def test_void_manual_pembalik_dan_saldo_isyarat_minus_bukan_blok():
    k = Kon(saldo=D("400"))  # uang masuk 1.000 sudah terpakai -> batal membuat kas -600: isyarat K3, BUKAN blok
    r = jalan(KT.rencana_void_manual(k, T, TX, "salah catat", Blok(True)))
    assert r["blocks"] == []
    assert r["baris"] == [(COA_BANK, D("0"), D("1000"), "Reversal - masuk"), (COA_LAWAN, D("1000"), D("0"), "Reversal - modal")]
    assert r["saldo"] == [{"bank_account_id": str(BA_ID), "account_name": "Kas Toko", "sebelum": D("400"), "sesudah": D("-600")}]
    assert r["jumlah"] == D("1000")
    assert not any("FOR UPDATE" in s for s, _ in k.sql if isinstance(s, str))  # pratinjau READ ONLY: tanpa kunci baris


@pytest.mark.parametrize("ubah,kode,status", [
    ({"tx": {"asal_sah": False}}, "BUKAN_MUTASI_MANUAL", 400),
    ({"tx": {"status": "VOIDED"}}, "MUTASI_SUDAH_DIBATALKAN", 400),
    ({"tx": {"transaction_type": "opening"}}, "SALDO_AWAL", 400),
    ({"tx": {"reconciliation_status": "RECONCILED"}}, "TEREKONSILIASI", 400),
    ({"periode": {"id": 1, "period_name": "Nov 2026", "status": "CLOSED"}}, "PERIODE_TERTUTUP", 403),
    ({"je": {"reversed_by_id": uuid.uuid4()}}, "JURNAL_SUDAH_DIBALIK", 409),
    ({"lines": []}, "JURNAL_TANPA_BARIS", 500),
])
def test_void_manual_blok_pratinjau_sama_dengan_galat_tulis(ubah, kode, status):
    r = jalan(KT.rencana_void_manual(Kon(**ubah), T, TX, "x", Blok(True)))
    assert r["blocks"][0]["code"] == kode and r["blocks"][0]["status"] == status
    with pytest.raises(GalatBerkode) as e:  # tulis: blok PERTAMA dilempar berkode, status sama
        jalan(KT.rencana_void_manual(Kon(**ubah), T, TX, "x", Blok(False), kunci=True))
    assert e.value.code == kode and e.value.status_code == status


def test_void_manual_tidak_ditemukan():
    k = Kon()
    k.tx = None
    r = jalan(KT.rencana_void_manual(k, T, TX, "x", Blok(True)))
    assert [b["code"] for b in r["blocks"]] == ["MUTASI_TIDAK_DITEMUKAN"]


def test_asal_manual_satu_predikat_daftar_dan_void():
    """Daftar /api/bank-transactions dan void memakai SATU string KT.ASAL_MANUAL (dulu `asal_sah` Python di kasbank_v2)."""
    assert KT.ASAL_MANUAL in KD.SQL_MANUAL and KT.ASAL_MANUAL in KT.SQL_MUTASI_VOID
    for h in ("bt.origin_type = 'MANUAL'", "bt.reference_type IS NULL", "bt.reference_id IS NULL", "ja.source_type = 'BANK_TRANSACTION'",
              "ja.source_id = bt.id", "ja.tenant_id = bt.tenant_id"):
        assert h in KT.ASAL_MANUAL
    assert "asal_sah =" not in (APP / "routers/kasbank_v2.py").read_text()  # salinan Python lama dihapus


# ---------------------------------------------------------------- tulis void manual == pratinjau
def test_tulis_void_manual_memakai_baris_penentu(monkeypatch):
    k = Kon()

    async def pool():
        return Pool(k)
    monkeypatch.setattr(V2, "get_pool", pool)
    h = jalan(V2.void_transaction(req(), TX, VoidTransactionRequest(reason="salah catat")))
    assert h["success"] is True
    jl = [a for s, a in k.exe if s.startswith("INSERT INTO journal_lines")]
    rencana = jalan(KT.rencana_void_manual(Kon(), T, TX, "salah catat", Blok(True)))["baris"]
    assert [(x[3], x[4], x[5], x[6]) for x in jl] == rencana  # pratinjau == tulis, baris demi baris
    assert any("FOR UPDATE" in s for s, _ in k.sql if isinstance(s, str))  # tulis mengunci baris + jurnal


def test_tulis_void_manual_idempoten_replay(monkeypatch):
    k = Kon()

    async def pool():
        return Pool(k)

    async def lama(*a, **kw):
        return "kp", "sd", {"success": True, "replay": True}
    monkeypatch.setattr(V2, "get_pool", pool)
    monkeypatch.setattr(V2.idem_buat, "mulai_aksi", lama)
    h = jalan(V2.void_transaction(req({"X-Idempotency-Key": "k1"}), TX, VoidTransactionRequest(reason="ulang")))
    assert h == {"success": True, "replay": True}  # ulang = jawaban tersimpan
    assert [x for x, _ in k.exe if not x.startswith("SELECT pg_advisory_xact_lock")] == []  # nol tulisan (hanya kunci)


# ---------------------------------------------------------------- penentu void transfer
def test_void_transfer_posted_pembalik_saldo_bersaring_tenant():
    k = Kon(transfer=TRANSFER, lines=[{"account_id": COA_KE, "debit": D("5000"), "credit": D("0"), "memo": "masuk"},
                                      {"account_id": COA_BANK, "debit": D("0"), "credit": D("7500"), "memo": "keluar"}])
    r = jalan(KT.rencana_void_transfer(k, T, TRF, "x", Blok(True)))
    assert r["blocks"] == [] and not r["hapus_draf"]
    assert r["baris"] == [(COA_KE, D("0"), D("5000"), "Reversal - masuk"), (COA_BANK, D("7500"), D("0"), "Reversal - keluar")]
    assert [(s["sebelum"], s["sesudah"]) for s in r["saldo"]] == [(D("100"), D("7600")), (D("3000"), D("-2000"))]  # minus = isyarat
    assert "je.tenant_id = bt.tenant_id" in KT.SQL_TRANSFER_POSTING  # saldo bersaring tenant (Law 24; dulu tanpa)


def test_void_transfer_draf_dihapus_tanpa_jurnal():
    r = jalan(KT.rencana_void_transfer(Kon(transfer={**TRANSFER, "status": "draft"}), T, TRF, "x", Blok(True)))
    assert r["blocks"] == [] and r["hapus_draf"] and r["baris"] == [] and r["catatan"] == "Draf akan dihapus (belum berjurnal)."


def test_void_transfer_periode_tertutup_403_berkode():
    k = Kon(transfer=TRANSFER, periode={"id": 1, "period_name": "Nov 2026", "status": "LOCKED"})
    with pytest.raises(GalatBerkode) as e:
        jalan(KT.rencana_void_transfer(k, T, TRF, "x", Blok(False), kunci=True))
    assert (e.value.status_code, e.value.code) == (403, "PERIODE_TERTUTUP")  # dulu 400 polos; kini seperti semua jalur


def test_void_transfer_sudah_batal_dan_hilang():
    assert jalan(KT.rencana_void_transfer(Kon(transfer={**TRANSFER, "status": "void"}), T, TRF, "x", Blok(True)))["blocks"][0]["code"] \
        == "TRANSFER_SUDAH_DIBATALKAN"
    assert jalan(KT.rencana_void_transfer(Kon(transfer=None), T, TRF, "x", Blok(True)))["blocks"][0]["code"] == "TRANSFER_TIDAK_DITEMUKAN"


def test_pratinjau_void_transfer_read_only_bentuk_b8(monkeypatch):
    k = Kon(transfer=TRANSFER)

    async def pool():
        return Pool(k)
    monkeypatch.setattr(BT, "get_pool", pool)
    d = jalan(BT.preview_void_bank_transfer(req(), TRF, VoidBankTransferRequest(reason="salah rekening")))["data"]
    assert set(d) == {"ok", "blocks", "jurnal", "saldo", "payload"} and d["ok"] is True
    assert d["saldo"][1] == {"bank_account_id": str(KE), "account_name": "BCA", "sebelum": "3000.00", "sesudah": "-2000.00", "minus": True}
    assert ("TX", {"readonly": True}) in k.sql and k.exe == []


# ---------------------------------------------------------------- teks posisi / aksi / dokumen per baris
def test_teks_posisi():
    assert KD.position_text_manual(D("1000"), "Kas Toko", False) == "Masuk ke Kas Toko"
    assert KD.position_text_manual(D("-5"), "BCA", False) == "Keluar dari BCA"
    assert KD.position_text_manual(D("-5"), "BCA", True, "salah catat") == "Dibatalkan · salah catat"
    assert KD.position_text_transfer("draft", "A", "B") == "Draf · belum diposting"
    assert KD.position_text_transfer("posted", "Kas", "BCA") == "Diposting · Kas → BCA"
    assert KD.position_text_transfer("void", "Kas", "BCA") == "Dibatalkan"
    assert KD.position_text_rekening(False, "bank", 5) == "Nonaktif"
    assert KD.position_text_rekening(True, "bank", -5) == "Saldo minus"
    assert KD.position_text_rekening(True, "credit_card", D("-250000")) == "Terutang Rp 250.000"
    assert KD.position_text_rekening(True, "credit_card", 0) == "Lunas"
    assert KD.position_text_rekening(True, "cash", 10, True) == "Aktif · utama"


@pytest.mark.parametrize("status,harap", [("draft", ["draf"]), ("posted", []), ("void", [])])
def test_aksi_transfer_python_setara_sql(status, harap):
    assert KD.aksi_semua_transfer(status) == harap
    assert ("bt.status = 'draft'" == KD.sql_aksi_transfer("draf", "$2")) and "bt.status <> 'void'" in KD.sql_aksi_transfer("bulan_ini", "$2")


@pytest.mark.parametrize("aktif,tipe,saldo,harap", [
    (True, "bank", -1, ["saldo_minus"]), (True, "cash", 0, []), (False, "bank", -1, []), (True, "credit_card", -9, [])])
def test_aksi_rekening_python_setara_sql(aktif, tipe, saldo, harap):
    assert KD.aksi_semua_rekening(aktif, tipe, saldo) == harap
    s = KD.sql_aksi_rekening("saldo_minus")
    assert "ba.is_active" in s and "<> 'credit_card'" in s and "< 0" in s and "je.tenant_id = ba.tenant_id" in s


def test_dokumen_baris():
    assert KD.documents_baris_manual(False) == [{"type": "bank_transaction"}, {"type": "journal"}]
    assert len(KD.documents_baris_manual(True)) == 3
    assert KD.documents_baris_transfer("draft", None) == [{"type": "bank_transfer"}]
    assert [d["type"] for d in KD.documents_baris_transfer("void", JE)].count("bank_transaction") == 4


def test_hapus_rekening_satu_penentu():
    assert KD.alasan_hapus_rekening(True, 3) is None and KD.alasan_hapus_rekening(False, 0) is None
    assert KD.alasan_hapus_rekening(False, 3).startswith("Rekening ini sudah nonaktif")
    src = (APP / "routers/bank_accounts.py").read_text()
    assert "KD.alasan_hapus_rekening(" in src and "KD.jumlah_mutasi_nyata(" in src and "Bank account already inactive" not in src


# ---------------------------------------------------------------- aksi_tersedia (penentu tulis)
def test_aksi_tersedia_manual_dan_transfer():
    a = jalan(KD.aksi_tersedia_manual(Kon(), T, TX))
    assert list(a) == ["batalkan", "ubah"] and a["batalkan"] == {"tersedia": True, "alasan": None} and a["ubah"]["tersedia"] is False
    b = jalan(KD.aksi_tersedia_manual(Kon(tx={"status": "VOIDED"}), T, TX))
    assert b["batalkan"] == {"tersedia": False, "alasan": "Mutasi ini sudah dibatalkan."}
    t = jalan(KD.aksi_tersedia_transfer(Kon(transfer=TRANSFER), T, TRANSFER))
    assert list(t) == ["posting", "ubah", "hapus", "batalkan"] and t["batalkan"]["tersedia"] and not t["posting"]["tersedia"]
    draf = {**TRANSFER, "status": "draft", "fee_amount": D("0"), "total_amount": D("5000")}
    d = jalan(KD.aksi_tersedia_transfer(Kon(transfer=draf), T, draf))
    assert d["posting"]["tersedia"] is False and d["ubah"]["tersedia"] is True  # kas 100 < 5.000 -> blok KAS_MINUS penentu posting
    assert d["posting"]["alasan"].startswith("Saldo Kas Toko") and d["batalkan"]["alasan"] == "Draf dihapus, bukan dibatalkan."


def test_aksi_tersedia_rekening():
    a = jalan(KD.aksi_tersedia_rekening(Kon(), T, {"id": BA_ID, "is_active": False}))
    assert list(a) == ["uang_masuk", "uang_keluar", "transfer", "hitung_fisik", "ubah", "nonaktifkan", "aktifkan", "hapus"]
    assert a["uang_keluar"] == {"tersedia": False, "alasan": "Rekening ini nonaktif."} and a["aktifkan"]["tersedia"] is True


# ---------------------------------------------------------------- rute /api/bank-transactions
class KonDaftar(Kon):
    async def fetchval(self, sql, *a):
        self.sql.append((sql, a))
        return 1

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        if "masuk_bulan_ini_count" in sql:
            return {"masuk_bulan_ini_count": 2, "masuk_bulan_ini_value": D("1500"), "keluar_bulan_ini_count": 1,
                    "keluar_bulan_ini_value": D("200"), "total_count": 3}
        if "AS masuk" in sql:
            return {"masuk": D("1500"), "keluar": D("200")}
        if "AND bt.id = $2" in sql:
            return BARIS
        return await Kon.fetchrow(self, sql, *a)

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        return [BARIS] if "FROM (" in sql else await Kon.fetch(self, sql, *a)


BARIS = {"id": TX, "nomor": "MT-ABCD1234", "tgl": dt.date(2026, 11, 5), "created_at": None, "description": "setor modal", "pihak": "Pak Budi",
         "journal_id": JE, "journal_number": "MT-ABCD1234", "je_at": None, "je_by": None, "reversed_by_id": None, "voided_at": None,
         "voided_by": None, "void_reason": None, "ba_id": BA_ID, "account_name": "Kas Toko", "account_type": "cash", "net": D("1000"),
         "lawan_id": COA_LAWAN, "lawan_kode": "3-10100", "lawan_nama": "Modal Pemilik", "batal": False}


def _pasang(monkeypatch, k):
    async def pool():
        return Pool(k)
    monkeypatch.setattr(BTX, "get_pool", pool)


def test_daftar_manual_bawaan_tercatat_dan_bentuk(monkeypatch):
    k = KonDaftar()
    _pasang(monkeypatch, k)
    h = jalan(BTX.list_bank_transactions(req(), arah=None, bank_account_id=None, status="tercatat", date_from=None, date_to=None, search=None,
                                         aksi=None, with_totals=True, skip=0, limit=50, sort_by="tanggal", sort_order="desc"))
    sql_hitung = next(s for s, _ in k.sql if isinstance(s, str) and s.startswith("SELECT COUNT(*)"))
    assert "NOT d.batal" in sql_hitung and KT.ASAL_MANUAL in sql_hitung
    assert (h["sum_masuk"], h["sum_keluar"], h["sum_neto"]) == ("1500.00", "200.00", "1300.00")
    x = h["items"][0]
    assert set(x) == {"id", "nomor", "transaction_date", "arah", "amount", "bank_account", "akun_lawan", "pihak", "description", "status",
                      "position_text", "aksi", "aksi_semua", "documents", "doc_count", "journal_id", "journal_number"}
    assert (x["arah"], x["amount"], x["position_text"], x["aksi"], x["aksi_semua"]) == ("masuk", "1000.00", "Masuk ke Kas Toko", None, [])
    assert x["akun_lawan"] == {"id": str(COA_LAWAN), "kode": "3-10100", "nama": "Modal Pemilik"}


def test_daftar_manual_aksi_kartu_memakai_tanggal_bisnis(monkeypatch):
    k = KonDaftar()
    _pasang(monkeypatch, k)
    jalan(BTX.list_bank_transactions(req(), arah="keluar", bank_account_id=BA_ID, status="semua", date_from=None, date_to=None, search="budi",
                                     aksi="keluar_bulan_ini", with_totals=False, skip=0, limit=50, sort_by="nominal", sort_order="asc"))
    s, a = next((s, a) for s, a in k.sql if isinstance(s, str) and s.startswith("SELECT COUNT(*)"))
    where = s.split(") d WHERE ")[1]
    assert where.startswith("d.net < 0 AND d.ba_id = $2")  # status=semua: tanpa saring batal
    assert KD.sql_aksi_manual("keluar_bulan_ini", "$3") in where and "d.lawan_nama ILIKE $4" in where
    assert a == (T, BA_ID, HARI, "%budi%")


def test_ringkasan_manual_kartu_dan_neto(monkeypatch):
    _pasang(monkeypatch, KonDaftar())
    d = jalan(BTX.bank_transactions_summary(req(), search=None))["data"]
    assert d == {"masuk_bulan_ini_count": 2, "keluar_bulan_ini_count": 1, "masuk_bulan_ini_value": "1500.00", "keluar_bulan_ini_value": "200.00",
                 "neto_bulan_ini_value": "1300.00", "total_count": 3}


def test_detail_manual_aksi_tersedia_di_data_dan_404(monkeypatch):
    _pasang(monkeypatch, KonDaftar())
    d = jalan(BTX.get_bank_transaction(req(), TX))["data"]
    assert d["id"] == str(TX) and list(d["aksi_tersedia"]) == ["batalkan", "ubah"]
    k = Kon()
    _pasang(monkeypatch, k)
    with pytest.raises(HTTPException) as e:
        jalan(BTX.get_bank_transaction(req(), TX))
    assert (e.value.status_code, e.value.detail) == (404, "Mutasi manual tidak ditemukan.")


def test_rute_ringkasan_di_atas_id():
    jalur = [r.path for r in BTX.router.routes]
    assert jalur.index("/summary") < jalur.index("/{transaction_id}")
    assert not any(p.endswith("/void") for p in jalur)  # void tetap fungsi kasbank_v2 (main.py), tidak ganda


# ---------------------------------------------------------------- izin
@pytest.mark.parametrize("jalur,metode,harap", [
    (f"/api/bank-transactions", "GET", ("kas_bank", "R")),
    (f"/api/bank-transactions/{TX}/history", "GET", ("kas_bank", "R")),
    (f"/api/bank-transactions/{TX}/void/preview", "POST", ("kas_bank", "V")),
    (f"/api/bank-transactions/{TX}/void", "POST", ("kas_bank", "V")),
    (f"/api/bank-transfers/{TRF}/void/preview", "POST", ("kas_bank", "V")),
    (f"/api/bank-transfers/{TRF}/documents", "GET", ("kas_bank", "R")),
    (f"/api/bank-accounts/{BA_ID}/history", "GET", ("kas_bank", "R")),
])
def test_izin_rute_baru(jalur, metode, harap):
    from app.middleware.permission_middleware import PermissionMiddleware
    assert PermissionMiddleware(app=None)._find_permission(jalur, metode) == harap


def test_main_memasang_router_mutasi_manual():
    src = (APP / "main.py").read_text()
    assert 'include_router(_bank_transactions.router, prefix="/api/bank-transactions"' in src
    t = ast.parse(src)
    assert any(isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "add_api_route" for n in ast.walk(t))
