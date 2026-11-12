"""BUG-185: badan biner/multipart ke rute ber-model JSON TIDAK boleh 500. Penangan 422 BERSAMA (services/galat_validasi.validasi_handler, didaftarkan main.py) membuang `input` bytes
(penanda '<biner N byte>', TANPA decode: isi biner tak bocor); loc/msg/type tetap; galat validasi JSON biasa isinya SAMA seperti sebelumnya."""
import pathlib
import re
import uuid

import pytest
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from starlette.testclient import TestClient

from app.routers.expenses import AddAttachmentRequest
from app.services import galat_validasi as GV

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(128, 200))
MULTIPART = (b'--zz\r\nContent-Disposition: form-data; name="file"; filename="n.png"\r\nContent-Type: image/png\r\n\r\n' + PNG + b"\r\n--zz--\r\n")


class Badan(BaseModel):
    nama: str = Field(..., min_length=1)
    jumlah: int = Field(..., gt=0)


def app_uji():
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, GV.validasi_handler)

    @app.post("/expenses/{eid}/attachments")
    async def lampir(eid: uuid.UUID, body: AddAttachmentRequest):      # bentuk SAMA dgn POST /api/expenses/{id}/attachments
        return {"ok": True}

    @app.post("/umum")
    async def umum(body: Badan):
        return {"ok": True}
    return TestClient(app, raise_server_exceptions=False)


def test_multipart_png_ke_rute_lampiran_biaya_422_bukan_500():
    r = app_uji().post(f"/expenses/{uuid.uuid4()}/attachments", content=MULTIPART, headers={"Content-Type": "multipart/form-data; boundary=zz"})
    assert r.status_code == 422, r.text
    j = r.json()
    assert j["code"] == GV.KODE and j["detail"] and j["message"]
    assert "PNG" not in r.text and "\\x89" not in r.text and "<biner" in r.text            # isi biner tak bocor; penanda ada


@pytest.mark.parametrize("isi", [b"\x89\x00\xff\xfe", b"\xff" * 5000, b"\x80abc", b""])
def test_badan_biner_apa_pun_tidak_500(isi):
    for ctype in ("application/octet-stream", "multipart/form-data; boundary=zz", "application/json", "text/plain"):
        r = app_uji().post("/umum", content=isi, headers={"Content-Type": ctype})
        assert r.status_code in (400, 422), (ctype, r.status_code, r.text[:120])


def test_galat_json_biasa_isinya_sama_seperti_sebelumnya():
    r = app_uji().post("/umum", json={"nama": "", "jumlah": 0})
    assert r.status_code == 422
    sebelum = GV.badan(jsonable_encoder([{"type": "string_too_short", "loc": ("body", "nama"), "msg": "String should have at least 1 character", "input": "", "ctx": {"min_length": 1}},
                                         {"type": "greater_than", "loc": ("body", "jumlah"), "msg": "Input should be greater than 0", "input": 0, "ctx": {"gt": 0}}]))
    j = r.json()
    assert j == sebelum and [d["loc"] for d in j["detail"]] == [["body", "nama"], ["body", "jumlah"]] and all(d["type"] and d["msg"] for d in j["detail"])


def test_bersihkan_hanya_input_bytes():
    e = [{"type": "t", "loc": ("body",), "msg": "m", "input": b"\x89ab", "ctx": {"a": 1}}, {"type": "u", "loc": ("body", "x"), "msg": "n", "input": {"k": bytearray(b"\xff\xfe"), "ok": 1}},
         {"type": "v", "loc": ("query", "q"), "msg": "o", "input": "teks"}, {"type": "w", "loc": ("body",), "msg": "p"}]
    h = GV.bersihkan(e)
    assert h[0]["input"] == "<biner 3 byte>" and h[0]["loc"] == ("body",) and h[0]["msg"] == "m" and h[0]["type"] == "t" and h[0]["ctx"] == {"a": 1}
    assert h[1]["input"] == {"k": "<biner 2 byte>", "ok": 1} and h[2] == e[2] and h[3] == e[3] and e[0]["input"] == b"\x89ab"       # asli tak diubah


def test_main_mendaftarkan_penangan_bersama():
    src = (pathlib.Path(__file__).resolve().parents[2] / "app" / "main.py").read_text()
    assert "_validasi_handler = _galat_validasi.validasi_handler" in src and re.search(r"add_exception_handler\(_RequestValidationError, _validasi_handler\)", src)
    assert "_jsonable(exc.errors())" not in src
