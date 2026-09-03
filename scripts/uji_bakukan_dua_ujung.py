#!/usr/bin/env python3
"""Gerbang: pembakuan id sesi bekerja di DUA UJUNG — tulis DAN baca.

APA YANG DIBUKTIKAN
Klien yang mengirim UUID huruf KAPITAL (mis. `UUID().uuidString` di Swift)
harus (1) menulis baris dengan id huruf kecil, DAN (2) menemukan kembali
barisnya ketika ia mencari dengan huruf KAPITAL lagi.

KENAPA KEDUANYA, BUKAN SALAH SATU
Sebelum tambalan, klien itu konsisten-salah: kapital di kedua ujung, jadi
alurnya tetap jalan. Kalau HANYA sisi tulis dibakukan, ia akan menulis huruf
kecil lalu mencari dengan kapital dan TIDAK menemukan apa pun — aksi
tertundanya mati. Regresi yang kita ciptakan sendiri. Karena itu gerbang ini
menguji perjalanan pulang-pergi, bukan bentuk simpanannya saja.

KONTROL NEGATIF (wajib MERAH)
Pembakuan dicabut DI SISI BACA SAJA, lalu pencarian dengan huruf kapital
harus GAGAL menemukan baris yang sama. Kalau kontrol ini tetap hijau, gerbang
ini tidak mengukur apa pun dan tak boleh dipercaya.

Seluruh uji berjalan di dalam SATU transaksi yang selalu ROLLBACK.

CATATAN SESUDAH V236 (3 Sep 2026): kolom `chat_session_id` kini bertipe
`uuid`, jadi pembakuan dijamin DUA KALI -- oleh helper di Python dan oleh tipe
kolom. Uji ini tetap bernilai karena ia menguji sisi BACA: helper yang dicabut
di sisi baca tetap membuat pencarian berhuruf kapital MELESET, tipe kolom tidak
menolongnya. Kontrol negatif di bawah membuktikan itu.

Pakai: python3 scripts/uji_bakukan_dua_ujung.py
"""

import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "api_gateway"))

import asyncpg  # noqa: E402

from app.services.unified_agent import workflow_engine as wf  # noqa: E402
from app.services.unified_agent.db_utils import bakukan_session_id  # noqa: E402

JENIS = "bank_reconciliation"


async def utama() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("GAGAL: DATABASE_URL tidak diset.", file=sys.stderr)
        return 2

    conn = await asyncpg.connect(dsn)
    tx = conn.transaction()
    await tx.start()
    gagal = []
    try:
        induk = await conn.fetchrow(
            "SELECT tenant_id, user_id FROM chat_sessions WHERE user_id IS NOT NULL LIMIT 1"
        )
        if not induk:
            print("GAGAL: tak ada sesi untuk dijadikan induk.", file=sys.stderr)
            return 2

        sid = await conn.fetchval(
            "INSERT INTO chat_sessions (tenant_id, user_id) VALUES ($1, $2) RETURNING id::text",
            induk["tenant_id"],
            induk["user_id"],
        )
        KAPITAL = sid.upper()
        assert KAPITAL != sid, "uuid tanpa huruf; ulangi"

        engine = wf.WorkflowEngine(
            db_pool=conn, tenant_id=induk["tenant_id"], user_id=str(induk["user_id"]), auth_token=""
        )

        # --- UJUNG 1: TULIS dengan huruf KAPITAL -------------------------
        await engine._load_or_create(KAPITAL, JENIS)
        # V236 mengubah kolom ini jadi `uuid`, jadi `lower(...)` tak lagi ada
        # (dan tak lagi perlu). Bandingkan bentuk teksnya.
        tersimpan = await conn.fetchval(
            "SELECT chat_session_id::text FROM chat_workflow_state "
            "WHERE workflow_type = $1 AND chat_session_id = $2::uuid",
            JENIS,
            sid,
        )
        if tersimpan == sid:
            print(f"OK   TULIS: kirim KAPITAL -> tersimpan huruf kecil ({tersimpan[:8]}...)")
        else:
            gagal.append(f"TULIS: tersimpan {tersimpan!r}, seharusnya {sid!r}")

        # --- UJUNG 2: BACA kembali dengan huruf KAPITAL -------------------
        ctx = await engine._load(KAPITAL, JENIS)
        if ctx:
            print("OK   BACA : cari pakai KAPITAL -> baris yang sama DITEMUKAN")
        else:
            gagal.append("BACA: cari pakai KAPITAL tidak menemukan barisnya")

        # --- KONTROL NEGATIF: cabut pembakuan di sisi BACA saja -----------
        asli = wf.__dict__.get("bakukan_session_id")
        import app.services.unified_agent.db_utils as du

        du_asli = du.bakukan_session_id
        du.bakukan_session_id = lambda v: v  # identitas: seolah tak dibakukan
        try:
            ctx_kontrol = await engine._load(KAPITAL, JENIS)
        finally:
            du.bakukan_session_id = du_asli
            if asli is not None:
                wf.bakukan_session_id = asli

        if ctx_kontrol is None:
            print("OK   KONTROL: pembakuan dicabut di sisi BACA -> MERAH (tidak ketemu)")
        else:
            gagal.append(
                "KONTROL TIDAK MERAH: tanpa pembakuan pun barisnya ketemu -> "
                "gerbang ini tidak mengukur apa pun"
            )

        # --- sentinel non-uuid harus lewat apa adanya ---------------------
        if bakukan_session_id("unknown") == "unknown":
            print("OK   SENTINEL: nilai non-uuid ('unknown') lewat tanpa diubah")
        else:
            gagal.append("SENTINEL: nilai non-uuid ikut diubah")

    finally:
        await tx.rollback()
        await conn.close()

    if gagal:
        print("\nGAGAL:", file=sys.stderr)
        for g in gagal:
            print("  - " + g, file=sys.stderr)
        return 1
    print("\nOK: pembakuan terbukti bekerja di DUA UJUNG, dan kontrol negatifnya MERAH.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(utama()))
