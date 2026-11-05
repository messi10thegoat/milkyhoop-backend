"""T-G5 (5 Nov 2026): 422 validasi Pydantic -> Bahasa Indonesia + kode VALIDASI_GAGAL (pola T-G3/GalatBerkode)."""
import os
from datetime import date
from decimal import Decimal
from typing import List, Literal

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")  # impor app.main butuh satu penyedia LLM

from fastapi import FastAPI  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import BaseModel, Field, field_validator  # noqa: E402

from app.services import galat_validasi as GV  # noqa: E402


class Baris(BaseModel):
    quantity: Decimal = Field(gt=0)


class Faktur(BaseModel):
    bill_date: date
    vendor_id: str
    jenis: Literal["a", "b"] = "a"
    items: List[Baris] = []

    @field_validator("vendor_id")
    @classmethod
    def _v(cls, v):
        if v == "x":
            raise ValueError("Vendor tidak dikenal.")
        return v


def _klien():
    from app.main import _validasi_handler
    mini = FastAPI()
    mini.add_exception_handler(RequestValidationError, _validasi_handler)

    @mini.post("/f")
    async def f(body: Faktur):
        return {"ok": True}
    return TestClient(mini)


def test_contoh_po_faktur_bill_date_bahasa_indonesia_berkode():
    r = _klien().post("/f", json={"bill_date": "2026-1x-05", "vendor_id": "v"})
    b = r.json()
    assert r.status_code == 422
    assert (b["code"], b["message"], b["field"]) == ("VALIDASI_GAGAL", "bill_date: tanggal tidak valid.", "bill_date")
    assert b["detail"][0]["loc"] == ["body", "bill_date"] and b["detail"][0]["msg"] == "tanggal tidak valid."
    assert "Input should" not in r.text


def test_banyak_galat_semua_msg_indonesia_pesan_galat_pertama():
    r = _klien().post("/f", json={"vendor_id": "x", "jenis": "z", "items": [{"quantity": 0}]})
    b = r.json()
    msgs = {".".join(map(str, d["loc"][1:])): d["msg"] for d in b["detail"]}
    assert msgs == {"bill_date": "wajib diisi.", "vendor_id": "Vendor tidak dikenal.", "jenis": "pilihan tidak dikenal.",
                    "items.0.quantity": "harus lebih dari 0."}
    assert b["message"] == "bill_date: wajib diisi." and b["field"] == "bill_date"


def test_badan_bukan_json():
    r = _klien().post("/f", content=b"{rusak", headers={"content-type": "application/json"})
    assert r.status_code == 422 and r.json()["code"] == "VALIDASI_GAGAL" and "JSON" in r.json()["message"]


def test_tipe_tak_dikenal_jatuh_ke_kalimat_umum():
    assert GV.kalimat({"type": "tipe_baru_pydantic", "msg": "Something English"}) == "isian tidak valid."
    assert GV.badan([]) == {"detail": [], "code": "VALIDASI_GAGAL", "message": "Isian tidak valid."}


def test_handler_terpasang_di_app_utama():
    from app.main import app, _validasi_handler
    assert app.exception_handlers[RequestValidationError] is _validasi_handler
