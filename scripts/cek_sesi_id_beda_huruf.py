#!/usr/bin/env python3
"""Penegak: id sesi chat yang disimpan sebagai TEKS harus sama huruf dengan chat_sessions.id.

KENAPA BERKAS INI ADA
3 Sep 2026. Saat memangkas sesi chat (sisakan 30/tenant), 112 baris
`pending_actions` dan 42 `chat_workflow_state` terbaca sebagai YATIM. Ternyata
153 dari 154 itu BUKAN yatim: induknya hidup, hanya saja id-nya tersimpan
dengan huruf heks KAPITAL sedangkan `chat_sessions.id` bertipe `uuid` yang
selalu dinormalisasi Postgres menjadi huruf kecil.

    chat_sessions.id            uuid  -> 'a1b2...'  (SELALU huruf kecil)
    pending_actions.conversation_id   text  -> 'A1B2...'  (apa adanya dari klien)
    chat_workflow_state.chat_session_id text -> idem

Satu identitas yang sama disimpan dalam dua tipe: yang satu membakukan, yang
satu tidak. Akibatnya `WHERE conversation_id = $1` MELESET secara diam-diam,
dan setiap JOIN antar keduanya menjatuhkan baris tanpa galat.

Sumbernya terukur: 111 baris, satu pengguna, satu tenant, 29-30 Agt 2026 —
jendela tertutup, konsisten dengan klien yang membangkitkan UUID huruf besar
(mis. `UUID().uuidString` di Swift).

KENAPA GERBANG, BUKAN TAMBALAN
Gerbang verifikasiku sendiri saat penghapusan memakai perbandingan persis, jadi
ia MUSTAHIL merah atas keadaan ini — ia melaporkan "yatim 0" justru karena
barisnya sudah tersapu. Penegak ini dibuat supaya keadaan itu bisa MERAH.

Pakai:  python3 scripts/cek_sesi_id_beda_huruf.py            # gerbang
        python3 scripts/cek_sesi_id_beda_huruf.py --kontrol  # bukti bisa MERAH
"""

import asyncio
import os
import sys

import asyncpg

# (tabel, kolom teks yang menyimpan id sesi)
PASANGAN = [
    ("pending_actions", "conversation_id"),
    ("chat_workflow_state", "chat_session_id"),
]

SQL = """
SELECT
  count(*) FILTER (WHERE {k} <> lower({k}))                       AS beda_huruf,
  count(*) FILTER (WHERE lower({k}) NOT IN
                         (SELECT lower(id::text) FROM chat_sessions)) AS yatim_sungguhan
FROM {t}
WHERE {k} IS NOT NULL AND {k} <> ''
"""


async def utama(kontrol: bool) -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("GAGAL: DATABASE_URL tidak diset.", file=sys.stderr)
        return 2

    conn = await asyncpg.connect(dsn)
    try:
        if kontrol:
            # Kontrol MERAH: tanam satu baris berhuruf kapital, buktikan gerbang
            # menangkapnya, lalu batalkan. Tanpa ini "hijau" tak membuktikan apa pun.
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
                await conn.execute(
                    "INSERT INTO chat_workflow_state "
                    "(chat_session_id, tenant_id, user_id) VALUES ($1, $2, $3)",
                    induk["id"].upper(),
                    induk["tenant_id"],
                    induk["user_id"],
                )
                baris = await conn.fetchrow(
                    SQL.format(t="chat_workflow_state", k="chat_session_id")
                )
                merah = baris["beda_huruf"] > 0
                print(
                    "KONTROL: baris huruf-kapital ditanam -> beda_huruf="
                    f"{baris['beda_huruf']} ({'MERAH, gerbang bekerja' if merah else 'HIJAU -- GERBANG RUSAK'})"
                )
                return 0 if merah else 1
            finally:
                await tx.rollback()

        total = 0
        for tabel, kolom in PASANGAN:
            baris = await conn.fetchrow(SQL.format(t=tabel, k=kolom))
            beda, yatim = baris["beda_huruf"], baris["yatim_sungguhan"]
            total += beda + yatim
            tanda = "OK " if (beda == 0 and yatim == 0) else "GAGAL"
            print(f"{tanda} {tabel}.{kolom}: beda_huruf={beda} yatim_sungguhan={yatim}")

        if total:
            print(
                "\nGAGAL: id sesi tersimpan dengan huruf yang tak dibakukan, atau "
                "menunjuk sesi yang tak ada. Keduanya membuat pencocokan MELESET "
                "TANPA GALAT.",
                file=sys.stderr,
            )
            return 1
        print("\nOK: semua id sesi berbentuk baku dan punya induk.")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(utama("--kontrol" in sys.argv)))
