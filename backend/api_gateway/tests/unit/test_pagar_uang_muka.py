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
             "services/pagar_akun_modul.py", "services/kernel_document_executor.py"}  # intake legacy: akun dari draf AI
    pemakai = sorted(str(p.relative_to(app)) for p in app.rglob("*.py")
                     if ("pagar_uang_muka" in p.read_text(errors="ignore")
                         or "validate_no_derived_layer_accounts" in p.read_text(errors="ignore")))
    assert set(pemakai) <= boleh, pemakai
    for sistem in ("routers/customer_deposits.py", "routers/receive_payments.py", "routers/credit_notes.py"):
        assert (app / sistem).exists()
