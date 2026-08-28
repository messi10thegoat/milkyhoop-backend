#!/usr/bin/env bash
# GATE T146 — WAF "--" pada teks chat yang diketik manusia
# ARTEFAK GATE. Bukan kode produk. Diukur LEWAT https://milkyhoop.com (nginx).
set -o pipefail

BASE="${BASE:-https://milkyhoop.com}"
TOK="${TOK:?TOK (JWT) wajib}"
OUT="${OUT:-/tmp/waf-gate}"
mkdir -p "$OUT"
PASS=0; FAIL=0

uuidv4() { cat /proc/sys/kernel/random/uuid; }

# probe_chat <id> <text> -> tulis $OUT/<id>.code, .body, .conv
probe_chat() {
  local id="$1" text="$2"
  local conv; conv="$(uuidv4)"
  echo "$conv" > "$OUT/$id.conv"
  local payload; payload="$(python3 -c 'import json,sys;print(json.dumps({"conversation_id":sys.argv[1],"text":sys.argv[2]}))' "$conv" "$text")"
  curl -sS -o "$OUT/$id.body" -D "$OUT/$id.hdr" -w "%{http_code}" \
    -X POST "$BASE/api/v3/chat/message/stream" \
    -H "Authorization: Bearer $TOK" \
    -H "Content-Type: application/json" \
    -H "Accept: text/event-stream" \
    --max-time 120 --data "$payload" > "$OUT/$id.code"
}

probe_raw() { # <id> <method> <path> <content-type> <databody> [extra header]
  local id="$1" m="$2" p="$3" ct="$4" data="$5" extra="${6:-X-Gate-Nop: 1}"
  curl -sS -o "$OUT/$id.body" -D "$OUT/$id.hdr" -w "%{http_code}" \
    -X "$m" "$BASE$p" -H "Authorization: Bearer $TOK" \
    -H "Content-Type: $ct" -H "$extra" --max-time 60 --data "$data" > "$OUT/$id.code"
}

code()  { cat "$OUT/$1.code"; }
bytes() { wc -c < "$OUT/$1.body" | tr -d " "; }
isblk() { grep -q WAF_BLOCKED "$OUT/$1.body" && echo yes || echo no; }

report() { # <label> <id> <expect-desc> <verdict>
  printf "%-6s %-46s http=%-4s bytes=%-7s waf_blocked=%-3s -> %s\n" \
    "$1" "$3" "$(code $2)" "$(bytes $2)" "$(isblk $2)" "$4"
}

echo "=== BASE=$BASE  $(date -Is) ==="
echo
echo "--- SISI MERAH (harus MERAH sekarang) ---"
probe_chat R1 "halo, berapa saldo kas -- sekarang"
report RED R1 "R1 chat teks owner ber-dashdash" "$( [ "$(code R1)" = 200 ] && [ "$(bytes R1)" -gt 0 ] && echo GREEN || echo RED )"
probe_chat R2 "tolong jelaskan laporan -- yang mana yang cocok"
report RED R2 "R2 prosa owner ber-dashdash (tak berbahaya)" "$( [ "$(code R2)" = 200 ] && echo GREEN || echo RED )"

echo
echo "--- KONTROL POSITIF (gate harus bisa HIJAU) ---"
probe_chat C1 "halo, berapa saldo kas xx sekarang"
report CTRL C1 "C1 kalimat sama tanpa dashdash" "$( [ "$(code C1)" = 200 ] && [ "$(bytes C1)" -gt 0 ] && echo GREEN || echo RED )"

echo
echo "--- SISI HIJAU-TETAP-HIJAU (garis dasar) ---"
probe_raw G1a POST /api/auth/login "application/json" '{"email":"nobody@example.com","password":"x -- y"}'
report BASE G1a "G1a non-chat /api/auth/login body dashdash" "$(code G1a)"
probe_raw G1b POST /api/__gate_nonexistent__ "application/json" '{"q":"a -- b"}'
report BASE G1b "G1b non-chat path tak ada, body dashdash" "$(code G1b)"
probe_chat G2 "1' OR '1'='1' UNION ALL SELECT NULL--"
report BASE G2 "G2 SQLi telanjang di jalur chat" "$(code G2)"
probe_chat G3a "coba ../ ini"
report BASE G3a "G3a path-traversal di jalur chat" "$(code G3a)"
probe_chat G3b "coba /* ini */ ya"
report BASE G3b "G3b blok-komentar di jalur chat" "$(code G3b)"
probe_raw G4 GET /api/items "application/json" "" "X-Gate-Probe: 1' OR '1'='1' UNION ALL SELECT NULL--"
report BASE G4 "G4 header pola SQLi" "$(code G4)"
probe_raw G4c GET /api/items "application/json" "" "X-Gate-Probe: benign"
report CTRL G4c "G4c kontrol header jinak" "$(code G4c)"
printf -- '--BND\r\nContent-Disposition: form-data; name="file"; filename="bank.csv"\r\nContent-Type: text/csv\r\n\r\ntanggal,ket,jumlah\r\n2026-01-01,TRF -- masuk,1000\r\n--BND--\r\n' > "$OUT/g5.bin"
curl -sS -o "$OUT/G5.body" -D "$OUT/G5.hdr" -w "%{http_code}" -X POST "$BASE/api/__gate_nonexistent__" \
  -H "Authorization: Bearer $TOK" -H "Content-Type: multipart/form-data; boundary=BND" \
  --max-time 60 --data-binary "@$OUT/g5.bin" > "$OUT/G5.code"
report BASE G5 "G5 multipart CSV bank ber-dashdash" "$(code G5)"
echo
echo "=== SELESAI. artefak di $OUT ==="
