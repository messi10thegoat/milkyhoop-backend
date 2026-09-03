"""T218 — bukti penghapusan dokumen tidak boleh bisa terpangkas retensi.

V230 menutup lubang "siapa menghapus faktur" dengan menulis
`eventType='DOCUMENT_DELETED'` ke `audit_logs`, dan MENCATAT di komentar bahwa
retensi kelak wajib mengecualikan event itu. Komentar tidak menahan apa-apa
(Hukum Besi 34). Berkas ini mengubah catatan itu jadi gerbang.

DIUKUR 2026-09-03 pada basis data hidup — pemangkasan audit hari ini MUSTAHIL,
dan mustahilnya berlapis TIGA, masing-masing sendirian sudah cukup:

  1. TRIGGER. `audit_logs` punya tiga trigger AKTIF yang me-RAISE EXCEPTION
     pada UPDATE/DELETE. `DELETE FROM audit_logs ... LIMIT 1` di dalam
     BEGIN/ROLLBACK ditolak: "Audit logs are immutable. Cannot UPDATE or
     DELETE." (prevent_audit_mutation).
  2. FUNGSINYA TIDAK ADA. `cleanup_audit_logs` dideklarasikan V057 tetapi NOL
     baris di `pg_proc` pada basis data hidup.
  3. KOLOMNYA TIDAK ADA. Andai fungsinya dipasang ulang, ia menyaring
     `category` dan `event_time` — DUA-DUANYA bukan kolom `audit_logs`
     (audit_logs memakai `eventType` dan `createdAt`). 0 dari 2 kolom hadir.

Plus: `audit_retention_policies` berisi 0 baris, jadi tak ada kebijakan yang
bisa memangkas apa pun.

Karena itu tiket "kecualikan DOCUMENT_DELETED di kode pemangkas" TIDAK BISA
dikerjakan sebagai perubahan kode: jalur pemangkasnya tidak ada. Yang BISA
dikerjakan, dan itulah isi berkas ini, adalah gerbang yang MERAH begitu ada
orang membangun jalur pemangkas TANPA pengecualian itu.
"""

import re
from pathlib import Path

import pytest

_GW = Path(__file__).resolve().parents[2]          # backend/api_gateway
_MIG = _GW.parent / "migrations"                    # backend/migrations

# Pola "memangkas audit_logs": DELETE yang menyasar audit_logs.
_HAPUS_AUDIT = re.compile(r"DELETE\s+FROM\s+audit_logs", re.IGNORECASE)

# Skrip reset tenant memang menghapus SEMUA data satu tenant (bukan retensi).
# Ia hidup di backend/scripts/, di luar lingkup pindaian ini.
#
# PENGECUALIAN EKSPLISIT, dengan alasan yang bisa diperiksa ulang:
# `V057__audit_trail.sql` memuat `cleanup_audit_logs()` — pemangkas WARISAN yang
# TIDAK BISA JALAN pada tabel hidup:
#   - fungsinya NOL baris di `pg_proc` (tak pernah terpasang), dan
#   - ia menyaring `category` + `event_time`, sedangkan audit_logs HIDUP memakai
#     `eventType` + `createdAt` (diukur lewat information_schema: 0 dari 2 kolom
#     itu hadir), karena tabel hidup BUKAN buatan V057 — lihat V216 yang
#     menyatakan "audit_logs itself already exists and is not touched here".
# Ia dikecualikan supaya gerbang ini tetap tajam untuk pemangkas BARU. Kalau
# seseorang menghidupkan fungsi ini, ia harus menulis ulang kolomnya — dan saat
# itulah pengecualian DOCUMENT_DELETED wajib ikut ditulis.
_DIKECUALIKAN = {("V057__audit_trail.sql", "cleanup_audit_logs")}

_SUMBER = [
    *sorted((_GW / "app").rglob("*.py")),
    *sorted(_MIG.glob("*.sql")),
]


def _potongan(teks: str, posisi: int, radius: int = 1500) -> str:
    return teks[max(0, posisi - radius) : posisi + radius]


def test_pemangkas_audit_wajib_mengecualikan_document_deleted():
    """Gerbang MASA DEPAN: setiap DELETE FROM audit_logs harus menyebut
    DOCUMENT_DELETED di sekitarnya (yakni mengecualikannya).

    Hari ini nol pemangkas -> tes lulus dengan hampa. Ia baru bergigi saat
    seseorang menambahkan pemangkas; kalau pengecualiannya lupa, tes MERAH
    sebelum kode itu sempat mendarat.
    """
    pelanggar = []
    for berkas in _SUMBER:
        try:
            teks = berkas.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in _HAPUS_AUDIT.finditer(teks):
            sekitar = _potongan(teks, m.start())
            if any(
                berkas.name == nama and fungsi in sekitar
                for nama, fungsi in _DIKECUALIKAN
            ):
                continue
            if "DOCUMENT_DELETED" not in sekitar:
                pelanggar.append(f"{berkas.name}:{teks[: m.start()].count(chr(10)) + 1}")

    assert not pelanggar, (
        "Ada DELETE FROM audit_logs TANPA pengecualian DOCUMENT_DELETED di "
        "sekitarnya: " + ", ".join(pelanggar) + ". Bukti penghapusan dokumen "
        "(V230) akan ikut terpangkas. Kecualikan eventType='DOCUMENT_DELETED'."
    )


