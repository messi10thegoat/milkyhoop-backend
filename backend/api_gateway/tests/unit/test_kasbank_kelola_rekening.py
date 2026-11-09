"""Kas & Bank kelola rekening standar SO + CW (10 Nov 2026; kontrak KONTRAK-kasbank-kelola-rekening-cw-20261110.md): penentu BERSAMA
pratinjau dan tulis (services/kasbank_rekening) untuk buat / ubah / hapus; pratinjau READ ONLY; putusan A (saldo awal terbukukan ->
400 berkalimat, dulu 500 + rollback). Paritas nyata = scripts/ops/uji-salinan-kasbank-rekening.sh."""
import asyncio
import inspect
import pathlib
import uuid
from decimal import Decimal as D

import pytest
from fastapi import HTTPException

from app.routers import bank_accounts as R
from app.schemas.bank_accounts import CreateBankAccountRequest, UpdateBankAccountRequest
from app.services import kasbank_daftar as KD
from app.services import kasbank_rekening as KR2

APP = pathlib.Path(inspect.getfile(KR2)).parents[1]
T = "kaos-biru-konveksi"
BA, COA, EQ, INDUK = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def jalan(c):
    return asyncio.run(c)


class Kon:
    """Jawaban per potongan SQL; tak ada tulisan (pratinjau READ ONLY dibuktikan di salinan)."""

    def __init__(self, nama_dipakai=False, kode_akhir="1-10207", induk=True, coa=None, coa_tertaut=False, ba=None, mutasi=0,
                 terbukukan=False, periode=None, sesi=0):
        self.k = dict(nama_dipakai=nama_dipakai, kode_akhir=kode_akhir, induk=induk, coa=coa, coa_tertaut=coa_tertaut, ba=ba, mutasi=mutasi,
                      terbukukan=terbukukan, periode=periode, sesi=sesi)
        self.sql = []

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        k = self.k
        if "account_code FROM chart_of_accounts" in sql and "LIKE" in sql:
            return k["kode_akhir"]
        if "FROM bank_accounts WHERE tenant_id = $1 AND account_name = $2" in sql:
            return uuid.uuid4() if k["nama_dipakai"] else None
        if "FROM bank_accounts WHERE tenant_id = $1 AND coa_id = $2" in sql:
            return uuid.uuid4() if k["coa_tertaut"] else None
        if "COUNT(*) FROM bank_transactions" in sql:
            return k["mutasi"]
        if "EXISTS (SELECT 1 FROM journal_lines" in sql:
            return k["terbukukan"]
        if "reconciliation_sessions" in sql:
            return k["sesi"]
        if "SELECT account_type FROM chart_of_accounts" in sql:
            return (k["coa"] or {}).get("account_type", "ASSET")
        return None

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        k = self.k
        if "FROM chart_of_accounts WHERE account_code = $1" in sql:
            return {"id": INDUK, "account_code": a[0], "name": "Bank" if a[0] == "1-10200" else "Hutang Kartu Kredit"} if k["induk"] else None
        if "FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2" in sql and "account_code, name, account_type" in sql:
            return k["coa"]
        if "FROM chart_of_accounts WHERE id = $1" in sql:
            return {"account_code": "1-10203", "name": "BCA"}
        if "FROM bank_accounts ba WHERE ba.id = $1" in sql:
            return k["ba"]
        if "fiscal_periods" in sql:
            return k["periode"]
        return None

    async def fetch(self, sql, *a):
        return []


@pytest.fixture(autouse=True)
def palsu(monkeypatch):
    async def ekuitas(conn, tid, kode):
        return EQ

    async def tanggal(conn, tid):
        import datetime as dt
        return dt.date(2026, 11, 10)

    async def uang_muka(conn, tid, cid):
        return None
    monkeypatch.setattr(KR2, "resolve_account_id", ekuitas)
    monkeypatch.setattr(KR2, "tanggal_dokumen", tanggal)
    monkeypatch.setattr(KR2, "tolak_rekening_bank", uang_muka)


def buat(**kw):
    return CreateBankAccountRequest(**{"account_name": "BCA Operasional", "account_type": "bank", "opening_balance": 1500000, **kw})


