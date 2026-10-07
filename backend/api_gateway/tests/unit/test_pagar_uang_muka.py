"""Pagar jurnal MANUAL untuk akun peran Uang Muka Pelanggan (CUSTOMER_DEPOSIT_LIABILITY) — 5 Okt 2026, MASTER."""
import asyncio
import inspect
import pathlib
import uuid

import pytest
from fastapi import HTTPException

from app.services import pagar_uang_muka as PU
from app.routers import journals as J
from app.routers import bank_accounts as BA

T = "tenant-uji"
DP = uuid.uuid4()
LAIN = uuid.uuid4()


class Conn:
    """account_roles tiruan: hanya DP yang dipetakan ke peran; mencatat kueri."""

    def __init__(self):
        self.q = []

    async def fetch(self, sql, *a):
        self.q.append((sql, a))
        if "account_roles" in sql:
            assert a[0] == T and a[1] == "CUSTOMER_DEPOSIT_LIABILITY"
            if "coa.account_code = ANY" in sql:  # varian kode (modul Saldo Awal)
                return [{"account_code": "2-10500", "name": "Uang Muka Pelanggan"}] if "2-10500" in a[2] else []
            return [{"account_code": "2-10500", "name": "Uang Muka Pelanggan"}] if DP in a[2] else []
        return []  # cek AR/AP & persediaan: tak ada


def _j(coro):
    return asyncio.run(coro)


def test_jurnal_manual_ke_uang_muka_ditolak_pesan_indonesia_dan_arah_modul():
    with pytest.raises(HTTPException) as e:
        _j(PU.tolak_jurnal_manual(Conn(), T, [LAIN, DP]))
    assert e.value.status_code == 400
    assert "2-10500 Uang Muka Pelanggan" in e.value.detail and "modul Uang Muka Pelanggan" in e.value.detail


def test_akun_lain_lolos_dan_kosong_tanpa_kueri():
    c = Conn()
    _j(PU.tolak_jurnal_manual(c, T, [LAIN]))
    _j(PU.tolak_jurnal_manual(c, T, []))
    assert len(c.q) == 1  # daftar kosong tak bertanya ke DB


def test_bank_akun_lawan_uang_muka_ditolak():
    with pytest.raises(HTTPException) as e:
        _j(PU.tolak_akun_lawan_bank(Conn(), T, DP))
    assert e.value.status_code == 400 and "Uang Masuk/Keluar" in e.value.detail
    _j(PU.tolak_akun_lawan_bank(Conn(), T, LAIN))


def test_kueri_peran_filter_tenant_di_kedua_tabel():
    c = Conn()
    _j(PU.akun_uang_muka_tersentuh(c, T, [DP]))
    sql = c.q[0][0]
    assert "ar.tenant_id = $1" in sql and "coa.tenant_id = ar.tenant_id" in sql and "ar.role_key = $2" in sql


def test_pagar_jurnal_manual_memanggil_cek_uang_muka():
    class L:
        def __init__(self, a):
            self.account_id = str(a)
    with pytest.raises(HTTPException) as e:
        _j(J.validate_no_derived_layer_accounts(Conn(), T, [L(LAIN), L(DP)]))
    assert "Uang Muka Pelanggan" in e.value.detail
    _j(J.validate_no_derived_layer_accounts(Conn(), T, [L(LAIN)]))


def test_buat_posting_balik_semuanya_lewat_pagar():
    assert "validate_no_derived_layer_accounts(" in inspect.getsource(J.create_journal)
    src_post = inspect.getsource(J.post_journal)
    assert 'journal["source_type"] == "MANUAL"' in src_post and "validate_no_derived_layer_accounts(" in src_post
    # pagar dijalankan SEBELUM UPDATE ... POSTED
    assert src_post.index("validate_no_derived_layer_accounts(") < src_post.index("SET status = 'POSTED'")
    assert "validate_no_derived_layer_accounts(" in inspect.getsource(J.reverse_journal)


def test_bank_manual_memanggil_pagar_sebelum_jurnal():
    src = inspect.getsource(BA.create_manual_transaction)
    assert "tolak_akun_lawan_bank(" in src
    assert src.index("tolak_akun_lawan_bank(") < src.index("INSERT INTO journal_entries")


