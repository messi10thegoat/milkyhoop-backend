"""Respons UM vendor (temuan uji nyata WORKSPACE 23 Okt 2026): after.position_text dihitung ulang dari angka sesudah; /void mengisi
reversal_journal_id. Buku tak berubah -- ini hanya bentuk respons."""
import inspect

from app.routers import vendor_deposits as RV
from app.services import uang_muka_vendor_rencana as R

UTUH = {"exists": True, "status": "posted", "amount": 1000.0, "amount_applied": 0.0, "amount_refunded": 0.0, "remaining": 1000.0,
        "position_text": "Belum diterapkan"}


def test_pratinjau_batal_dibatalkan():
    assert R.sesudah(UTUH, status="void", remaining=None)["position_text"] == "Dibatalkan"


def test_terapkan_penuh_dan_sebagian():
    assert R.sesudah(UTUH, amount_applied=1000.0, remaining=0.0)["position_text"] == "Diterapkan penuh"
    assert R.sesudah(UTUH, amount_applied=400.0, remaining=600.0)["position_text"] == "Diterapkan sebagian"


def test_kembalikan_penuh_dan_lepas_kembali_utuh():
    assert R.sesudah(UTUH, amount_refunded=1000.0, remaining=0.0)["position_text"] == "Dikembalikan"
    terpakai = R.sesudah(UTUH, amount_applied=1000.0, remaining=0.0)
    assert R.sesudah(terpakai, amount_applied=0.0, remaining=1000.0)["position_text"] == "Belum diterapkan"


def test_tak_ada_tetap_tanpa_teks():
    assert R.sesudah({"exists": False}) == {"exists": False}


def test_void_mengisi_reversal_journal_id():
    src = inspect.getsource(RV)
    blok = src.split("return VoidDepositResponse(")[1:]
    assert len(blok) == 2  # jalur idempoten (sudah batal) + jalur void pertama
    assert "reversed_by_id FROM journal_entries" in blok[0][:600]  # idempoten: pembalik yang sudah ada
    assert "reversal_journal_id=reversal_id" in blok[1][:300]
