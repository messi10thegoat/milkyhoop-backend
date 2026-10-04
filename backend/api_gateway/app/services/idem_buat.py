"""Idempotensi BUAT dokumen dengan header X-Idempotency-Key (pola W0 / POST /sales-invoices, 4 Okt 2026).

Satu tempat untuk jalur buat yang baru dipagari (nota kredit, penawaran): kunci {PREFIX}:{user}:{kunci}, sidik badan,
IDEM lock + replay SEBELUM nomor terbit, badan beda -> 409 IDEMPOTENCY_KEY_REUSED + id/nomor dokumen tersimpan (dibaca
ulang berpagar tenant), respons dicatat di tx yang SAMA dengan dokumennya. Tanpa kunci = perilaku lama.
`tabel`/`kolom_nomor` = literal per pemanggil (bukan masukan pengguna)."""
from uuid import UUID

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from ..utils.idempotency import ambil_replay_klien, hash_payload, kunci_idempotensi_klien, simpan_replay_klien

_TABEL = {"credit_notes": "credit_note_number", "quotes": "quote_number"}


def kunci_dari(request):
    try:
        return kunci_idempotensi_klien(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


async def mulai(conn, ctx: dict, kunci_klien, prefix: str, body, tabel: str, nama: str, response=None):
    """-> (kunci_penuh, sidik, respons_lama|None). WAJIB di dalam transaksi, sebelum penomoran/penulisan."""
    if not kunci_klien:
        return None, None, None
    kolom = _TABEL[tabel]
    kunci_penuh = f"{prefix}:{ctx['user_id']}:{kunci_klien}"
    sidik = hash_payload(body.model_dump(mode="json"))
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"IDEM:{ctx['tenant_id']}:{kunci_penuh}")
    try:
        lama = await ambil_replay_klien(conn, ctx["tenant_id"], kunci_penuh, sidik)
    except LookupError as e:
        asli = (getattr(e, "respons", None) or {}).get("data") or {}
        dok = None
        if asli.get("id"):
            dok = await conn.fetchrow(f"SELECT id, {kolom} AS nomor FROM {tabel} WHERE id = $1 AND tenant_id = $2",
                                      UUID(str(asli["id"])), ctx["tenant_id"])
        raise HTTPException(status_code=409, detail={
            "code": "IDEMPOTENCY_KEY_REUSED",
            "message": "Idempotency-Key sudah dipakai untuk dokumen dengan isi berbeda",
            f"{nama}_id": str(dok["id"]) if dok else None,
            f"{nama}_number": dok["nomor"] if dok else None})
    if lama is not None and response is not None:
        response.headers["X-Idempotent-Replay"] = "true"
    return kunci_penuh, sidik, lama


async def simpan(conn, ctx: dict, kunci_penuh, sidik, source_type: str, resp, result_id) -> dict:
    """Respons -> bentuk JSON yang SAMA untuk jawaban pertama dan replay; dicatat di tx yang sama bila berkunci."""
    resp = jsonable_encoder(resp)
    if kunci_penuh:
        await simpan_replay_klien(conn, ctx["tenant_id"], kunci_penuh, source_type, sidik, resp, result_id=result_id)
    return resp


async def mulai_aksi(conn, ctx: dict, kunci_klien, prefix: str, doc_id, isi: dict, response=None):
    """Idempotensi AKSI pada dokumen yang sudah ada (void/refund/apply/terbit...): kunci {PREFIX}:{user}:{doc}:{kunci},
    sidik isi, IDEM lock + replay. Kunci sama + isi beda -> 409. -> (kunci_penuh, sidik, respons_lama|None)."""
    if not kunci_klien:
        return None, None, None
    kunci_penuh = f"{prefix}:{ctx['user_id']}:{doc_id}:{kunci_klien}"
    sidik = hash_payload(isi)
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"IDEM:{ctx['tenant_id']}:{kunci_penuh}")
    try:
        lama = await ambil_replay_klien(conn, ctx["tenant_id"], kunci_penuh, sidik)
    except LookupError:
        raise HTTPException(status_code=409, detail={
            "code": "IDEMPOTENCY_KEY_REUSED", "message": "Idempotency-Key sudah dipakai untuk aksi dengan isi berbeda"})
    if lama is not None and response is not None:
        response.headers["X-Idempotent-Replay"] = "true"
    return kunci_penuh, sidik, lama
