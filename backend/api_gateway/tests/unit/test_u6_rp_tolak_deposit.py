"""U6 (4 Okt 2026, putusan MASTER Opsi A): penerimaan BERSUMBER UANG MUKA dihentikan + void append-only defensif.

Penjaga: (1) buat (dan ubah draf) source_type='deposit' ditolak 422 dgn kode stabil + pesan Indonesia SEBELUM kueri/penulisan
apa pun; (2) NOL `DELETE FROM customer_deposit_applications` di seluruh app (dulu void menghapus baris -> riwayat DITERAPKAN+
DILEPAS lenyap); void menandai status='reversed', reversed_by_id = jurnal void, tenant eksplisit, idempoten ('active' saja),
tanpa jurnal baru; (3) anomalies idle menghitung hanya aplikasi 'active' (pembatalan bukan aktivitas); (4) pemanggil internal
buat_penerimaan tak pernah membawa sumber deposit."""
import ast
import asyncio
import inspect
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routers import anomalies as AN
from app.routers import receive_payments as RP
from app.schemas.receive_payments import CreateReceivePaymentRequest

APP = Path(__file__).resolve().parents[2] / "app"


def test_tolak_sumber_deposit_kode_stabil_dan_pesan_indonesia():
    with pytest.raises(HTTPException) as e:
        RP.tolak_sumber_deposit()
    assert e.value.status_code == 422
    assert e.value.detail["code"] == "RP_SUMBER_DEPOSIT_DIHENTIKAN"
    assert "Terapkan uang muka" in e.value.detail["message"] and "Penerimaan" in e.value.detail["message"]


class _KonnTerlarang:
    """Koneksi yang meledak bila disentuh: pagar harus bekerja SEBELUM kueri apa pun."""
    def __getattr__(self, n):
        raise AssertionError(f"koneksi disentuh ({n}) sebelum penolakan")


def _body(**u):
    d = dict(customer_id="c1", payment_date=date(2026, 10, 4), bank_account_id="b1", total_amount=Decimal("10000"),
             allocations=[{"invoice_id": "i1", "amount_applied": Decimal("10000")}])
    d.update(u)
    return CreateReceivePaymentRequest(**d)


def test_buat_penerimaan_deposit_ditolak_sebelum_kueri():
    b = _body(source_type="deposit", source_deposit_id="d1")
    with pytest.raises(HTTPException) as e:
        asyncio.run(RP.buat_penerimaan(_KonnTerlarang(), {"tenant_id": "t1", "user_id": None}, b))
    assert e.value.status_code == 422 and e.value.detail["code"] == "RP_SUMBER_DEPOSIT_DIHENTIKAN"
    # sebagai draf pun ditolak
    b2 = _body(source_type="deposit", source_deposit_id="d1", save_as_draft=True)
    with pytest.raises(HTTPException) as e2:
        asyncio.run(RP.buat_penerimaan(_KonnTerlarang(), {"tenant_id": "t1", "user_id": None}, b2))
    assert e2.value.status_code == 422


def test_buat_penerimaan_cash_tetap_lolos_pagar():
    """Kontrol: sumber cash TIDAK ditolak pagar (ia lanjut ke kueri pertama -> koneksi terlarang meledak = pagar lewat)."""
    with pytest.raises(AssertionError):
        asyncio.run(RP.buat_penerimaan(_KonnTerlarang(), {"tenant_id": "t1", "user_id": None}, _body()))


def test_ubah_draf_tak_boleh_dialihkan_ke_sumber_deposit():
    src = inspect.getsource(RP.update_receive_payment)
    pagar = 'update_data.get("source_type") == "deposit" or update_data.get("source_deposit_id")'
    assert pagar in src and "tolak_sumber_deposit()" in src
    assert src.index(pagar) < src.index("pelanggan_kanonik_tenant")  # sebelum penulisan apa pun


def test_nol_delete_aplikasi_uang_muka_di_seluruh_app():
    ada = []
    for p in APP.rglob("*.py"):
        if re.search(r"DELETE\s+FROM\s+customer_deposit_applications", p.read_text(encoding="utf-8"), re.I):
            ada.append(str(p.relative_to(APP)))
    assert not ada, f"baris aplikasi uang muka tak boleh dihapus (append-only): {ada}"


def test_void_menandai_reversed_append_only():
    src = " ".join(inspect.getsource(RP._tulis_void_pembayaran).split())
    assert "UPDATE customer_deposit_applications SET status = 'reversed', reversed_by_id = $3, reversed_at = NOW() " \
           "WHERE deposit_id = $1 AND journal_id = $2 AND tenant_id = $4 AND status = 'active'" in src
    # urutan argumen: deposit, jurnal penerimaan, JURNAL VOID (bukan jurnal baru), tenant
    m = re.search(r"AND status = 'active'\s*\"\"\",\s*payment\[\"source_deposit_id\"\],\s*payment\[\"journal_id\"\],\s*"
                  r"void_journal_id,\s*ctx\[\"tenant_id\"\],", inspect.getsource(RP._tulis_void_pembayaran))
    assert m, "argumen UPDATE reversed tak sesuai ($1 deposit, $2 jurnal penerimaan, $3 void_journal_id, $4 tenant)"


def test_anomalies_idle_hanya_aplikasi_aktif():
    src = " ".join(inspect.getsource(AN._check_deposit_idle).split())
    assert "WHERE a.deposit_id = d.id AND a.status = 'active'" in src
    assert "customer_deposit_refunds" in src  # refund tetap dihitung


def test_pemanggil_internal_buat_penerimaan_tak_membawa_sumber_deposit():
    """Pemanggil internal (faktur, pelunasan SO) membangun body SENDIRI: tak boleh ada source_type deposit di sana.
    Faktur: keyword di panggilan CreateReceivePaymentRequest(...); pelunasan SO: kunci dict payload."""
    pohon = ast.parse((APP / "routers/sales_invoices.py").read_text(encoding="utf-8"))
    panggilan = [n for n in ast.walk(pohon) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "CreateReceivePaymentRequest"]
    assert panggilan, "kontrol positif: pemanggil faktur tak ditemukan"
    for c in panggilan:
        assert not {k.arg for k in c.keywords} & {"source_type", "source_deposit_id"}
    pohon2 = ast.parse((APP / "services/so_pelunasan.py").read_text(encoding="utf-8"))
    kunci = {k.value for n in ast.walk(pohon2) if isinstance(n, ast.Dict) for k in n.keys if isinstance(k, ast.Constant)}
    assert "source_type" not in kunci and "source_deposit_id" not in kunci
    # kontrol: pendeteksi bisa menemukan keyword terlarang
    uji = ast.parse("CreateReceivePaymentRequest(source_type='deposit')").body[0].value
    assert {k.arg for k in uji.keywords} & {"source_type"}
