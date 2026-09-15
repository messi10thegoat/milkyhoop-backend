"""Kategori BROKEN — perkakas rusak BUKAN kegagalan data, dan bukan pula LULUS.

Sesudah patch Law 33, tiga penjaga yang mati akhirnya berbicara — tapi mereka
berbicara sebagai CRITICAL/HIGH/WARNING, yaitu merah-ALAT menyamar jadi
merah-PRODUK. Dua-duanya menuntut tindakan berbeda: yang satu panggil akuntan,
yang satu panggil kita. Dua tindakan berbeda tak boleh berbagi satu label.

EMPAT SYARAT yang dipegang:
 1. BROKEN tak pernah lulus: keluar dari PASS_COUNT, punya cacah sendiri,
    exit code bukan-nol (3) -- jalan yang memuat BROKEN TAK BISA berakhir 0.
 2. Discord menyebut JUMLAH dan NAMA, bukan angka telanjang.
 3. BROKEN punya UMUR yang terlihat -- supaya ember ini tak jadi tempat parkir
    yang nyaman. Umur bertambah tiap pagi tanpa berkurang = sinyal tersendiri.
 4. BROKEN = 0 adalah SASARAN, bukan keadaan mapan. Ditulis di kepala skrip.

check_2 IKUT diberi cabang BROKEN, bukan cuma ketiga yang mati: kontrol positif
memutasi check_2, dan kalau perkakas yang sengaja dirusak melapor CRITICAL, ia
membantah pembedaan yang justru jadi alasan kategori ini ada.
"""
import io
import sys

PATH = "monitoring/accounting_health_check.sh"

# ---------- 1. pencacah ----------
A_CNT = """# Counters
CRITICAL_COUNT=0
HIGH_COUNT=0
WARNING_COUNT=0
TOTAL_CHECKS=0
PASS_COUNT=0"""

B_CNT = """# Counters
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
BROKEN_NAMES=\"\""""

# ---------- 2. helper ----------
A_LOG = """log() {
    echo "$DATE | $1"
    echo "$DATE | $1" >> "$LOG_FILE"
}"""

B_LOG = """log() {
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
        7)  echo "2026-03-05" ;;
        9)  echo "2026-03-05" ;;
        13) echo "2026-04-20" ;;
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
    BROKEN_COUNT=$((BROKEN_COUNT + 1))
    case "$BROKEN_NAMES" in
        *"$label"*) : ;;
        *) BROKEN_NAMES="${BROKEN_NAMES}${BROKEN_NAMES:+, }${label}" ;;
    esac
    log "  BROKEN $label: perkakas tak bisa dijalankan — $detail"
}"""

# ---------- 3. empat pemanggil ----------
CALLERS = [
    # check_2 (punyaku; kontrol positif memutasinya)
    ("""    if [ "$CHK_PASS" = "1" ]; then
        LEDGER_PASS=$((LEDGER_PASS + 1)); PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    else
        CRITICAL_COUNT=$((CRITICAL_COUNT + 1)); T_CRITICAL=$((T_CRITICAL + 1))
        LEDGER_FAILS="${LEDGER_FAILS}hash: $CHK_DETAIL; "
        log "  CRITICAL [2] Hash Chain: $CHK_DETAIL"
    fi""",
     """    if [ "$CHK_PASS" = "1" ]; then
        LEDGER_PASS=$((LEDGER_PASS + 1)); PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    elif is_broken "$CHK_DETAIL"; then
        note_broken 2 "Hash Chain" "$CHK_DETAIL"
    else
        CRITICAL_COUNT=$((CRITICAL_COUNT + 1)); T_CRITICAL=$((T_CRITICAL + 1))
        LEDGER_FAILS="${LEDGER_FAILS}hash: $CHK_DETAIL; "
        log "  CRITICAL [2] Hash Chain: $CHK_DETAIL"
    fi"""),
    # check_7
    ("""        CRITICAL_COUNT=$((CRITICAL_COUNT + 1)); T_CRITICAL=$((T_CRITICAL + 1))
        SYNC_FAILS="${SYNC_FAILS}AP: $CHK_DETAIL; "
        log "  CRITICAL [7] AP Invariant: $CHK_DETAIL\"""",
     """        CRITICAL_COUNT=$((CRITICAL_COUNT + 1)); T_CRITICAL=$((T_CRITICAL + 1))
        SYNC_FAILS="${SYNC_FAILS}AP: $CHK_DETAIL; "
        log "  CRITICAL [7] AP Invariant: $CHK_DETAIL\""""),
    # check_9
    ("""        HIGH_COUNT=$((HIGH_COUNT + 1)); T_HIGH=$((T_HIGH + 1))
        VALUE_FAILS="${VALUE_FAILS}inv value: $CHK_DETAIL; "
        log "  HIGH [9] Inventory Value: $CHK_DETAIL\"""",
     """        HIGH_COUNT=$((HIGH_COUNT + 1)); T_HIGH=$((T_HIGH + 1))
        VALUE_FAILS="${VALUE_FAILS}inv value: $CHK_DETAIL; "
        log "  HIGH [9] Inventory Value: $CHK_DETAIL\""""),
]