def test_kontrol_merah_pemindai_benar_benar_bisa_menemukan():
    """Alat ukurnya wajib terbukti BISA gagal (verification-tool-must-be-able-
    to-fail). Tanpa ini, 'nol pelanggar' di atas tak bisa dibedakan dari
    'pemindai rusak'."""
    buruk = "DELETE FROM audit_logs WHERE createdAt < now() - interval '1 day'"
    baik = (
        "DELETE FROM audit_logs WHERE createdAt < now() - interval '1 day' "
        "AND \"eventType\" <> 'DOCUMENT_DELETED'"
    )
    assert _HAPUS_AUDIT.search(buruk), "pemindai gagal menemukan DELETE yang jelas ada"
    assert "DOCUMENT_DELETED" not in buruk, "contoh buruk seharusnya tanpa pengecualian"
    assert _HAPUS_AUDIT.search(baik) and "DOCUMENT_DELETED" in baik


def test_v230_menulis_document_deleted_dan_mencatat_kewajiban_retensi():
    """V230 harus tetap menjadi sumber event itu, dan tetap menyatakan
    kewajiban retensinya (supaya alasannya tidak hilang saat berkas disunting)."""
    v230 = _MIG / "V230__audit_document_deletion.sql"
    assert v230.exists(), "V230 hilang — jejak penghapusan dokumen ikut hilang"
    isi = v230.read_text(encoding="utf-8")
    assert "'DOCUMENT_DELETED'" in isi
    assert "DOCUMENT_DELETED' WAJIB DIKECUALIKAN" in isi.replace("\n-- ", " ")


def test_audit_logs_immutable_by_trigger_di_ddl():
    """Lapis 1: keabadian audit_logs ditegakkan DATABASE, bukan konvensi."""
    v106 = (_MIG / "V106__action_mode_foundation.sql").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert "prevent_audit_mutation" in v106
    assert re.search(r"RAISE\s+EXCEPTION", v106, re.IGNORECASE), (
        "prevent_audit_mutation harus MELEMPAR, bukan sekadar mengembalikan NULL"
    )


def test_cleanup_audit_logs_v057_memakai_kolom_yang_tidak_ada():
    """Lapis 3: fungsi retensi warisan V057 menyaring `category` dan
    `event_time`, sedangkan audit_logs memakai `eventType`/`createdAt`.

    Didokumentasikan supaya siapa pun yang hendak 'menghidupkan kembali'
    fungsi itu tahu ia tidak akan jalan apa adanya.
    """
    v057 = (_MIG / "V057__audit_trail.sql").read_text(encoding="utf-8", errors="ignore")
    assert "cleanup_audit_logs" in v057
    blok = v057[v057.index("cleanup_audit_logs") :]
    assert "category" in blok and "event_time" in blok, (
        "Bentuk fungsi berubah — ukur ulang kolomnya sebelum mempercayai tes ini."
    )


def test_v057_mendeklarasikan_bentuk_audit_logs_yang_BUKAN_bentuk_hidup():
    """Drift terukur: ada DUA definisi `audit_logs` yang tidak cocok.

    V057 mendeklarasikan bentuk snake_case (`event_time`, `category`,
    `user_id`), sedangkan tabel HIDUP memakai camelCase (`eventType`,
    `createdAt`, `userId`) — itulah bentuk yang ditulisi V230 dan dibaca
    `routers/audit.py`. V216 menjelaskan sebabnya: V057 gugur setelah
    CREATE TABLE, dan tabel hidup sudah ada lebih dulu, jadi "audit_logs
    itself ... is not touched here".

    Konsekuensi praktis, dan inilah alasan tes ini ada: `cleanup_audit_logs`
    warisan V057 TIDAK AKAN JALAN di tabel hidup. Siapa pun yang hendak
    menghidupkan retensi harus menulis ulang fungsinya dari nol — dan di situ
    pengecualian DOCUMENT_DELETED wajib ikut, yang dijaga tes pertama berkas ini.
    """
    v057 = (_MIG / "V057__audit_trail.sql").read_text(encoding="utf-8", errors="ignore")
    m = re.search(
        r"CREATE TABLE[^;]*?audit_logs\s*\((.*?)\);", v057, re.IGNORECASE | re.DOTALL
    )
    assert m, "DDL audit_logs V057 tidak ketemu"
    ddl_v057 = m.group(1)
    # Bentuk V057 (snake_case) — ADA di migrasi, TIDAK ada di tabel hidup.
    assert re.search(r"^\s*event_time\b", ddl_v057, re.IGNORECASE | re.MULTILINE)
    assert re.search(r"^\s*category\b", ddl_v057, re.IGNORECASE | re.MULTILINE)
    # Bentuk HIDUP (camelCase) — dipakai V230, TIDAK ada di DDL V057.
    v230 = (_MIG / "V230__audit_document_deletion.sql").read_text(encoding="utf-8")
    assert '"eventType"' in v230, "V230 harus menulis kolom camelCase tabel hidup"
    assert not re.search(r"^\s*\"?eventType\"?\b", ddl_v057, re.MULTILINE), (
        "DDL V057 ternyata memuat eventType — drift-nya hilang, ukur ulang premis."
    )