# ---------------------------------------------------------------- buat
def test_buat_bank_coa_otomatis_direncanakan_dan_jurnal_saldo_awal():
    r = jalan(KR2.rencana_buat_rekening(Kon(), T, buat(), True))
    assert r["blocks"] == [] and r["coa"]["baru"] and (r["coa"]["account_code"], r["coa"]["account_type"]) == ("1-10208", "ASSET")
    assert r["baris"] == [("COA_BARU", D(1500000), D(0), "Saldo Awal - BCA Operasional"), (EQ, D(0), D(1500000), "Modal Saldo Awal - BCA Operasional")]
    assert r["saldo"][0]["sesudah"] == D(1500000) and "1-10208 'BCA Operasional'" in r["catatan"] and "1-10200 Bank" in r["catatan"]
    out = jalan(KR2.keluaran(Kon(), T, r, {}))
    assert out["ok"] and out["jurnal"][0]["account_name"] == "1-10208 BCA Operasional (baru)" and out["jurnal"][0]["debit"] == "1500000.00"


def test_buat_kartu_kredit_kebalikan_dan_tanpa_saldo_awal_tanpa_jurnal():
    r = jalan(KR2.rencana_buat_rekening(Kon(kode_akhir=None), T, buat(account_type="credit_card"), True))
    assert r["coa"]["account_code"] == "2-10601" and r["coa"]["normal_balance"] == "CREDIT"
    assert r["baris"][0][0] == EQ and r["baris"][1] == ("COA_BARU", D(0), D(1500000), "Saldo Awal Hutang - BCA Operasional")
    k = jalan(KR2.rencana_buat_rekening(Kon(), T, buat(account_type="cash", opening_balance=0), True))
    assert k["baris"] == [] and k["blocks"] == []


@pytest.mark.parametrize("kon,body,kode", [
    (dict(nama_dipakai=True), {}, "NAMA_REKENING_DIPAKAI"),
    (dict(induk=False), {}, "COA_INDUK_TIDAK_ADA"),
    (dict(kode_akhir="1-10299"), {}, "COA_KODE_PENUH"),
    (dict(coa=None), {"coa_id": str(uuid.uuid4())}, "COA_TIDAK_DITEMUKAN"),
    (dict(coa={"id": COA, "account_code": "2-1", "name": "Utang", "account_type": "LIABILITY"}), {"coa_id": str(COA)}, "COA_TIPE_SALAH"),
    (dict(coa={"id": COA, "account_code": "1-1", "name": "Kas", "account_type": "ASSET"}, coa_tertaut=True), {"coa_id": str(COA)}, "COA_SUDAH_TERTAUT"),
    (dict(periode={"id": 1, "period_name": "Okt 2026", "status": "CLOSED"}), {}, "PERIODE_TERTUTUP"),
])
def test_buat_blok_pratinjau_dikumpulkan_tulis_dilempar(kon, body, kode):
    r = jalan(KR2.rencana_buat_rekening(Kon(**kon), T, buat(**body), True))
    assert kode in [b["code"] for b in r["blocks"]]
    with pytest.raises(HTTPException):
        jalan(KR2.rencana_buat_rekening(Kon(**kon), T, buat(**body), False))


# ---------------------------------------------------------------- ubah
BA_ROW = {"id": BA, "account_name": "BCA", "account_type": "bank", "coa_id": COA, "is_active": True, "is_default": True, "saldo": D("250000"),
          "account_number": "123", "bank_name": "BCA", "bank_branch": None, "bank_address": None, "swift_code": None,
          "account_holder_name": None, "notes": None}


def test_ubah_catatan_perubahan_dan_peringatan_bukan_blok():
    body = UpdateBankAccountRequest(account_name="BCA Utama", bank_branch="", is_active=False)
    r = jalan(KR2.rencana_ubah_rekening(Kon(ba=BA_ROW), T, BA, body, True))
    assert r["blocks"] == [] and [p["medan"] for p in r["perubahan"]] == ["account_name", "is_active"]  # "" -> None == None: tak berubah
    assert len(r["peringatan"]) == 2 and r["peringatan"][0].startswith("Ini rekening utama") and "Rp 250.000" in r["peringatan"][1]
    tetap = jalan(KR2.rencana_ubah_rekening(Kon(ba=BA_ROW), T, BA, UpdateBankAccountRequest(account_name="BCA"), True))
    assert tetap["perubahan"] == [] and tetap["catatan"] == "Tidak ada perubahan."