def test_kontrol_jurnal_sistem_tak_melewati_pagar():
    """Penulis jurnal SISTEM (uang muka terima/terapkan/refund/lepas, NK, unapply penerimaan) TIDAK boleh memanggil
    pagar ini -- hanya jalur yang akunnya dipilih pengguna."""
    app = pathlib.Path(J.__file__).resolve().parents[1]
    boleh = {"routers/journals.py", "routers/bank_accounts.py", "services/pagar_uang_muka.py",
             "routers/opening_balance.py",  # 7 Okt: saldo awal = akun dipilih pengguna
             "services/pagar_akun_modul.py", "services/kernel_document_executor.py"}  # intake legacy: akun dari draf AI
    pemakai = sorted(str(p.relative_to(app)) for p in app.rglob("*.py")
                     if ("pagar_uang_muka" in p.read_text(errors="ignore")
                         or "validate_no_derived_layer_accounts" in p.read_text(errors="ignore")))
    assert set(pemakai) <= boleh, pemakai
    for sistem in ("routers/customer_deposits.py", "routers/receive_payments.py", "routers/credit_notes.py"):
        assert (app / sistem).exists()


# ---- 7 Okt 2026: SALDO AWAL ke akun Uang Muka (putusan pemilik = TOLAK) ----
from app.routers import opening_balance as OB
from app.schemas.opening_balance import AccountBalanceLine as Baris


def test_saldo_awal_ke_uang_muka_ditolak_berkode_pesan_indonesia():
    for b in (Baris(account_code="2-10500", credit=5_000_000), Baris(account_code="2-10500", debit=1)):
        with pytest.raises(HTTPException) as e:
            _j(PU.tolak_saldo_awal(Conn(), T, [Baris(account_code="1-10100", debit=5_000_000), b]))
        assert e.value.status_code == 400
        d = e.value.detail
        assert d["code"] == "SALDO_AWAL_UANG_MUKA_DITOLAK"
        assert "2-10500 Uang Muka Pelanggan" in d["message"] and "modul Uang Muka Pelanggan" in d["message"]


def test_saldo_awal_baris_nol_dan_akun_lain_lolos():
    c = Conn()
    _j(PU.tolak_saldo_awal(c, T, [Baris(account_code="1-10100", debit=5), Baris(account_code="2-10500")]))
    assert c.q[0][1][2] == ["1-10100"]  # baris nol tak ikut ditanyakan
    c = Conn()
    _j(PU.tolak_saldo_awal(c, T, [Baris(account_code="2-10500")]))
    assert c.q == []  # tak ada baris bernilai -> tak bertanya ke DB


def test_kueri_kode_filter_tenant_di_kedua_tabel():
    c = Conn()
    _j(PU.akun_uang_muka_dari_kode(c, T, ["2-10500"]))
    sql = c.q[0][0]
    assert "ar.tenant_id = $1" in sql and "coa.tenant_id = ar.tenant_id" in sql and "ar.role_key = $2" in sql


def test_rekening_bank_ke_uang_muka_ditolak_berkode():
    with pytest.raises(HTTPException) as e:
        _j(PU.tolak_rekening_bank(Conn(), T, DP))
    assert e.value.status_code == 400 and e.value.detail["code"] == "REKENING_BANK_UANG_MUKA_DITOLAK"
    _j(PU.tolak_rekening_bank(Conn(), T, LAIN))


def test_validasi_saldo_awal_menampilkan_galat_uang_muka(monkeypatch):
    async def akun(conn, tid, kode):
        return {"id": 1, "code": kode, "name": kode, "type": "X", "normal_balance": "DEBIT"}

    async def peran(conn, tid, role):
        return {"code": "zz-" + role}
    monkeypatch.setattr(OB, "get_account_by_code", akun)
    monkeypatch.setattr(OB, "_resolve_role_account", peran)
    req = OB.CreateOpeningBalanceRequest(opening_date="2026-01-01", accounts=[
        Baris(account_code="1-10100", debit=100), Baris(account_code="2-10500", credit=100)])
    v = _j(OB.validate_opening_balance_request(Conn(), T, req))
    assert not v.is_valid and any("Uang Muka Pelanggan" in g for g in v.errors)
    req = OB.CreateOpeningBalanceRequest(opening_date="2026-01-01", accounts=[
        Baris(account_code="1-10100", debit=100), Baris(account_code="3-10000", credit=100)])
    assert not any("Uang Muka" in g for g in _j(OB.validate_opening_balance_request(Conn(), T, req)).errors)


def test_buat_dan_ubah_saldo_awal_menolak_sebelum_transaksi():
    for f in (OB.create_opening_balance, OB.update_opening_balance):
        src = inspect.getsource(f)
        assert src.count("tolak_saldo_awal(conn, tenant_id, body.accounts)") == 1, f.__name__
        assert src.index("tolak_saldo_awal(") < src.index("async with conn.transaction()")


def test_buat_rekening_bank_menolak_sebelum_insert():
    src = inspect.getsource(BA.create_bank_account)
    assert src.index("tolak_rekening_bank(") < src.index("INSERT INTO bank_accounts")
