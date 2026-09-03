#!/usr/bin/env python3
"""Penegak: id sesi chat yang disimpan di luar `chat_sessions` tetap baku.

RIWAYAT BERKAS INI (penting, karena artinya BERUBAH)
-----------------------------------------------------
Versi pertama (3 Sep 2026) menjaga `pending_actions.conversation_id` dan
`chat_workflow_state.chat_session_id` yang saat itu bertipe TEXT: ia menghitung
baris berhuruf KAPITAL, karena `chat_sessions.id` bertipe `uuid` (selalu huruf
kecil) sementara kolom teks menyimpan apa adanya dari klien. 153 baris pernah
terbaca "yatim" gara-gara itu.

V236 mengubah kedua kolom itu menjadi `uuid`. Sesudah itu versi lama berkas ini
MATI TOTAL -- bukan merah, tapi MELEDAK: `lower(uuid)` bukan fungsi yang ada.
Gerbang yang meledak karena sebab yang tak ada hubungannya dengan yang diukur
sama tak bergunanya dengan gerbang yang tak bisa merah. Itu sebabnya berkas ini
ditulis ulang, bukan ditambal.

APA YANG DIJAGA SEKARANG
  1. TIPE kolom masih `uuid`. Kalau seseorang mengembalikannya ke `text`,
     jaminan pembakuan hilang diam-diam dan bug 29-30 Agustus bisa lahir lagi.
  2. Tak ada baris yang menunjuk sesi yang tidak ada.

KONTROL (--kontrol) menanam nilai berhuruf KAPITAL lalu menuntut yang
TERSIMPAN berbentuk kanonik huruf kecil.

⚠️ Dugaan pertamaku keliru dan kontrolnya sendiri yang membantah: tipe `uuid`
TIDAK MENOLAK huruf kapital -- masukan uuid di Postgres case-insensitive, jadi
`'AABB...'::uuid` diterima lalu DIBAKUKAN. Jaminannya karena itu bukan
"menolak" melainkan "menyimpan kanonik". Kalau tipe kolom kelak dikembalikan
ke `text`, nilai kapital akan tersimpan APA ADANYA dan kontrol ini MERAH.

Pakai:  python3 scripts/cek_sesi_id_beda_huruf.py
        python3 scripts/cek_sesi_id_beda_huruf.py --kontrol
"""

import asyncio
import os
import sys

import asyncpg

PASANGAN = [
    ("pending_actions", "conversation_id"),
    ("chat_workflow_state", "chat_session_id"),
]


async def utama(kontrol: bool) -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("GAGAL: DATABASE_URL tidak diset.", file=sys.stderr)
        return 2

    conn = await asyncpg.connect(dsn)
    try:
        if kontrol:
            tx = conn.transaction()
            await tx.start()
            try:
                induk = await conn.fetchrow(
                    "SELECT id::text AS id, tenant_id, user_id FROM chat_sessions "
                    "WHERE user_id IS NOT NULL LIMIT 1"
                )
                if not induk:
                    print("GAGAL: tak ada sesi untuk dijadikan kontrol.", file=sys.stderr)
                    return 2
                kapital = induk["id"].upper()
                await conn.execute(
                    "INSERT INTO chat_workflow_state "
                    "(chat_session_id, tenant_id, user_id, workflow_type) "
                    "VALUES ($1, $2, $3, $4)",
                    kapital,
                    induk["tenant_id"],
                    induk["user_id"],
                    "_kontrol_pembakuan",
                )
                tersimpan = await conn.fetchval(
                    "SELECT chat_session_id::text FROM chat_workflow_state "
                    "WHERE workflow_type = $1",
                    "_kontrol_pembakuan",
                )
                if tersimpan == induk["id"]:
                    print(
                        f"KONTROL: dikirim {kapital[:8]}... (KAPITAL) -> "
                        f"tersimpan {tersimpan[:8]}... (kanonik). Jaminan tipe bekerja."
                    )
                    return 0
                print(
                    f"KONTROL MERAH: dikirim KAPITAL, tersimpan {tersimpan!r} "
                    "apa adanya. Tipe kolom kemungkinan sudah dikembalikan ke "
                    "`text` -- jaminan pembakuan HILANG.",
                    file=sys.stderr,
                )
                return 1
            finally:
                await tx.rollback()

        gagal = []

        # 1. tipe kolom
        for tabel, kolom in PASANGAN:
            tipe = await conn.fetchval(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=$1 AND column_name=$2",
                tabel,
                kolom,
            )
            if tipe == "uuid":
                print(f"OK   {tabel}.{kolom}: tipe `uuid`")
            else:
                gagal.append(f"{tabel}.{kolom}: tipe `{tipe}`, seharusnya `uuid` (V236)")

        # 2. baris yatim
        for tabel, kolom in PASANGAN:
            n = await conn.fetchval(
                f"SELECT count(*) FROM {tabel} "  # noqa: S608 - nama tabel dari konstanta
                f"WHERE {kolom} IS NOT NULL "
                f"  AND {kolom} NOT IN (SELECT id FROM chat_sessions)"
            )
            if n == 0:
                print(f"OK   {tabel}.{kolom}: nol baris yatim")
            else:
                gagal.append(f"{tabel}.{kolom}: {n} baris menunjuk sesi yang tak ada")

        if gagal:
            print("\nGAGAL:", file=sys.stderr)
            for g in gagal:
                print("  - " + g, file=sys.stderr)
            return 1
        print("\nOK: tipe terjaga dan tak ada baris yatim.")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(utama("--kontrol" in sys.argv)))
