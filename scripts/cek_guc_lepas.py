#!/usr/bin/env python3
"""Pemeriksa GUC lepas — menahan pertumbuhan penyetel `app.*` yang MATI.

═══════════════════════════════════════════════════════════════════════════
KENAPA BERKAS INI ADA
═══════════════════════════════════════════════════════════════════════════
`SET LOCAL app.tenant_id = …` dan `set_config('app.tenant_id', …, true)` HANYA
berlaku di dalam sebuah transaksi. asyncpg mengirim setiap `execute()` sebagai
pesan terpisah, jadi tiap statement menjadi transaksi implisitnya SENDIRI —
GUC-nya mati sebelum query berikutnya berjalan.

Terukur 2026-09-03, dua statement terpisah pada satu koneksi:
    SET LOCAL app.tenant_id = 'x'   -> WARNING: SET LOCAL can only be used in
                                       transaction blocks
    SELECT current_setting(...)     -> KOSONG

    SELECT set_config('app.tenant_id','x',true)  -> mengembalikan 'x'
                                                    (TAMPAK BERHASIL, nol warning)
    SELECT current_setting(...)                  -> KOSONG

Bentuk `set_config` LEBIH BERBAHAYA: ia mengembalikan nilainya sehingga tampak
sukses, dan tidak mencetak peringatan apa pun. Gagalnya HENING.

⚠️ LAW 34 — INI ILUSI PERLINDUNGAN, BUKAN SEKADAR KODE MATI.
Pembaca berikutnya melihat `SET LOCAL app.tenant_id` dan menyimpulkan "isolasi
tenant dijaga di sini". Tidak. Baris itu tak melakukan apa pun. Klaim tentang
PERLINDUNGAN harus dibaca sebagai hipotesis sampai diukur — dan yang ini,
ketika diukur, ternyata kosong.

⚠️ JANGAN "PERBAIKI" DENGAN `set_config(..., false)`.
Terukur: is_local=false membuat nilainya BERTAHAN DI SESI. Karena koneksi
dipakai bergantian lewat connection pool, tenant_id milik satu permintaan akan
BOCOR ke permintaan berikutnya. Untuk GUC bernama tenant itu bahaya isolasi,
bukan perbaikan. Satu-satunya bentuk yang benar:

    async with conn.transaction():
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", tenant_id
        )
        ...query yang membutuhkannya...

(`set_config` dengan parameter, bukan f-string: nilainya tak masuk ke teks SQL.)

═══════════════════════════════════════════════════════════════════════════
KENAPA INI BASELINE, BUKAN NOL
═══════════════════════════════════════════════════════════════════════════
Per 2026-09-03 ada 316 situs mati (92 `SET LOCAL` lepas + 224 `set_config`
lepas) tersebar di 50+ berkas. Diukur, NOL di antaranya berakibat hari ini:
  - NOL trigger membaca `app.tenant_id` (pg_trigger x pg_proc = 0 baris);
  - 202 policy RLS memakainya, tetapi gateway menyambung sebagai `postgres`
    yang `rolbypassrls = t` DAN `rolsuper = t`, jadi policy tak pernah
    dievaluasi (Law 24, kini dengan angka);
  - fungsi DB yang dipanggil menyetel GUC-nya SENDIRI dari parameter.
Menyunting 316 situs demi nol perubahan perilaku adalah risiko tanpa imbalan.
Maka: jumlahnya DIBEKUKAN, tidak dipaksa nol. Bersih-bersih dikerjakan
per-modul saat modulnya memang sedang disentuh.

Pemakaian:
    python3 scripts/cek_guc_lepas.py            # periksa terhadap baseline
    python3 scripts/cek_guc_lepas.py --daftar   # cetak semua situs
    python3 scripts/cek_guc_lepas.py --baseline # cetak angka hari ini
Keluar 1 bila jumlah MELEBIHI baseline. Keluar 0 bila sama atau berkurang
(dan menganjurkan menurunkan baseline bila berkurang).
"""
from __future__ import annotations

import argparse
import os
import re
import sys