# cabang elif untuk check_7 / 9 / 13 disisipkan lewat pola "else" spesifik
ELIF = [
    ("""    if [ "$CHK_PASS" = "1" ]; then
        SYNC_PASS=$((SYNC_PASS + 1)); PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    else
        CRITICAL_COUNT=$((CRITICAL_COUNT + 1)); T_CRITICAL=$((T_CRITICAL + 1))
        SYNC_FAILS="${SYNC_FAILS}AP: $CHK_DETAIL; \"""",
     """    if [ "$CHK_PASS" = "1" ]; then
        SYNC_PASS=$((SYNC_PASS + 1)); PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    elif is_broken "$CHK_DETAIL"; then
        note_broken 7 "AP Invariant" "$CHK_DETAIL"
    else
        CRITICAL_COUNT=$((CRITICAL_COUNT + 1)); T_CRITICAL=$((T_CRITICAL + 1))
        SYNC_FAILS="${SYNC_FAILS}AP: $CHK_DETAIL; \""""),
    ("""    if [ "$CHK_PASS" = "1" ]; then
        VALUE_PASS=$((VALUE_PASS + 1)); PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    else
        HIGH_COUNT=$((HIGH_COUNT + 1)); T_HIGH=$((T_HIGH + 1))
        VALUE_FAILS="${VALUE_FAILS}inv value: $CHK_DETAIL; \"""",
     """    if [ "$CHK_PASS" = "1" ]; then
        VALUE_PASS=$((VALUE_PASS + 1)); PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    elif is_broken "$CHK_DETAIL"; then
        note_broken 9 "Inventory Value" "$CHK_DETAIL"
    else
        HIGH_COUNT=$((HIGH_COUNT + 1)); T_HIGH=$((T_HIGH + 1))
        VALUE_FAILS="${VALUE_FAILS}inv value: $CHK_DETAIL; \""""),
    ("""    if [ "$CHK_PASS" = "1" ]; then
        PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    else
        WARNING_COUNT=$((WARNING_COUNT + 1)); T_WARNING=$((T_WARNING + 1))
        log "  WARNING [13] Status Desync: $CHK_DETAIL"
    fi""",
     """    if [ "$CHK_PASS" = "1" ]; then
        PASS_COUNT=$((PASS_COUNT + 1)); T_PASS=$((T_PASS + 1))
    elif is_broken "$CHK_DETAIL"; then
        note_broken 13 "Status Desync" "$CHK_DETAIL"
    else
        WARNING_COUNT=$((WARNING_COUNT + 1)); T_WARNING=$((T_WARNING + 1))
        log "  WARNING [13] Status Desync: $CHK_DETAIL"
    fi"""),
]

# ---------- 4. ringkasan ----------
A_SUM = '''log "  Critical:      $CRITICAL_COUNT"'''
B_SUM = '''log "  Critical:      $CRITICAL_COUNT"
log "  BROKEN:        $BROKEN_COUNT${BROKEN_NAMES:+  -> $BROKEN_NAMES}"'''

