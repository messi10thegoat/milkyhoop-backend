#!/bin/bash
# ==================================================
# MilkyHoop Daily Accounting Health Check — v2 (P5)
# ==================================================
# 12 checks across 4 categories:
#   Ledger Integrity: Check 1 (balance), 2 (hash), 5 (orphans), 6 (sequence)
#   Sync Layers:      Check 3 (bank gap), 4 (inventory qty), 7 (AP), 8 (AR)
#   Value Integrity:  Check 9 (inv value), 10 (COGS), 11 (opening balance)
#   Anomaly:          Check 12 (negative cash/bank)
#
# Severity: CRITICAL (exit 2), HIGH (exit 1), WARNING (exit 0)
# Per-tenant loop, skip cafeanna
# ==================================================
# Usage:  ./accounting_health_check.sh
# Cron:   0 6 * * * /root/milkyhoop-dev/monitoring/accounting_health_check.sh >> /var/log/milkyhoop/accounting_health.log 2>&1
# ==================================================

set -uo pipefail

# ---- Configuration ----
CONTAINER="milkyhoop-dev-postgres-1"
DB_NAME="milkydb"
DB_USER="postgres"
LOG_DIR="/var/log/milkyhoop"
WEBHOOK_URL="${DISCORD_WEBHOOK_URL:-}"
DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
DATE_SHORT=$(date -u +"%Y-%m-%d %H:%M UTC")
LOG_FILE="$LOG_DIR/health_check_$(date +%Y%m%d_%H%M%S).log"
SKIP_TENANTS="cafeanna"

mkdir -p "$LOG_DIR"

# Log retention: 30 days
find "$LOG_DIR" -name "health_check_*.log" -mtime +30 -delete 2>/dev/null || true

# Counters
CRITICAL_COUNT=0
HIGH_COUNT=0
WARNING_COUNT=0
TOTAL_CHECKS=0
PASS_COUNT=0
# Law 33 (2026-09-13): BROKEN = perkakasnya tak bisa jalan.
# BUKAN lulus, BUKAN kegagalan data. SASARANNYA NOL -- kategori ini SEMENTARA.
# Ketiga penghuni awalnya ada di sini karena fungsi DB yang dirujuk TIDAK ADA
# (compute_ap_adjustments, compute_inventory_adjustments) atau SQL-nya cacat
# sejak lahir (check_13). Kalau kelak ada BROKEN keempat yang bertahan
# berbulan-bulan, UMUR di laporan harian yang akan menelanjanginya.
BROKEN_COUNT=0
BROKEN_EVENTS=0
BROKEN_NAMES=""

# Per-tenant Discord message accumulator
DISCORD_BODY=""

# ---- Helpers ----

log() {
    echo "$DATE | $1"
    echo "$DATE | $1" >> "$LOG_FILE"
}

# --- Law 33: perkakas rusak vs data rusak ---
is_broken() {
    case "$1" in
        *__GAGAL__*) return 0 ;;
        *) return 1 ;;
    esac
}

# Tanggal "rusak sejak" DIUKUR dari git (commit yang memperkenalkannya),
# bukan dikarang: check_7 & check_9 lahir 4c7cafb3, check_13 lahir 0bdebe86.
# Ketiganya rusak SEJAK LAHIR -- fungsinya tak pernah ada / SQL-nya cacat.
broken_since() {
    case "$1" in
        # 7/9/13 dihidupkan V242 (2026-09-13); tanggal lahir-rusak mereka
        # tak berlaku lagi -- kerusakan baru dilabeli "sejak jalan ini".
        *)  echo "" ;;
    esac
}

note_broken() {
    local num="$1" name="$2" detail="$3"
    local since age label
    since=$(broken_since "$num")
    if [ -n "$since" ]; then
        age=$(( ( $(date -u +%s) - $(date -u -d "$since" +%s 2>/dev/null || echo 0) ) / 86400 ))
        label="[$num] $name (rusak sejak $since, $age hari)"
    else
        label="[$num] $name (rusak sejak jalan ini)"
    fi
    # Cacah DIPISAH dgn sengaja: BROKEN_COUNT = pemeriksaan BERBEDA,
    # BROKEN_EVENTS = kejadian (satu pemeriksaan rusak muncul sekali per tenant).
    # Versi pertama memakai satu cacah untuk keduanya, sehingga Discord berbunyi
    # "6 pemeriksaan RUSAK" sambil menyebut TIGA nama -- cacah yang sah dipasang
    # ke pertanyaan yang lain, persis kelas yang berulang kali menggigit kami.
    BROKEN_EVENTS=$((BROKEN_EVENTS + 1))
    case "$BROKEN_NAMES" in
        *"$label"*) : ;;
        *) BROKEN_COUNT=$((BROKEN_COUNT + 1))
           BROKEN_NAMES="${BROKEN_NAMES}${BROKEN_NAMES:+, }${label}" ;;
    esac
    log "  BROKEN $label: perkakas tak bisa dijalankan — $detail"
}

detail() {
    # Detail only goes to log file, not stdout
    echo "  $1" >> "$LOG_FILE"
}