# Dibekukan 2026-09-03. Turunkan bila situs mati benar-benar dibersihkan.
#
# Angka ini diukur OLEH SKRIP INI SENDIRI, bukan disalin dari laporan lain.
# Ia memindai SELURUH keluarga `app.*`, jadi lebih luas daripada angka 316 yang
# pernah dilaporkan untuk `app.tenant_id` saja. Selisihnya adalah 10 situs
# `app.current_tenant_id` / `app.bypass_rls` di lima berkas
# `*/prisma_rls_extension.py` pada services/ (accounting, inventory, reporting,
# rule_engine, transaction).
#
# Catatan terpisah untuk kelima berkas itu: mereka MENYISIPKAN tenant_id ke
# dalam teks SQL lewat f-string (`f"SELECT set_config('app.current_tenant_id',
# '{self.tenant_id}', TRUE)"`). Selain mati di luar transaksi, itu juga
# permukaan injeksi. TIDAK diperbaiki di tiket ini — dicatat supaya tak hilang.
BASELINE = 325

AKAR_BAWAAN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")

PAT_TXN = re.compile(r"^\s*async with .*\.transaction\(\)")
PAT_SET = re.compile(
    r"SET\s+LOCAL\s+app\.[a-z_]+|set_config\(\s*['\"]app\.[a-z_]+", re.IGNORECASE
)
PAT_BACA = re.compile(r"current_setting\(\s*['\"]app\.", re.IGNORECASE)


def pindai(akar: str) -> list[tuple[str, int, str]]:
    """Kembalikan daftar (berkas, baris, potongan) penyetel GUC DI LUAR transaksi."""
    temuan: list[tuple[str, int, str]] = []
    for dirpath, dirnames, filenames in os.walk(akar):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "node_modules")]
        for fn in filenames:
            if not fn.endswith(".py") or ".bak" in fn:
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, encoding="utf-8") as fh:
                    lines = fh.read().splitlines()
            except OSError:
                continue

            # Tumpukan indentasi blok `async with ...transaction():` yang terbuka.
            txn: list[int] = []
            for i, line in enumerate(lines, 1):
                if not line.strip():
                    continue
                indent = len(line) - len(line.lstrip())
                while txn and indent <= txn[-1]:
                    txn.pop()
                if PAT_TXN.match(line):
                    txn.append(indent)
                    continue
                if not PAT_SET.search(line):
                    continue
                if PAT_BACA.search(line) and "set_config" not in line.lower():
                    continue  # hanya MEMBACA, bukan menyetel
                if not txn:
                    temuan.append(
                        (os.path.relpath(path, akar), i, line.strip()[:100])
                    )
    return temuan


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--akar", default=AKAR_BAWAAN, help="direktori yang dipindai")
    p.add_argument("--daftar", action="store_true", help="cetak semua situs")
    p.add_argument("--baseline", action="store_true", help="cetak jumlah saja")
    a = p.parse_args()

    akar = os.path.abspath(a.akar)
    if not os.path.isdir(akar):
        print(f"GAGAL: direktori tidak ada: {akar}", file=sys.stderr)
        return 2

    temuan = pindai(akar)
    n = len(temuan)

    if a.baseline:
        print(n)
        return 0

    if a.daftar:
        for berkas, baris, potongan in sorted(temuan):
            print(f"{berkas}:{baris}: {potongan}")
        print(f"\nTOTAL: {n}")
        return 0

    if n > BASELINE:
        print(
            f"GAGAL: penyetel GUC di luar transaksi = {n}, melebihi baseline "
            f"{BASELINE} (+{n - BASELINE}).",
            file=sys.stderr,
        )
        print(
            "\n`SET LOCAL app.*` dan `set_config('app.*', …, true)` di luar blok\n"
            "transaksi TIDAK BEREFEK — statement berikutnya membaca kosong.\n"
            "Bentuk yang benar:\n\n"
            "    async with conn.transaction():\n"
            '        await conn.execute(\n'
            "            \"SELECT set_config('app.tenant_id', $1, true)\", tenant_id\n"
            "        )\n"
            "        ...query yang membutuhkannya...\n\n"
            "JANGAN memakai is_local=false: nilainya bertahan di sesi dan BOCOR\n"
            "antar-permintaan lewat connection pool.\n"
            "Jalankan `--daftar` untuk melihat situsnya.",
            file=sys.stderr,
        )
        return 1

    if n < BASELINE:
        print(
            f"OK: {n} situs (baseline {BASELINE}). BERKURANG {BASELINE - n} — "
            f"turunkan BASELINE di berkas ini menjadi {n}."
        )
        return 0

    print(f"OK: {n} situs, sama dengan baseline {BASELINE}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
