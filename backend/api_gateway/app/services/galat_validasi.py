"""Galat validasi permintaan (422 Pydantic) berbahasa Indonesia + berkode (T-G5, 5 Nov 2026; pola T-G3/GalatBerkode).

Dulu: badan bawaan FastAPI {"detail": [{type, loc, msg: "Input should be a valid date or datetime, ..."}]} -> layar berbahasa Inggris.
Kini: {"detail": [... msg Indonesia, type/loc/input tetap ...], "code": VALIDASI_GAGAL, "message": <galat pertama>, "field": <medan>}.
- detail TETAP daftar (pembaca lama FE memetakan detail[].msg / loc); message didahulukan bacaPesanGalat FE.
- Validator kustom ("Value error, <kalimat>") = kalimat kita sendiri -> awalan Pydantic dibuang.
"""
KODE = "VALIDASI_GAGAL"

_TETAP = {
    "missing": "wajib diisi.",
    "date_parsing": "tanggal tidak valid.",
    "date_from_datetime_parsing": "tanggal tidak valid.",
    "date_from_datetime_inexact": "tanggal tidak boleh memuat jam.",
    "date_type": "tanggal tidak valid.",
    "datetime_parsing": "tanggal dan jam tidak valid.",
    "datetime_from_date_parsing": "tanggal dan jam tidak valid.",
    "datetime_type": "tanggal dan jam tidak valid.",
    "time_parsing": "jam tidak valid.",
    "decimal_parsing": "harus angka.",
    "decimal_type": "harus angka.",
    "float_parsing": "harus angka.",
    "float_type": "harus angka.",
    "int_parsing": "harus bilangan bulat.",
    "int_type": "harus bilangan bulat.",
    "int_from_float": "harus bilangan bulat.",
    "finite_number": "harus angka.",
    "bool_parsing": "harus ya atau tidak.",
    "bool_type": "harus ya atau tidak.",
    "uuid_parsing": "ID tidak valid.",
    "uuid_type": "ID tidak valid.",
    "string_type": "harus teks.",
    "list_type": "harus daftar.",
    "dict_type": "harus objek.",
    "model_type": "harus objek.",
    "model_attributes_type": "harus objek.",
    "literal_error": "pilihan tidak dikenal.",
    "enum": "pilihan tidak dikenal.",
    "json_invalid": "badan permintaan bukan JSON yang valid.",
    "extra_forbidden": "isian tidak dikenal.",
}


def kalimat(e: dict) -> str:
    """Satu galat Pydantic -> kalimat Indonesia (tanpa nama medan)."""
    t, ctx = e.get("type", ""), e.get("ctx") or {}
    if t in ("value_error", "assertion_error"):
        m = str(e.get("msg", ""))
        for awalan in ("Value error, ", "Assertion failed, "):
            if m.startswith(awalan):
                return m[len(awalan):]
        return m or "isian tidak valid."
    if t in _TETAP:
        return _TETAP[t]
    batas = {"greater_than": ("harus lebih dari", "gt"), "greater_than_equal": ("minimal", "ge"),
             "less_than": ("harus kurang dari", "lt"), "less_than_equal": ("maksimal", "le"),
             "string_too_short": ("minimal panjang", "min_length"), "string_too_long": ("maksimal panjang", "max_length"),
             "too_short": ("minimal jumlah", "min_length"), "too_long": ("maksimal jumlah", "max_length"),
             "decimal_max_places": ("maksimal desimal", "decimal_places"), "decimal_max_digits": ("maksimal digit", "max_digits")}
    if t in batas and batas[t][1] in ctx:
        return f"{batas[t][0]} {ctx[batas[t][1]]}."
    return "isian tidak valid."


def medan(e: dict) -> str:
    """loc tanpa awalan sumber: ('body', 'items', 0, 'quantity') -> 'items.0.quantity'."""
    loc = list(e.get("loc") or ())
    if loc and loc[0] in ("body", "query", "path", "header", "cookie"):
        loc = loc[1:]
    return ".".join(str(x) for x in loc)


def badan(errors: list) -> dict:
    """errors = jsonable_encoder(exc.errors()) -> badan respons 422."""
    detail = [{**e, "msg": kalimat(e)} for e in errors]
    pertama = errors[0] if errors else {}
    m, k = medan(pertama), kalimat(pertama) if errors else "isian tidak valid."
    pesan = f"{m}: {k}" if m else k[:1].upper() + k[1:]
    out = {"detail": detail, "code": KODE, "message": pesan}
    if m:
        out["field"] = m
    return out


def _aman(v):
    """Nilai `input` galat validasi yang aman dijadikan JSON: bytes/bytearray TIDAK didecode (isi biner bisa bocor ke respons / UnicodeDecodeError -> 500), diganti penanda panjang."""
    if isinstance(v, (bytes, bytearray)):
        return f"<biner {len(v)} byte>"
    if isinstance(v, dict):
        return {k: _aman(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_aman(x) for x in v]
    return v


def bersihkan(errors: list) -> list:
    """exc.errors() -> salinan dgn `input` bytes diganti penanda; loc/msg/type/ctx TETAP (BUG-185: badan biner/multipart ke rute ber-model JSON dulu = 500)."""
    return [{**e, "input": _aman(e["input"])} if "input" in e else e for e in errors]


async def validasi_handler(request, exc):  # noqa: ANN001
    """Penangan RequestValidationError BERSAMA (didaftarkan app/main.py): 422 berkode, input apa pun TIDAK boleh menghasilkan 500."""
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=422, content=badan(jsonable_encoder(bersihkan(exc.errors()))))
