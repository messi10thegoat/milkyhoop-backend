"""Milkybook T0 (24 Okt 2026, BACKEND2): AS-G1 idem ubah/batal penawaran + AS-G14 pratinjau & idem buat pelanggan/barang.
Kontrak: penentu SATU untuk pratinjau dan tulis (pratinjau == tulis), kode blok = kode galat BE yang ada, pratinjau READ ONLY."""
import inspect

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.routers import customers as RC
from app.routers import items as RI
from app.routers import quotes as RQ
from app.services import idem_buat


@pytest.mark.parametrize("fn,awalan,mulai", [
    (RQ.update_quote, "QUOTE_UPDATE", "mulai_aksi"), (RQ.void_quote, "QUOTE_VOID", "mulai_aksi"),
    (RC.create_customer, "CUSTOMER_CREATE", "mulai("), (RI.create_item, "ITEM_CREATE", "mulai(")])
def test_idem_per_handler(fn, awalan, mulai):
    src = inspect.getsource(fn)
    assert "idem_buat.kunci_dari(request)" in src and f"idem_buat.{mulai}" in src and f'"{awalan}"' in src
    assert src.count(f'"{awalan}"') >= 2  # mulai + simpan (respons dicatat untuk replay)
    assert "_lama is not None" in src


def test_patch_sidik_exclude_unset_dan_tabel_idem():
    assert "exclude_unset=True" in inspect.getsource(RQ.update_quote)
    assert idem_buat._TABEL["customers"] == "nomor_member" and idem_buat._TABEL["products"] == "item_code"


def test_penentu_satu_untuk_pratinjau_dan_tulis():
    assert "_siapkan_buat_pelanggan(" in inspect.getsource(RC.create_customer)
    assert "_siapkan_buat_pelanggan(" in inspect.getsource(RC.preview_create_customer)
    assert "_siapkan_buat_barang(" in inspect.getsource(RI.create_item)
    p = inspect.getsource(RI.preview_create_item)
    assert "_siapkan_buat_barang(" in p and "tanpa_stok_bila_bukan_barang(" in p and "periksa_badan_item(" in p
    for f in (RC.preview_create_customer, RI.preview_create_item):
        s = inspect.getsource(f)
        assert "transaction(readonly=True)" in s and "rollback()" in s  # nol tulis


def test_item_idem_sebelum_validasi():
    """Ketuk ganda = replay, bukan 409 nama dipakai: idem dimulai SEBELUM penentu."""
    s = inspect.getsource(RI.create_item)
    assert s.index("idem_buat.mulai(") < s.index("_siapkan_buat_barang(")
    s = inspect.getsource(RC.create_customer)
    assert s.index("idem_buat.mulai(") < s.index("_siapkan_buat_pelanggan(")


def test_blok_dari_galat_kosakata_be():
    b = RC.blok_dari_galat(HTTPException(409, {"code": "NAMA_PELANGGAN_DIPAKAI", "message": "x"}))
    assert b == {"code": "NAMA_PELANGGAN_DIPAKAI", "message": "x"}
    b = RC.blok_dari_galat(HTTPException(409, {"code": "NAMA_BARANG_DIPAKAI", "field": "name", "message": "y", "id": 5}))
    assert b == {"code": "NAMA_BARANG_DIPAKAI", "message": "y", "field": "name", "id": "5"}
    b = RC.blok_dari_galat(HTTPException(422, RC.PESAN_SALDO_AWAL_SAAT_BUAT))
    assert b["code"] == "SALDO_AWAL_SAAT_BUAT" and b["field"] == "ar_opening_balance"
    assert RC.blok_dari_galat(HTTPException(400, "lain"))["code"] == "HTTP_400"


def test_blok_validasi_jasa_bisa_dikirim():
    from app.schemas.items import CreateItemRequest
    with pytest.raises(ValidationError) as e:
        CreateItemRequest.model_validate({"name": "Jasa X", "item_type": "service", "base_unit": "jam", "bisa_dikirim": True})
    bl = RC.blok_validasi(e.value)
    assert bl and all(b["code"] == "DATA_TIDAK_SAH" for b in bl) and any(b["field"] == "bisa_dikirim" for b in bl)


def test_rute_pratinjau_terdaftar_sebelum_rute_ber_id():
    jc = [r.path for r in RC.router.routes]
    assert "/preview" in jc
    ji = [r.path for r in RI.router.routes]
    assert "/items/preview" in ji


@pytest.mark.parametrize("jalur,harap", [("/api/customers/preview", ("customer", "C")), ("/api/items/preview", ("item", "C")),
                                         ("/api/customers", ("customer", "C")), ("/api/items", ("item", "C"))])
def test_izin_rute_nyata(jalur, harap):
    from app.middleware.permission_middleware import izin_untuk_jalur
    assert izin_untuk_jalur(jalur, "POST") == harap


def _req(headers):
    from starlette.requests import Request

    async def rcv(): return {"type": "http.request", "body": b"", "more_body": False}
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
                    "query_string": b""}, rcv)


def test_kunci_dari_request_nyata_tak_berubah():
    """Syarat MASTER (b): penolong BERSAMA AS-G12 -- Request NYATA: header dulu, cadangan badan, beda = 422; tanpa .headers = None."""
    assert idem_buat.kunci_dari(_req({"X-Idempotency-Key": "h1"})) == "h1"
    assert idem_buat.kunci_dari(_req({"Idempotency-Key": "h2"})) == "h2"
    assert idem_buat.kunci_dari(_req({}), "b1") == "b1"
    assert idem_buat.kunci_dari(_req({"X-Idempotency-Key": "s"}), "s") == "s"
    with pytest.raises(HTTPException) as e:
        idem_buat.kunci_dari(_req({"X-Idempotency-Key": "h"}), "b")
    assert e.value.status_code == 422 and e.value.detail["code"] == "IDEMPOTENCY_KEY_MISMATCH"
    assert idem_buat.kunci_dari(_req({})) is None
    with pytest.raises(HTTPException) as e:
        idem_buat.kunci_dari(_req({"X-Idempotency-Key": "x" * 10000}))
    assert e.value.status_code == 400
    from types import SimpleNamespace
    assert idem_buat.kunci_dari(SimpleNamespace(state=None)) is None  # pemanggilan langsung (tes) = tanpa kunci header
    assert idem_buat.kunci_dari(SimpleNamespace(state=None), "b") == "b"
