CONTAINER="milkyhoop-dev-postgres-1"
DB_NAME="milkydb"
DB_USER="postgres"
LOG_FILE=/dev/null; detail(){ :; }
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
check_14_ar_reconciliation_enforce() {
    local tenant="$1"
    local verdict
    verdict=$(psql_cmd "SELECT verdict FROM verify_ar_reconciliation_all() WHERE tenant_id = '$tenant';")
    if [ "$verdict" = "PASS" ] || [ "$verdict" = "PASS_EXEMPT" ]; then
        CHK_PASS=1
        CHK_DETAIL=""
    else
        local drift
        drift=$(psql_cmd "SELECT total_drift FROM verify_ar_reconciliation_all() WHERE tenant_id = '$tenant';")
        CHK_PASS=0
        CHK_DETAIL="AR reconciliation $verdict (drift=$drift)"
        detail "[CHECK 14] $tenant: $CHK_DETAIL"
    fi
}
for t in grapgrap-manado kaos-biru-konveksi; do check_14_ar_reconciliation_enforce $t; echo "$t CHK_PASS=$CHK_PASS DETAIL=[$CHK_DETAIL]"; done