# Law 33 (2026-09-13): stderr TIDAK LAGI dibuang.
# Sebelumnya `2>/dev/null` + tujuh situs `|| [ -z "$x" ]` berarti SETIAP
# kegagalan kueri menjadi PASS. Terbukti: check_7/check_9 memanggil fungsi yang
# TIDAK ADA (compute_ap_adjustments, compute_inventory_adjustments) dan
# check_13 ber-SQL cacat sejak lahir -- ketiganya dihitung "Passed" berbulan.
# Sekarang galat memancarkan __GAGAL__ (penanda eksplisit, bisa dibedakan dari
# "kosong yang sah") dan pesan galatnya ikut tercatat di log.
psql_cmd() {
    local __out __rc __err
    # stderr ke berkas TERPISAH, bukan 2>&1: kalau digabung, satu NOTICE pada
    # kueri yang SUKSES akan mencemari nilai kembaliannya -- menukar cacat
    # "diam-lulus" dengan cacat "nilai salah diam-diam". Verdikt dari rc saja.
    __err=$(mktemp)
    __out=$(docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" \
                   -v ON_ERROR_STOP=1 -t -c "$1" 2>"$__err")
    __rc=$?
    if [ "$__rc" -ne 0 ]; then
        echo "[SQL GAGAL rc=$__rc] $(head -2 "$__err" | tr '\n' ' ')" >> "$LOG_FILE" 2>/dev/null || true
        rm -f "$__err"
        echo "__GAGAL__"
        return 0
    fi
    rm -f "$__err"
    echo "$__out" | tr -d ' '
}

psql_lines() {
    # Returns multi-line results (trimmed, no empty lines)
    docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -t -A -c "$1" 2>/dev/null | grep -v '^$'
}

send_discord() {
    local message="$1"
    if [ -n "$WEBHOOK_URL" ]; then
        # Escape special chars for JSON
        local escaped
        escaped=$(echo "$message" | sed 's/\\/\\\\/g; s/"/\\"/g' | sed ':a;N;$!ba;s/\n/\\n/g')
        curl -s -H "Content-Type: application/json" \
            -d "{\"content\":\"$escaped\"}" \
            "$WEBHOOK_URL" > /dev/null 2>&1 || true
    fi
}

# ---- Per-Tenant Check Functions ----
# Each function sets result in: CHK_PASS (0/1), CHK_DETAIL (string)

check_1_journal_balance() {
    local tenant="$1"
    local count
    count=$(psql_cmd "
        SELECT COUNT(*) FROM journal_entries
        WHERE status = 'POSTED' AND total_debit != total_credit
          AND tenant_id = '$tenant';
    ")
    if [ "$count" = "0" ]; then
        CHK_PASS=1
        CHK_DETAIL=""
    else
        CHK_PASS=0
        CHK_DETAIL="$count unbalanced journals"
        detail "[CHECK 1] $tenant: $CHK_DETAIL"
    fi
}

# V241 2026-09-13 — Check 2 membaca VERDIKT, bukan jumlah mentah.
# Bentuknya meniru check_14 / check_15: verify_chain_integrity_all() +
# journal_chain_exemptions, verdikt PASS / PASS_EXEMPT / FAIL_NON_EXEMPT /
# FAIL_DRIFT_CHANGED.
#
# KENAPA: 2 tautan pecah yang SUDAH ADA (seq 109 & 393) membuat pemeriksaan ini
# CRITICAL tiap pagi. Menyembuhkannya = menyunting jurnal POSTED (Law 2/3) =
# putusan pemilik. Alarm yang SELALU merah akan diabaikan, dan alarm yang
# diabaikan lebih buruk daripada alarm yang tak ada karena ia TERLIHAT seperti
# penjagaan. Garis dasarnya MENGAMPUNI yang lama, TIDAK menghapusnya.
#
# Terbukti MERAH SEBELUM dipasang (rollback, nol baris menetap):
#   pecahan ke-12 simulasi   2 -> 3   FAIL_DRIFT_CHANGED
#   jumlah sama, sidik jari beda      FAIL_DRIFT_CHANGED
#   sidik jari dipulihkan             PASS_EXEMPT (merahnya memang dari situ)
# Sidik jari mengunci IDENTITAS: garis dasar skalar akan MELOLOSKAN penggantian
# (satu pecahan lama sembuh + satu baru muncul, jumlah tetap 2).
check_2_hash_chain() {
    local tenant="$1"
    local posted
    posted=$(psql_cmd "SELECT COUNT(*) FROM journal_entries WHERE status = 'POSTED' AND tenant_id = '$tenant';")
    if [ "$posted" = "0" ]; then
        CHK_PASS=1
        CHK_DETAIL=""
        return
    fi
    local verdict
    verdict=$(psql_cmd "SELECT verdict FROM verify_chain_integrity_all() WHERE tenant_id = '$tenant';")
    if [ "$verdict" = "PASS" ] || [ "$verdict" = "PASS_EXEMPT" ]; then
        CHK_PASS=1
        CHK_DETAIL=""
    else
        local broken
        broken=$(psql_cmd "SELECT broken_count FROM verify_chain_integrity_all() WHERE tenant_id = '$tenant';")
        CHK_PASS=0
        CHK_DETAIL="hash chain $verdict ($broken broken chain links)"
        detail "[CHECK 2] $tenant: $CHK_DETAIL"
    fi
}

check_3_bank_sync() {
    # V243 (2026-09-13): R9 lama menaruh je.status='POSTED' di dalam ON sebuah
    # LEFT JOIN -> saringan status DEKORATIF, jurnal berstatus APA PUN ikut
    # dijumlahkan. Terbukti lewat eksekusi: jurnal DRAFT + btx terikat membuat
    # gap nyata, R9 lama tetap 0. Kini hanya jurnal POSTED (syarat di WHERE),
    # anggota + patok identitas di hc_verdict('bank_sync'); 16 jurnal VOID lama
    # dipatok (TIKET-r9-saringan-status-dekoratif-20260913).
    local tenant="$1"
    local row verdict drift cnt
    row=$(psql_cmd "SELECT verdict||'|'||COALESCE(drift::text,'')||'|'||member_count FROM hc_verdict('bank_sync', '$tenant');")
    verdict="${row%%|*}"
    if [ "$verdict" = "PASS" ] || [ "$verdict" = "PASS_EXEMPT" ]; then
        CHK_PASS=1
        CHK_DETAIL=""
    elif [ "$row" = "__GAGAL__" ]; then
        CHK_PASS=0
        CHK_DETAIL="__GAGAL__"
    else
        drift=$(echo "$row" | cut -d'|' -f2); cnt=$(echo "$row" | cut -d'|' -f3)
        CHK_PASS=0
        CHK_DETAIL="bank sync $verdict gap=$drift anggota=$cnt"
        detail "[CHECK 3] $tenant: $CHK_DETAIL"
        local details
        details=$(psql_lines "SELECT member_key || ' ' || amount FROM hc_bank_sync_members('$tenant') ORDER BY member_key LIMIT 20;")
        echo "$details" | while read -r line; do detail "  $line"; done
    fi
}

check_4_inventory_qty() {
    # TIGA LENGAN, dan lengan B/C ADA KARENA LENGAN A BUTA.
    #
    # Versi lama HANYA punya lengan A, dan ia menyaring
    # `WHERE warehouse_id IS NOT NULL`. Penyaring itu membuang PERSIS baris
    # yang menyebabkan cacat P1 tanggal 11 Sep 2026: 11 baris
    # `inventory_ledger` ber-`warehouse_id` NULL (10 PRODUCTION_OUTPUT + 1
    # OPENING_BALANCE) membawa 130 unit di 3 item. Trigger
    # `trg_update_warehouse_stock` mencocokkan `WHERE warehouse_id =
    # NEW.warehouse_id`; NULL tak pernah cocok, jadi barang hasil produksi tak
    # pernah masuk cache gudang. Layar (yang menjumlahkan ledger tanpa peduli
    # gudang) menjanjikan 126 unit; `/fulfill` (yang membaca cache) menolak 409.
    #
    # Dan Check 4 lama HIJAU SEMPURNA lintas SELURUH tenant sepanjang itu.
    # Bukan karena datanya bersih -- karena penyaringnya membuang buktinya.
    # Sebuah penyaring di dalam invariant adalah ASUMSI yang tak pernah diuji;
    # di sini asumsinya ("setiap gerakan punya gudang") justru yang gagal.
    #
    # Lengan A juga berangkat DARI `warehouse_stock`, jadi pasangan
    # (produk, gudang) yang ada di ledger tapi belum punya baris cache sama
    # sekali tak pernah diperiksa. Itu lengan C.
    #
    # BUKTI IA BISA MERAH -- dua angka berdampingan, data yang SAMA, hari yang
    # SAMA (11 Sep 2026), diukur SEBELUM satu baris pun diperbaiki:
    #
    #     lengan A (versi lama)   0 baris  di SELURUH tenant   -> hijau sempurna
    #     lengan B (versi baru)   3 produk di kaos-biru        -> MERAH
    #                            (11 baris; yang dihitung SALDO-nya, bukan barisnya)
    #
    # Itu bukan argumen bahwa guard lama buta; itu pengukurannya. Simpan kedua
    # angka ini di sini -- kalau kelak ada yang memperdebatkan apakah penyaring
    # `IS NOT NULL` itu berbahaya, jawabannya tertulis di baris atas.
    #
    # LENGAN B AKAN TETAP MERAH sampai tiket koreksi 130 unit selesai. Merah
    # yang DISENGAJA dengan sebab tertulis berbeda dari merah yang diabaikan;
    # pesan detailnya menyebutkannya supaya tak ada yang membungkamnya.
    local tenant="$1"
    local gaps_a gaps_b gaps_c
    gaps_a=$(psql_cmd "
        WITH ledger_balance AS (
            SELECT product_id, warehouse_id,
                COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) AS computed_qty
            FROM inventory_ledger
            WHERE warehouse_id IS NOT NULL AND tenant_id = '$tenant'
            GROUP BY product_id, warehouse_id
        )
        SELECT COUNT(*) FROM warehouse_stock ws
        LEFT JOIN ledger_balance lb
            ON lb.product_id = ws.item_id AND lb.warehouse_id = ws.warehouse_id
        WHERE ws.tenant_id = '$tenant'
          AND ws.quantity != COALESCE(lb.computed_qty, 0);
    ")
    # LENGAN B -- STOK tanpa lokasi. Stok yang tak bisa dikirim.
    #
    # ⚠️ Versi pertama lengan ini MENGHITUNG BARIS ber-`warehouse_id` NULL, dan
    # itu SALAH UKUR: `inventory_ledger` bersifat hanya-tambah (Rule 8), jadi
    # koreksi yang sah pun MENAMBAH baris, bukan menghapusnya. Ukuran
    # berbasis-baris karena itu TAK PERNAH BISA KEMBALI NOL -- ia akan merah
    # selamanya bahkan sesudah cacatnya benar-benar diperbaiki, dan gerbang
    # yang merah selamanya adalah gerbang yang orang belajar abaikan.
    #
    # Yang benar diukur adalah SALDO BERSIH tak berlokasi per produk. Sesudah
    # relokasi yang sah (keluar dari NULL, masuk ke gudang nyata) saldo itu
    # nol, meski barisnya bertambah dua.
    #
    # ⚠️ JANGAN BINGUNG ketika `B = 0` sementara `SELECT count(*) FROM
    # inventory_ledger WHERE warehouse_id IS NULL` mengembalikan angka BESAR.
    # Kedua angka itu BENAR dan tidak bertentangan. Terukur 12 Sep 2026:
    #   14 baris ber-warehouse_id NULL ADA, tersebar di 3 produk
    #   saldo bersih tak-berlokasi tiap produk = 0.0000  -> B = 0, BENAR
    # Sebabnya tiap kaki MASUK tanpa lokasi punya kaki KELUAR pasangannya
    # (relokasi/pembalik). Barisnya tinggal selamanya -- tabel ini hanya-tambah.
    #
    # DAN LENGAN INI MASIH BERGIGI, dibuktikan dengan kontrafaktual pada data
    # nyata, bukan dinalar: kalau kaki-keluar RELOKASI_GUDANG dikeluarkan dari
    # hitungan (= keadaan sebelum koreksi 11 Sep), B kembali menjadi 3. Jadi
    # kejadian BARU tetap akan menyalakannya.
    gaps_b=$(psql_cmd "
        SELECT COUNT(*) FROM (
            SELECT product_id
            FROM inventory_ledger
            WHERE tenant_id = '$tenant' AND warehouse_id IS NULL
            GROUP BY product_id
            HAVING COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) <> 0
        ) x;
    ")
    # LENGAN C -- saldo ledger yang belum punya baris cache sama sekali.
    gaps_c=$(psql_cmd "
        WITH lb AS (
            SELECT product_id, warehouse_id,
                COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) AS computed_qty
            FROM inventory_ledger
            WHERE warehouse_id IS NOT NULL AND tenant_id = '$tenant'
            GROUP BY product_id, warehouse_id
        )
        SELECT COUNT(*) FROM lb
        LEFT JOIN warehouse_stock ws
            ON ws.item_id = lb.product_id AND ws.warehouse_id = lb.warehouse_id
           AND ws.tenant_id = '$tenant'
        WHERE ws.id IS NULL AND lb.computed_qty <> 0;
    ")
    if [ "$gaps_a" = "0" ] && [ "$gaps_b" = "0" ] && [ "$gaps_c" = "0" ]; then
        CHK_PASS=1
        CHK_DETAIL=""
    else
        CHK_PASS=0
        CHK_DETAIL="A=$gaps_a cache!=ledger, B=$gaps_b produk ber-stok tanpa gudang, C=$gaps_c saldo tanpa baris cache"
        if [ "$gaps_b" != "0" ]; then
            CHK_DETAIL="$CHK_DETAIL [lengan B: menunggu tiket 3 koreksi 130 unit lewat penyesuaian stok ber-jurnal -- merah ini DISENGAJA sampai itu selesai]"
        fi
        detail "[CHECK 4] $tenant: $CHK_DETAIL"
    fi
}

check_5_orphaned_lines() {
    # Global check — not tenant-specific (journal_lines has no tenant_id)
    local tenant="$1"
    # Only run once for first tenant
    if [ "$tenant" != "$FIRST_TENANT" ]; then
        CHK_PASS=1
        CHK_DETAIL=""
        return
    fi
    local count
    count=$(psql_cmd "
        SELECT COUNT(*) FROM journal_lines jl
        LEFT JOIN journal_entries je ON je.id = jl.journal_id
        WHERE je.id IS NULL;
    ")
    if [ "$count" = "0" ]; then
        CHK_PASS=1
        CHK_DETAIL=""
    else
        CHK_PASS=0
        CHK_DETAIL="$count orphaned journal lines (global)"
        detail "[CHECK 5] GLOBAL: $CHK_DETAIL"
    fi
}

check_6_sequence() {
    local tenant="$1"
    local dupes
    dupes=$(psql_cmd "
        SELECT COUNT(*) FROM (
            SELECT chain_sequence, COUNT(*)
            FROM journal_entries
            WHERE status = 'POSTED' AND chain_sequence IS NOT NULL
              AND tenant_id = '$tenant'
            GROUP BY chain_sequence
            HAVING COUNT(*) > 1
        ) dupes;
    ")
    if [ "$dupes" = "0" ]; then
        CHK_PASS=1
        CHK_DETAIL=""
    else
        CHK_PASS=0
        CHK_DETAIL="$dupes duplicate chain sequences"
        detail "[CHECK 6] $tenant: $CHK_DETAIL"
    fi
}

check_7_ap_invariant() {
    # V242 (2026-09-13): dulu memanggil compute_ap_adjustments yang TAK PERNAH ADA
    # -> mati sejak lahir (5 Mar). Kini GL PAYABLE efektif vs compute_ap_outstanding,
    # anggota + patok identitas di hc_verdict(). Diampuni hanya bila himpunan
    # tagihan, cacah, jumlah SAMA dan anggota MENUTUP drift.
    local tenant="$1"
    local row verdict drift cnt
    row=$(psql_cmd "SELECT verdict||'|'||COALESCE(drift::text,'')||'|'||member_count FROM hc_verdict('ap_invariant', '$tenant');")
    verdict="${row%%|*}"
    if [ "$verdict" = "PASS" ] || [ "$verdict" = "PASS_EXEMPT" ]; then
        CHK_PASS=1
        CHK_DETAIL=""
    elif [ "$row" = "__GAGAL__" ]; then
        CHK_PASS=0
        CHK_DETAIL="__GAGAL__"
    else
        drift=$(echo "$row" | cut -d'|' -f2); cnt=$(echo "$row" | cut -d'|' -f3)
        CHK_PASS=0
        CHK_DETAIL="AP $verdict drift=$drift tagihan=$cnt"
        detail "[CHECK 7] $tenant: $CHK_DETAIL"
    fi
}

# FIX_P35_ARCANON 2026-06-17 -- check_8_ar_invariant RETIRED.
# It used an ASYMMETRIC identity (GL net == outstanding + adjustments) that
# double-subtracted credit-note/deposit adjustments already netted into the
# canonical compute_ar_outstanding(), producing false positives on
# reverse-heavy tenants (golden-apparel, grapgrap, p3verify) AND missing the
# real anthonius-iwan orphan (saw drift=0 where check_14 sees 1,000,000).
# Superseded by check_14_ar_reconciliation_enforce (symmetric, per-customer,
# is_effective, exemption-aware). Scope of check_14 strictly contains check_8.

# FIX_P35_ARCANON 2026-06-17 — Layer 3: enforce-with-grandfather AR reconciliation.
# Per-customer canonical compute_ar_outstanding == GL RECEIVABLE net (is_effective,
# symmetric). Exemption store ar_reconciliation_exemptions grandfathers known drift:
#   non-exempt drift!=0      -> FAIL_NON_EXEMPT   (critical, fail loud)
#   exempt drift==baseline   -> PASS_EXEMPT       (shrinking debt, allowed)
#   exempt drift!=baseline   -> FAIL_DRIFT_CHANGED (debt moved, fail loud)
# This is the AUTHORITATIVE AR invariant. check_8 was RETIRED in this migration:
# it was asymmetric for reverse-heavy tenants (Track-alpha); this is its symmetric
# replacement (scope strictly contains check_8).
check_14_ar_reconciliation_enforce() {
    local tenant="$1"
    local verdict
    verdict=$(psql_cmd "SELECT verdict FROM verify_ar_reconciliation_all() WHERE tenant_id = '$tenant';")
    if [ "$verdict" = "PASS" ] || [ "$verdict" = "PASS_EXEMPT" ]; then
        CHK_PASS=1
        CHK_DETAIL=""
    elif [ -z "$verdict" ]; then
        # verify_ar_reconciliation_all() tak mengembalikan baris utk tenant ini.
        # KOSONG + NOL dokumen AR = tak ada yang direkonsiliasi -> PASS (bukan
        # CRITICAL palsu; dulu adhita/subbidel tanpa AR jatuh ke else = CRITICAL).
        # KOSONG + ADA dokumen AR = alatnya (tenants-CTE) tak mencakup tenant ini
        # -> BROKEN (Law 33), bukan CRITICAL.
        local ar_docs
        ar_docs=$(psql_cmd "SELECT COUNT(*) FROM sales_invoices WHERE tenant_id = '$tenant' AND status NOT IN ('draft','void');")
        if [ "${ar_docs:-0}" = "0" ]; then
            CHK_PASS=1
            CHK_DETAIL=""
        else
            CHK_PASS=0
            CHK_DETAIL="__GAGAL__ AR reconciliation verdict KOSONG padahal ada $ar_docs dokumen AR (tenants-CTE tak mencakup tenant ini)"
            detail "[CHECK 14] $tenant: $CHK_DETAIL"
        fi
    else
        local drift
        drift=$(psql_cmd "SELECT total_drift FROM verify_ar_reconciliation_all() WHERE tenant_id = '$tenant';")
        CHK_PASS=0
        CHK_DETAIL="AR reconciliation $verdict (drift=$drift)"
        detail "[CHECK 14] $tenant: $CHK_DETAIL"
    fi
}

check_9_inventory_value() {
    # V242 (2026-09-13): dulu memanggil compute_inventory_adjustments yang TAK
    # PERNAH ADA, dan suku GL-nya asimetris (membuang jurnal asli, menyimpan
    # pembalik -> artefak 3 juta). Kini akun LITERAL 1-10600 + is_effective_journal.
    # SENGAJA beda mekanisme dari check_15 (berbasis PERAN): bila keduanya TAK
    # SEPAKAT, itu dilaporkan sebagai temuan tersendiri -- pembeda antara
    # "persediaan menyimpang" dan "pemeriksaannya rusak".
    local tenant="$1"
    local row verdict drift cnt d15 tol15 selisih
    row=$(psql_cmd "SELECT verdict||'|'||COALESCE(drift::text,'')||'|'||member_count FROM hc_verdict('inventory_value', '$tenant');")
    if [ "$row" = "__GAGAL__" ]; then
        CHK_PASS=0
        CHK_DETAIL="__GAGAL__"
        return
    fi
    verdict="${row%%|*}"
    drift=$(echo "$row" | cut -d'|' -f2); cnt=$(echo "$row" | cut -d'|' -f3)

    d15=$(psql_cmd "SELECT COALESCE(drift::text,'')||'|'||COALESCE(tolerance::text,'') FROM verify_inventory_wac_reconciliation_all() WHERE tenant_id = '$tenant';")
    if [ "$d15" = "__GAGAL__" ]; then
        CHK_PASS=0
        CHK_DETAIL="__GAGAL__"
        return
    fi
    if [ -n "$d15" ]; then
        tol15="${d15#*|}"; d15="${d15%%|*}"
        # sepakat bila |drift9 - drift15| <= toleransi check_15
        selisih=$(psql_cmd "SELECT (abs(${drift:-0}::numeric - ${d15:-0}::numeric) > ${tol15:-0}::numeric)::text;")
        if [ "$selisih" = "true" ]; then
            CHK_PASS=0
            CHK_DETAIL="check_9 ($drift) dan check_15 ($d15) TAK SEPAKAT, toleransi $tol15"
            detail "[CHECK 9] $tenant: $CHK_DETAIL"
            return
        elif [ "$selisih" = "__GAGAL__" ]; then
            CHK_PASS=0
            CHK_DETAIL="__GAGAL__"
            return
        fi
    fi

    if [ "$verdict" = "PASS" ] || [ "$verdict" = "PASS_EXEMPT" ]; then
        CHK_PASS=1
        CHK_DETAIL=""
    else
        CHK_PASS=0
        CHK_DETAIL="inventory value $verdict drift=$drift anggota=$cnt"
        detail "[CHECK 9] $tenant: $CHK_DETAIL"
    fi
}

# INV_WAC_GUARD_V181 2026-06-17 — Check 15: Inventory WAC reconciliation
# (health-check level, NOT a close-time hard gate). Per-tenant: GL inventory
# CoA 1-10600 balance (journal-derived, is_effective, symmetric reversals net)
# vs Sum over products (on_hand_qty * current_WAC). Exemption store
# inventory_wac_reconciliation_exemptions grandfathers known frozen drift,
# mirroring Check 14 / verify_ar_reconciliation_all:
#   non-exempt drift>tol     -> FAIL_NON_EXEMPT   (fail loud)
#   exempt drift==baseline   -> PASS_EXEMPT       (frozen legacy, allowed)
#   exempt drift!=baseline   -> FAIL_DRIFT_CHANGED (drift moved, fail loud)
# Detects the WAC-inflation bug class (net-on-hand value WAC, milkyhoop-inventory
# Rule 3). HIGH severity (value integrity), not CRITICAL — avoids over-reject.
check_15_inventory_wac_reconciliation() {
    local tenant="$1"
    local verdict
    verdict=$(psql_cmd "SELECT verdict FROM verify_inventory_wac_reconciliation_all() WHERE tenant_id = '$tenant';")
    if [ "$verdict" = "PASS" ] || [ "$verdict" = "PASS_EXEMPT" ]; then
        CHK_PASS=1
        CHK_DETAIL=""
    else
        local drift
        drift=$(psql_cmd "SELECT drift FROM verify_inventory_wac_reconciliation_all() WHERE tenant_id = '$tenant';")
        CHK_PASS=0
        CHK_DETAIL="inventory WAC reconciliation $verdict (drift=$drift)"
        detail "[CHECK 15] $tenant: $CHK_DETAIL"
    fi
}

# DEFERRED_REV_GUARD_V182 2026-06-18 — Check 16: Deferred-revenue reconciliation
# (P4 / PSAK-72 revenue-timing). Per-tenant: canonical Sum(allocated-recognized)
# over effective posted invoices == GL net (Cr-Dr) of the per-tenant
# REVENUE_DEFERRED role-mapped account (POSTED + is_effective). Exemption store
# deferred_revenue_reconciliation_exemptions grandfathers known frozen drift,
# mirroring Check 14/15:
#   non-exempt drift>tol     -> FAIL_NON_EXEMPT   (fail loud)
#   exempt drift==baseline   -> PASS_EXEMPT       (frozen legacy, allowed)
#   exempt drift!=baseline   -> FAIL_DRIFT_CHANGED (drift moved, fail loud)
# Detects early/late revenue recognition leaks (PSAK-72 contract-liability
# integrity). CRITICAL severity (revenue integrity).
check_16_deferred_revenue_reconciliation() {
    local tenant="$1"
    local verdict
    verdict=$(psql_cmd "SELECT verdict FROM verify_deferred_revenue_reconciliation_all() WHERE tenant_id = '$tenant';")
    if [ "$verdict" = "PASS" ] || [ "$verdict" = "PASS_EXEMPT" ]; then
        CHK_PASS=1
        CHK_DETAIL=""
    else
        local drift
        drift=$(psql_cmd "SELECT drift FROM verify_deferred_revenue_reconciliation_all() WHERE tenant_id = '$tenant';")
        CHK_PASS=0
        CHK_DETAIL="deferred-revenue reconciliation $verdict (drift=$drift)"
        detail "[CHECK 16] $tenant: $CHK_DETAIL"
    fi
}

# BILL_INV_RECON_V272 -- Check 17: bill Persediaan == inventory_ledger (dual-ledger).
# verify_bill_inventory_reconciliation_all(): per tenant, NET Persediaan from BILL +
# RECLASSIFY_BILL_INVENTORY (Cr nets reclassified debits back out) vs SUM(inventory_ledger
# .total_cost) WHERE source_type='BILL'. A non-inventory bill debiting Persediaan without
# an inventory_ledger inbound -> GL>ledger -> RED (proven 2-sided V272). Exemption store
# bill_inventory_reconciliation_exemptions grandfathers frozen kaos drift; a drift CHANGE
# still fails loud (FAIL_DRIFT_CHANGED). HIGH severity (value integrity).
check_17_bill_inventory_reconciliation() {
    local tenant="$1"
    local verdict
    verdict=$(psql_cmd "SELECT verdict FROM verify_bill_inventory_reconciliation_all() WHERE tenant_id = '$tenant';")
    if [ "$verdict" = "PASS" ] || [ "$verdict" = "PASS_EXEMPT" ]; then
        CHK_PASS=1
        CHK_DETAIL=""
    else
        local drift
        drift=$(psql_cmd "SELECT drift FROM verify_bill_inventory_reconciliation_all() WHERE tenant_id = '$tenant';")
        CHK_PASS=0
        CHK_DETAIL="bill inventory reconciliation $verdict (drift=$drift)"
        detail "[CHECK 17] $tenant: $CHK_DETAIL"
    fi
}

check_10_cogs_orphans() {
    local tenant="$1"
    local count
    count=$(psql_cmd "
        SELECT COUNT(*) FROM journal_entries je
        WHERE je.source_type IN ('SALES_INVOICE_COGS', 'SALES_RECEIPT_COGS')
          AND je.status = 'POSTED'
          AND je.reversed_by_id IS NULL
          AND je.tenant_id = '$tenant'
          AND NOT EXISTS (
              SELECT 1 FROM inventory_ledger il
              WHERE il.source_id = je.source_id
                AND il.tenant_id = je.tenant_id
                AND il.quantity_out > 0
          );
    ")
    if [ "$count" = "0" ]; then
        CHK_PASS=1
        CHK_DETAIL=""
    else
        CHK_PASS=0
        CHK_DETAIL="$count COGS journals without ledger entry"
        detail "[CHECK 10] $tenant: $CHK_DETAIL"
        # Log details
        local details
        details=$(psql_lines "
            SELECT je.journal_number || ' (' || je.source_type || ')'
            FROM journal_entries je
            WHERE je.source_type IN ('SALES_INVOICE_COGS', 'SALES_RECEIPT_COGS')
              AND is_effective_journal(je.id)  -- Rule 8.1
              AND je.tenant_id = '$tenant'
              AND NOT EXISTS (
                  SELECT 1 FROM inventory_ledger il
                  WHERE il.source_id = je.source_id AND il.tenant_id = je.tenant_id AND il.quantity_out > 0
              ) LIMIT 10;
        ")
        echo "$details" | while read -r line; do detail "  $line"; done
    fi
}

check_11_opening_balance() {
    local tenant="$1"
    local vendor_drift bank_drift
    CHK_PASS=1
    CHK_DETAIL=""

    # 11a. Vendor opening_balance
    vendor_drift=$(psql_cmd "
        SELECT COUNT(*) FROM (
            SELECT v.id
            FROM vendors v
            LEFT JOIN (
                SELECT je.source_id, SUM(jl.credit) AS journal_amount
                FROM journal_entries je
                JOIN journal_lines jl ON jl.journal_id = je.id
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE je.source_type = 'OPENING'
                  AND is_effective_journal(je.id)  -- Rule 8.1
                  AND coa.account_type = 'PAYABLE' AND jl.credit > 0
                  AND je.tenant_id = '$tenant'
                GROUP BY je.source_id
            ) voj ON voj.source_id = v.id
            WHERE v.tenant_id = '$tenant'
              AND COALESCE(v.opening_balance, 0) > 0
              AND ABS(COALESCE(v.opening_balance, 0) - COALESCE(voj.journal_amount, 0)) > 0.01
        ) sub;
    ")

    # 11b. Bank opening_balance
    bank_drift=$(psql_cmd "
        SELECT COUNT(*) FROM (
            SELECT ba.id
            FROM bank_accounts ba
            LEFT JOIN (
                SELECT je.source_id, je.tenant_id, SUM(jl.debit) AS journal_amount
                FROM journal_entries je
                JOIN journal_lines jl ON jl.journal_id = je.id
                JOIN bank_accounts ba2 ON ba2.id = je.source_id AND ba2.tenant_id = je.tenant_id
                WHERE je.source_type = 'OPENING'
                  AND is_effective_journal(je.id)  -- Rule 8.1
                  AND jl.account_id = ba2.coa_id AND jl.debit > 0
                  AND je.tenant_id = '$tenant'
                GROUP BY je.source_id, je.tenant_id
            ) boj ON boj.source_id = ba.id AND boj.tenant_id = ba.tenant_id
            WHERE ba.tenant_id = '$tenant'
              AND COALESCE(ba.opening_balance, 0) > 0
              AND ABS(COALESCE(ba.opening_balance, 0) - COALESCE(boj.journal_amount, 0)) > 0.01
        ) sub;
    ")

    local total_drift=$(( ${vendor_drift:-0} + ${bank_drift:-0} ))
    if [ "$total_drift" -gt 0 ]; then
        CHK_PASS=0
        local parts=""
        [ "${vendor_drift:-0}" -gt 0 ] && parts="vendor=$vendor_drift"
        [ "${bank_drift:-0}" -gt 0 ] && { [ -n "$parts" ] && parts="$parts, "; parts="${parts}bank=$bank_drift"; }
        CHK_DETAIL="OB drift: $parts"
        detail "[CHECK 11] $tenant: $CHK_DETAIL"
        # Log bank details
        if [ "${bank_drift:-0}" -gt 0 ]; then
            local details
            details=$(psql_lines "
                SELECT ba.account_name || ': field=' || ba.opening_balance || ' journal=' || COALESCE(boj.journal_amount, 0)
                FROM bank_accounts ba
                LEFT JOIN (
                    SELECT je.source_id, je.tenant_id, SUM(jl.debit) AS journal_amount
                    FROM journal_entries je
                    JOIN journal_lines jl ON jl.journal_id = je.id
                    JOIN bank_accounts ba2 ON ba2.id = je.source_id AND ba2.tenant_id = je.tenant_id
                    WHERE je.source_type = 'OPENING' AND is_effective_journal(je.id)  -- Rule 8.1
                      AND jl.account_id = ba2.coa_id AND jl.debit > 0 AND je.tenant_id = '$tenant'
                    GROUP BY je.source_id, je.tenant_id
                ) boj ON boj.source_id = ba.id AND boj.tenant_id = ba.tenant_id
                WHERE ba.tenant_id = '$tenant'
                  AND COALESCE(ba.opening_balance, 0) > 0
                  AND ABS(COALESCE(ba.opening_balance, 0) - COALESCE(boj.journal_amount, 0)) > 0.01
                LIMIT 10;
            ")
            echo "$details" | while read -r line; do detail "  $line"; done
        fi
    fi
}


check_13_status_desync() {
    # V242 (2026-09-13): SQL lama kehilangan SEMUA tanda kutip (mati sejak 20 Apr)
    # dan hanya menghadap satu arah. Kini DUA arah di hc_status_desync_members():
    #   jurnal hidup tapi status<>POSTED, DAN status POSTED tapi jurnalnya dibalik.
    local tenant="$1"
    local row verdict cnt
    row=$(psql_cmd "SELECT verdict||'|'||member_count FROM hc_verdict('status_desync', '$tenant');")
    verdict="${row%%|*}"
    if [ "$verdict" = "PASS" ] || [ "$verdict" = "PASS_EXEMPT" ]; then
        CHK_PASS=1
        CHK_DETAIL=""
    elif [ "$row" = "__GAGAL__" ]; then
        CHK_PASS=0
        CHK_DETAIL="__GAGAL__"
    else
        cnt="${row#*|}"
        CHK_PASS=0
        CHK_DETAIL="status desync $verdict dokumen=$cnt"
        detail "[CHECK 13] $tenant: $CHK_DETAIL"
    fi
}

check_12_negative_balance() {
    local tenant="$1"
    local count
    count=$(psql_cmd "
        SELECT COUNT(*) FROM (
            SELECT cba.account_code, cba.name,
                   COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) AS balance
            FROM (
                SELECT coa.id, coa.account_code, coa.name
                FROM chart_of_accounts coa
                WHERE coa.account_code IN ('1-10100', '1-10200')
                  AND coa.tenant_id = '$tenant'
                UNION ALL
                SELECT coa.id, coa.account_code, coa.name
                FROM bank_accounts ba
                JOIN chart_of_accounts coa ON coa.id = ba.coa_id
                WHERE ba.tenant_id = '$tenant'
            ) cba
            LEFT JOIN journal_lines jl ON jl.account_id = cba.id
            LEFT JOIN journal_entries je ON je.id = jl.journal_id
                AND je.status = 'POSTED'
                AND je.reversed_by_id IS NULL
                AND je.tenant_id = '$tenant'
            GROUP BY cba.account_code, cba.name
            HAVING COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) < 0
        ) sub;
    ")
    if [ "$count" = "0" ]; then
        CHK_PASS=1
        CHK_DETAIL=""
    else
        CHK_PASS=0
        CHK_DETAIL=""
        # Get details for Discord inline
        local details
        details=$(psql_lines "
            SELECT cba.account_code || ' ' || cba.name || '=' || COALESCE(SUM(jl.debit) - SUM(jl.credit), 0)
            FROM (
                SELECT coa.id, coa.account_code, coa.name
                FROM chart_of_accounts coa
                WHERE coa.account_code IN ('1-10100', '1-10200')
                  AND coa.tenant_id = '$tenant'
                UNION ALL
                SELECT coa.id, coa.account_code, coa.name
                FROM bank_accounts ba
                JOIN chart_of_accounts coa ON coa.id = ba.coa_id
                WHERE ba.tenant_id = '$tenant'
            ) cba
            LEFT JOIN journal_lines jl ON jl.account_id = cba.id
            LEFT JOIN journal_entries je ON je.id = jl.journal_id
                AND is_effective_journal(je.id)  -- Rule 8.1
                AND je.tenant_id = '$tenant'
            GROUP BY cba.account_code, cba.name
            HAVING COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) < 0
            ORDER BY cba.account_code;
        ")
        CHK_DETAIL=$(echo "$details" | tr '\n' ', ' | sed 's/,$//')
        detail "[CHECK 12] $tenant: $count negative: $CHK_DETAIL"
    fi
}

# ==================================================
# MAIN LOOP
# ==================================================

log "=========================================="
log "MilkyHoop Accounting Health Check v2 (P5)"
log "=========================================="

# Get active tenants (skip cafeanna)
TENANTS=$(psql_lines "
    SELECT DISTINCT tenant_id FROM journal_entries
    WHERE status = 'POSTED' AND tenant_id NOT IN ('$SKIP_TENANTS')
    ORDER BY tenant_id;
")

TENANT_COUNT=$(echo "$TENANTS" | wc -l | tr -d ' ')
FIRST_TENANT=$(echo "$TENANTS" | head -1)

log "Tenants: $TENANT_COUNT (skipping: $SKIP_TENANTS)"
log ""

for TENANT in $TENANTS; do
    log "--- Tenant: $TENANT ---"

    # Track per-tenant results
    T_CRITICAL=0
    T_HIGH=0
    T_WARNING=0
    T_PASS=0

    # Category accumulators
    LEDGER_PASS=0; LEDGER_TOTAL=4; LEDGER_FAILS=""
    SYNC_PASS=0;   SYNC_TOTAL=4;   SYNC_FAILS=""
    VALUE_PASS=0;  VALUE_TOTAL=3;   VALUE_FAILS=""
    ANOMALY_DETAIL=""

    # ---- LEDGER INTEGRITY (CRITICAL) ----
    # Check 1: Journal Balance
    check_1_journal_balance "$TENANT"
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    if [ "$CHK_PASS" = "1" ]; then
        LEDGER_PASS=$((LEDGER_PASS + 1)); PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    else
        CRITICAL_COUNT=$((CRITICAL_COUNT + 1)); T_CRITICAL=$((T_CRITICAL + 1))
        LEDGER_FAILS="${LEDGER_FAILS}balance: $CHK_DETAIL; "
        log "  CRITICAL [1] Journal Balance: $CHK_DETAIL"
    fi

    # Check 2: Hash Chain
    check_2_hash_chain "$TENANT"
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    if [ "$CHK_PASS" = "1" ]; then
        LEDGER_PASS=$((LEDGER_PASS + 1)); PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    elif is_broken "$CHK_DETAIL"; then
        note_broken 2 "Hash Chain" "$CHK_DETAIL"
    else
        CRITICAL_COUNT=$((CRITICAL_COUNT + 1)); T_CRITICAL=$((T_CRITICAL + 1))
        LEDGER_FAILS="${LEDGER_FAILS}hash: $CHK_DETAIL; "
        log "  CRITICAL [2] Hash Chain: $CHK_DETAIL"
    fi

    # Check 5: Orphaned Lines
    check_5_orphaned_lines "$TENANT"
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    if [ "$CHK_PASS" = "1" ]; then
        LEDGER_PASS=$((LEDGER_PASS + 1)); PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    else
        CRITICAL_COUNT=$((CRITICAL_COUNT + 1)); T_CRITICAL=$((T_CRITICAL + 1))
        LEDGER_FAILS="${LEDGER_FAILS}orphans: $CHK_DETAIL; "
        log "  CRITICAL [5] Orphaned Lines: $CHK_DETAIL"
    fi

    # Check 6: Sequence
    check_6_sequence "$TENANT"
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    if [ "$CHK_PASS" = "1" ]; then
        LEDGER_PASS=$((LEDGER_PASS + 1)); PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    else
        CRITICAL_COUNT=$((CRITICAL_COUNT + 1)); T_CRITICAL=$((T_CRITICAL + 1))
        LEDGER_FAILS="${LEDGER_FAILS}sequence: $CHK_DETAIL; "
        log "  CRITICAL [6] Sequence: $CHK_DETAIL"
    fi

    # ---- SYNC LAYERS (HIGH) ----
    # Check 3: Bank Sync
    check_3_bank_sync "$TENANT"
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    if [ "$CHK_PASS" = "1" ]; then
        SYNC_PASS=$((SYNC_PASS + 1)); PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    else
        HIGH_COUNT=$((HIGH_COUNT + 1)); T_HIGH=$((T_HIGH + 1))
        SYNC_FAILS="${SYNC_FAILS}bank: $CHK_DETAIL; "
        log "  HIGH [3] Bank Sync: $CHK_DETAIL"
    fi

    # Check 4: Inventory qty
    check_4_inventory_qty "$TENANT"
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    if [ "$CHK_PASS" = "1" ]; then
        SYNC_PASS=$((SYNC_PASS + 1)); PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    else
        HIGH_COUNT=$((HIGH_COUNT + 1)); T_HIGH=$((T_HIGH + 1))
        SYNC_FAILS="${SYNC_FAILS}inv qty: $CHK_DETAIL; "
        log "  HIGH [4] Inventory Qty: $CHK_DETAIL"
    fi

    # Check 7: AP Invariant
    check_7_ap_invariant "$TENANT"
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    if [ "$CHK_PASS" = "1" ]; then
        SYNC_PASS=$((SYNC_PASS + 1)); PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    elif is_broken "$CHK_DETAIL"; then
        note_broken 7 "AP Invariant" "$CHK_DETAIL"
    else
        CRITICAL_COUNT=$((CRITICAL_COUNT + 1)); T_CRITICAL=$((T_CRITICAL + 1))
        SYNC_FAILS="${SYNC_FAILS}AP: $CHK_DETAIL; "
        log "  CRITICAL [7] AP Invariant: $CHK_DETAIL"
    fi

    # Check 14: AR Reconciliation (enforce-with-grandfather, Layer 3) — AUTHORITATIVE
    check_14_ar_reconciliation_enforce "$TENANT"
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    if [ "$CHK_PASS" = "1" ]; then
        SYNC_PASS=$((SYNC_PASS + 1)); PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    elif is_broken "$CHK_DETAIL"; then
        note_broken 14 "AR Reconciliation" "$CHK_DETAIL"
    else
        CRITICAL_COUNT=$((CRITICAL_COUNT + 1)); T_CRITICAL=$((T_CRITICAL + 1))
        SYNC_FAILS="${SYNC_FAILS}AR-recon: $CHK_DETAIL; "
        log "  CRITICAL [14] AR Reconciliation: $CHK_DETAIL"
    fi

    # Check 16: Deferred-revenue Reconciliation (PSAK-72 P4, grandfathered) — CRITICAL
    check_16_deferred_revenue_reconciliation "$TENANT"
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    if [ "$CHK_PASS" = "1" ]; then
        SYNC_PASS=$((SYNC_PASS + 1)); PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    else
        CRITICAL_COUNT=$((CRITICAL_COUNT + 1)); T_CRITICAL=$((T_CRITICAL + 1))
        SYNC_FAILS="${SYNC_FAILS}deferred-rev: $CHK_DETAIL; "
        log "  CRITICAL [16] Deferred-Revenue Reconciliation: $CHK_DETAIL"
    fi

    # ---- VALUE INTEGRITY (HIGH) ----
    # Check 9: Inventory Value
    check_9_inventory_value "$TENANT"
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    if [ "$CHK_PASS" = "1" ]; then
        VALUE_PASS=$((VALUE_PASS + 1)); PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    elif is_broken "$CHK_DETAIL"; then
        note_broken 9 "Inventory Value" "$CHK_DETAIL"
    else
        HIGH_COUNT=$((HIGH_COUNT + 1)); T_HIGH=$((T_HIGH + 1))
        VALUE_FAILS="${VALUE_FAILS}inv value: $CHK_DETAIL; "
        log "  HIGH [9] Inventory Value: $CHK_DETAIL"
    fi

    # Check 15: Inventory WAC Reconciliation (GL vs on_hand x WAC, grandfathered)
    check_15_inventory_wac_reconciliation "$TENANT"
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    if [ "$CHK_PASS" = "1" ]; then
        VALUE_PASS=$((VALUE_PASS + 1)); PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    else
        HIGH_COUNT=$((HIGH_COUNT + 1)); T_HIGH=$((T_HIGH + 1))
        VALUE_FAILS="${VALUE_FAILS}inv WAC recon: $CHK_DETAIL; "
        log "  HIGH [15] Inventory WAC Reconciliation: $CHK_DETAIL"
    fi

    # Check 17: Bill Inventory Reconciliation (GL Persediaan-from-bill vs inventory_ledger)
    check_17_bill_inventory_reconciliation "$TENANT"
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    if [ "$CHK_PASS" = "1" ]; then
        VALUE_PASS=$((VALUE_PASS + 1)); PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    else
        HIGH_COUNT=$((HIGH_COUNT + 1)); T_HIGH=$((T_HIGH + 1))
        VALUE_FAILS="${VALUE_FAILS}bill inv recon: $CHK_DETAIL; "
        log "  HIGH [17] Bill Inventory Reconciliation: $CHK_DETAIL"
    fi

    # Check 10: COGS Orphans
    check_10_cogs_orphans "$TENANT"
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    if [ "$CHK_PASS" = "1" ]; then
        VALUE_PASS=$((VALUE_PASS + 1)); PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    else
        HIGH_COUNT=$((HIGH_COUNT + 1)); T_HIGH=$((T_HIGH + 1))
        VALUE_FAILS="${VALUE_FAILS}COGS: $CHK_DETAIL; "
        log "  HIGH [10] COGS Orphans: $CHK_DETAIL"
    fi

    # Check 11: Opening Balance
    check_11_opening_balance "$TENANT"
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    if [ "$CHK_PASS" = "1" ]; then
        VALUE_PASS=$((VALUE_PASS + 1)); PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    else
        HIGH_COUNT=$((HIGH_COUNT + 1)); T_HIGH=$((T_HIGH + 1))
        VALUE_FAILS="${VALUE_FAILS}$CHK_DETAIL; "
        log "  HIGH [11] Opening Balance: $CHK_DETAIL"
    fi

    # ---- ANOMALY (WARNING) ----
    # Check 12: Negative Balance
    check_12_negative_balance "$TENANT"
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    if [ "$CHK_PASS" = "1" ]; then
        PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    else
        WARNING_COUNT=$((WARNING_COUNT + 1)); T_WARNING=$((T_WARNING + 1))
        ANOMALY_DETAIL="$CHK_DETAIL"
        log "  WARNING [12] Negative Balance: $CHK_DETAIL"
    fi

    # Check 13: Status Desync (journal exists but accounting_status wrong)
    check_13_status_desync "$TENANT"
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    if [ "$CHK_PASS" = "1" ]; then
        PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    elif is_broken "$CHK_DETAIL"; then
        note_broken 13 "Status Desync" "$CHK_DETAIL"
    else
        WARNING_COUNT=$((WARNING_COUNT + 1)); T_WARNING=$((T_WARNING + 1))
        log "  WARNING [13] Status Desync: $CHK_DETAIL"
    fi

    # ---- Build per-tenant Discord block ----
    TENANT_BLOCK=""

    # Ledger integrity line
    if [ "$LEDGER_PASS" = "$LEDGER_TOTAL" ]; then
        TENANT_BLOCK="${TENANT_BLOCK}  ✅ Ledger integrity ($LEDGER_PASS/$LEDGER_TOTAL: balance, hash, orphans, sequence)\n"
    else
        TENANT_BLOCK="${TENANT_BLOCK}  ❌ Ledger integrity ($LEDGER_PASS/$LEDGER_TOTAL) — ${LEDGER_FAILS}\n"
    fi

    # Sync layers line
    if [ "$SYNC_PASS" = "$SYNC_TOTAL" ]; then
        TENANT_BLOCK="${TENANT_BLOCK}  ✅ Sync layers ($SYNC_PASS/$SYNC_TOTAL: bank, inv qty, AP, AR-recon)\n"
    else
        TENANT_BLOCK="${TENANT_BLOCK}  ⚠️ Sync layers ($SYNC_PASS/$SYNC_TOTAL) — ${SYNC_FAILS}\n"
    fi

    # Value integrity line
    if [ "$VALUE_PASS" = "$VALUE_TOTAL" ]; then
        TENANT_BLOCK="${TENANT_BLOCK}  ✅ Value integrity ($VALUE_PASS/$VALUE_TOTAL: inv value, COGS, opening balance)\n"
    else
        TENANT_BLOCK="${TENANT_BLOCK}  ⚠️ Value integrity ($VALUE_PASS/$VALUE_TOTAL) — ${VALUE_FAILS}\n"
    fi

    # Anomaly line
    if [ -n "$ANOMALY_DETAIL" ]; then
        TENANT_BLOCK="${TENANT_BLOCK}  💡 Anomaly: $ANOMALY_DETAIL\n"
    fi

    DISCORD_BODY="${DISCORD_BODY}📊 **Tenant: $TENANT**\n${TENANT_BLOCK}\n"

    log ""
done

# ---- Global Summary ----

TOTAL_POSTED=$(psql_cmd "SELECT COUNT(*) FROM journal_entries WHERE status = 'POSTED';")
TOTAL_LINES=$(psql_cmd "SELECT COUNT(*) FROM journal_lines;")

log "=========================================="
log "SUMMARY"
log "=========================================="
log "  Checks run:    $TOTAL_CHECKS"
log "  Passed:        $PASS_COUNT"
log "  Critical:      $CRITICAL_COUNT"
log "  BROKEN:        $BROKEN_COUNT pemeriksaan ($BROKEN_EVENTS kejadian)${BROKEN_NAMES:+  -> $BROKEN_NAMES}"
log "  High:          $HIGH_COUNT"
log "  Warnings:      $WARNING_COUNT"
log "  Posted jrnls:  $TOTAL_POSTED"
log "  Journal lines: $TOTAL_LINES"
log "  Tenants:       $TENANT_COUNT"
log "  Log file:      $LOG_FILE"

# ---- Discord Message ----

NEXT_RUN=$(date -u -d "+1 day" +"%Y-%m-%d 06:00 UTC" 2>/dev/null || date -u -v+1d +"%Y-%m-%d 06:00 UTC" 2>/dev/null || echo "tomorrow 06:00 UTC")

# Status icon
if [ "$CRITICAL_COUNT" -gt 0 ]; then
    STATUS_ICON="🔴"
    STATUS_TEXT="CRITICAL"
elif [ "$BROKEN_COUNT" -gt 0 ]; then
    STATUS_ICON="🔧"
    STATUS_TEXT="TOOLS BROKEN"
elif [ "$HIGH_COUNT" -gt 0 ]; then
    STATUS_ICON="🟡"
    STATUS_TEXT="HIGH ALERTS"
elif [ "$WARNING_COUNT" -gt 0 ]; then
    STATUS_ICON="🟠"
    STATUS_TEXT="WARNINGS"
else
    STATUS_ICON="🟢"
    STATUS_TEXT="ALL CLEAR"
fi

DISCORD_MSG="${STATUS_ICON} **MilkyHoop Health Check — ${DATE_SHORT}**\n\n"
DISCORD_MSG="${DISCORD_MSG}${DISCORD_BODY}"
DISCORD_MSG="${DISCORD_MSG}━━━━━━━━━━━━━━━━━━━━━━━━━\n"
DISCORD_MSG="${DISCORD_MSG}Summary: ${PASS_COUNT}/${TOTAL_CHECKS} ✅"
[ "$CRITICAL_COUNT" -gt 0 ] && DISCORD_MSG="${DISCORD_MSG} │ ${CRITICAL_COUNT} ❌ CRITICAL"
# Syarat 2: sebut JUMLAH dan NAMA + UMUR. Angka telanjang tak bisa ditindak.
[ "$BROKEN_COUNT" -gt 0 ] && DISCORD_MSG="${DISCORD_MSG}\n🔧 ${BROKEN_COUNT} pemeriksaan RUSAK (perkakas, bukan data): ${BROKEN_NAMES}"
[ "$HIGH_COUNT" -gt 0 ] && DISCORD_MSG="${DISCORD_MSG} │ ${HIGH_COUNT} ⚠️ HIGH"
[ "$WARNING_COUNT" -gt 0 ] && DISCORD_MSG="${DISCORD_MSG} │ ${WARNING_COUNT} 💡 WARNING"
DISCORD_MSG="${DISCORD_MSG}\nNext run: ${NEXT_RUN}"

send_discord "$DISCORD_MSG"

log ""
log "Discord message sent: $STATUS_TEXT"

# ---- Exit Code ----

if [ "$CRITICAL_COUNT" -gt 0 ]; then
    log "EXIT 2: CRITICAL failures detected"
    exit 2
elif [ "$BROKEN_COUNT" -gt 0 ]; then
    # Syarat 1: jalan yang memuat BROKEN TAK BISA berakhir 0.
    log "EXIT 3: $BROKEN_COUNT pemeriksaan RUSAK — $BROKEN_NAMES"
    exit 3
elif [ "$HIGH_COUNT" -gt 0 ]; then
    log "EXIT 1: HIGH alerts detected"
    exit 1
else
    log "EXIT 0: All clear"
    exit 0
fi