# ---------- 5. ikon status ----------
A_ICON = '''if [ "$CRITICAL_COUNT" -gt 0 ]; then
    STATUS_ICON="🔴"
    STATUS_TEXT="CRITICAL"
elif [ "$HIGH_COUNT" -gt 0 ]; then'''
B_ICON = '''if [ "$CRITICAL_COUNT" -gt 0 ]; then
    STATUS_ICON="🔴"
    STATUS_TEXT="CRITICAL"
elif [ "$BROKEN_COUNT" -gt 0 ]; then
    STATUS_ICON="🔧"
    STATUS_TEXT="TOOLS BROKEN"
elif [ "$HIGH_COUNT" -gt 0 ]; then'''

# ---------- 6. baris Discord ----------
A_DISC = '''[ "$CRITICAL_COUNT" -gt 0 ] && DISCORD_MSG="${DISCORD_MSG} │ ${CRITICAL_COUNT} ❌ CRITICAL"'''
B_DISC = '''[ "$CRITICAL_COUNT" -gt 0 ] && DISCORD_MSG="${DISCORD_MSG} │ ${CRITICAL_COUNT} ❌ CRITICAL"
# Syarat 2: sebut JUMLAH dan NAMA + UMUR. Angka telanjang tak bisa ditindak.
[ "$BROKEN_COUNT" -gt 0 ] && DISCORD_MSG="${DISCORD_MSG}\\n🔧 ${BROKEN_COUNT} pemeriksaan RUSAK (perkakas, bukan data): ${BROKEN_NAMES}"'''

# ---------- 7. exit code ----------
A_EXIT = '''if [ "$CRITICAL_COUNT" -gt 0 ]; then
    log "EXIT 2: CRITICAL failures detected"
    exit 2
elif [ "$HIGH_COUNT" -gt 0 ]; then'''
B_EXIT = '''if [ "$CRITICAL_COUNT" -gt 0 ]; then
    log "EXIT 2: CRITICAL failures detected"
    exit 2
elif [ "$BROKEN_COUNT" -gt 0 ]; then
    # Syarat 1: jalan yang memuat BROKEN TAK BISA berakhir 0.
    log "EXIT 3: $BROKEN_COUNT pemeriksaan RUSAK — $BROKEN_NAMES"
    exit 3
elif [ "$HIGH_COUNT" -gt 0 ]; then'''

SUNTINGAN = [("pencacah", A_CNT, B_CNT, 1), ("helper log", A_LOG, B_LOG, 1),
             ("ringkasan", A_SUM, B_SUM, 1), ("ikon", A_ICON, B_ICON, 1),
             ("discord", A_DISC, B_DISC, 1), ("exit", A_EXIT, B_EXIT, 1)]
SUNTINGAN += [("elif-%d" % i, a, b, 1) for i, (a, b) in enumerate(ELIF)]

teks = io.open(PATH, encoding="utf-8").read()
gagal = []
for nama, lama, _b, harus in SUNTINGAN:
    n = teks.count(lama)
    if n != harus:
        gagal.append("%s: cocok %dx, harus %dx" % (nama, n, harus))

if gagal:
    print("GAGAL — NOL ditulis:")
    for g in gagal:
        print("   " + g)
    sys.exit(1)

for nama, lama, baru, _h in SUNTINGAN:
    teks = teks.replace(lama, baru)

io.open(PATH, "w", encoding="utf-8").write(teks)
print("kategori BROKEN terpasang: %d suntingan" % len(SUNTINGAN))

ulang = io.open(PATH, encoding="utf-8").read()
for wajib in ("BROKEN_COUNT=0", "is_broken()", "note_broken()", "broken_since()",
              "exit 3", "TOOLS BROKEN", "pemeriksaan RUSAK"):
    if wajib not in ulang:
        print("GAGAL cek-balik: %r tak terpasang" % wajib)
        sys.exit(1)
if ulang.count("note_broken ") != 4:
    print("GAGAL cek-balik: cabang note_broken %d, harus 4" % ulang.count("note_broken "))
    sys.exit(1)
print("cek-balik: 4 cabang BROKEN, exit 3, penamaan + umur terpasang")