@pytest.mark.parametrize("kon,body,kode", [
    (dict(ba=None), {}, "REKENING_TIDAK_DITEMUKAN"),
    (dict(ba=BA_ROW), {"account_name": " "}, "NAMA_REKENING_KOSONG"),
    (dict(ba=BA_ROW, nama_dipakai=True), {"account_name": "BRI"}, "NAMA_REKENING_DIPAKAI"),
    (dict(ba=BA_ROW), {"account_type": "credit_card"}, "TIPE_KARTU_KREDIT"),
    (dict(ba=BA_ROW, coa={"account_type": "LIABILITY"}), {"account_type": "cash"}, "TIPE_COA_TIDAK_KONSISTEN"),
])
def test_ubah_blok_sama_dengan_penulis_lama(kon, body, kode):
    r = jalan(KR2.rencana_ubah_rekening(Kon(**kon), T, BA, UpdateBankAccountRequest(**body), True))
    assert [b["code"] for b in r["blocks"]][:1] == [kode]


# ---------------------------------------------------------------- hapus (putusan A)
def test_hapus_akibat_permanen_nonaktifkan_dan_saldo_awal_terbukukan():
    p = jalan(KR2.rencana_hapus_rekening(Kon(ba=BA_ROW, sesi=2), T, BA, True))
    assert p["akibat"] == "permanen" and "akun perkiraan 1-10203 BCA, dan 2 sesi rekonsiliasi DIHAPUS PERMANEN" in p["catatan"]
    n = jalan(KR2.rencana_hapus_rekening(Kon(ba=BA_ROW, mutasi=4), T, BA, True))
    assert n["akibat"] == "nonaktifkan" and "4 mutasi" in n["catatan"]
    s = jalan(KR2.rencana_hapus_rekening(Kon(ba=BA_ROW, terbukukan=True), T, BA, True))
    assert [b["code"] for b in s["blocks"]] == ["REKENING_BERSALDO_AWAL"] and s["akibat"] is None
    with pytest.raises(HTTPException) as e:
        jalan(KR2.rencana_hapus_rekening(Kon(ba=BA_ROW, terbukukan=True), T, BA, False))
    assert e.value.status_code == 400 and "saldo awal yang sudah dibukukan" in str(e.value.detail)


def test_aksi_tersedia_hapus_satu_penentu_dengan_delete():
    a = jalan(KD.aksi_tersedia_rekening(Kon(terbukukan=True), T, {"id": BA, "is_active": True, "coa_id": COA}))
    assert a["hapus"] == {"tersedia": False, "alasan": "Rekening punya saldo awal yang sudah dibukukan, jadi tidak bisa dihapus permanen. Nonaktifkan saja."}
    assert jalan(KD.aksi_tersedia_rekening(Kon(), T, {"id": BA, "is_active": True, "coa_id": COA}))["hapus"]["tersedia"] is True
    q = " ".join(inspect.getsource(KD.coa_terbukukan).split())
    assert "je.tenant_id = $2" in q and "je.source_type = 'OPENING' AND je.source_id = $3 AND je.status <> 'POSTED'" in q


# ---------------------------------------------------------------- rute
def test_rute_penulis_memanggil_rencana_bersama_dan_pratinjau_read_only():
    src = (APP / "routers/bank_accounts.py").read_text()
    assert "auto_create_coa_for_bank_account" not in src and "get_next_coa_code" not in src  # digantikan rencana -> DIHAPUS
    buat_src = inspect.getsource(R.create_bank_account)
    assert "KR2.rencana_buat_rekening(conn, ctx[\"tenant_id\"], body, pratinjau=False)" in buat_src and "KR2.tulis_coa(" in buat_src
    assert 'idem_buat.mulai(conn, ctx, idem_buat.kunci_dari(request), "BANK_CREATE"' in buat_src and "str(e)" not in buat_src.split("except Exception")[1]
    assert "KR2.rencana_ubah_rekening(conn" in inspect.getsource(R.update_bank_account)
    assert "KR2.rencana_hapus_rekening(conn" in inspect.getsource(R.delete_bank_account)
    assert 'router.patch("/{bank_account_id}/preview")' in src and 'router.post("/{bank_account_id}/delete/preview")' in src
    assert 'router.post("/preview")' in src and "exclude_unset=True" in inspect.getsource(R.preview_update_bank_account)
    assert "conn.transaction(readonly=True)" in inspect.getsource(KR2.pratinjau)
