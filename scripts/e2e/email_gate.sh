#!/bin/bash
# GATE — email fail-loud. DUA ARAH.
#
# BATAS SISI HIJAU, ditulis di sini supaya tak salah dibaca kemudian:
# sisi hijau memakai kunci PALSU. Resend akan menolaknya. Jadi yang dibuktikan
# adalah "kode MENCOBA mengirim dan tidak lagi ditahan prasyarat" — BUKAN
# "email terkirim". Yang bisa membuktikan pengiriman hanya kunci SUNGGUHAN,
# dan itu bagian (b): pengadaan, di luar kode.
B=${B:-http://localhost:8002/api}
PASS=0; FAIL=0
ok(){ if [ "$2" = "$3" ]; then echo "  ✓ $1: $2"; PASS=$((PASS+1));
      else echo "  ✗ $1: dapat=$2 HARAP=$3"; FAIL=$((FAIL+1)); fi; }
Q(){ local b=$(printf '%s' "$1"|base64); ssh root@159.89.202.160 "echo $b|base64 -d>/tmp/q.sql && docker cp /tmp/q.sql milkyhoop-dev-postgres-1:/tmp/q.sql>/dev/null && docker exec milkyhoop-dev-postgres-1 sh -c 'PGPASSWORD=Proyek771977 psql -U postgres -d milkydb -tA -f /tmp/q.sql'" 2>&1|tr -d '\r '; }
HZ=$(curl -s -o /dev/null -w '%{http_code}' "${B%/api}/healthz"); [ "$HZ" = "200" ] || { echo "!! healthz=$HZ"; exit 2; }
M="mailgate+$(date +%s)@kaosbiru.co.id"

# Gate WAJIB menetapkan keadaannya sendiri, bukan mewarisi keadaan run
# sebelumnya. Percobaan pertama menjalankan arah 1 di gateway yang MASIH
# memegang kunci palsu dari arah 2 — hasilnya 200, dan terbaca seolah
# prasyaratnya tak bekerja. Keadaan ambient yang tak dikendalikan = gate
# yang jawabannya bergantung pada urutan.
recreate(){ ssh root@159.89.202.160 "bash /tmp/setup_testgw_mail.sh $1" >/dev/null 2>&1
  for i in $(seq 1 30); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' "${B%/api}/healthz")" = "200" ] && return 0; sleep 1
  done; echo "!! gateway uji tak siap = KEGAGALAN ALAT"; exit 2; }

recreate ""
KL=$(ssh root@159.89.202.160 'docker exec mh-testgw-mail sh -c "printf %s \"\${#RESEND_API_KEY}\""')
[ "$KL" = "0" ] || { echo "!! kunci MASIH tersetel (panjang=$KL) — arah 1 tak sah"; exit 2; }
echo "=== ARAH 1: KUNCI KOSONG (panjang=0, diverifikasi) -> tolak jujur ==="
N0=$(Q "SELECT count(*) FROM pending_registrations;")
echo "    pending_registrations sebelum: $N0"
C=$(curl -s -o /tmp/eg -w '%{http_code}' -X POST "$B/auth/signup/register" -H 'Content-Type: application/json' -d "{\"email\":\"$M\"}")
ok "register -> HTTP" "$C" "503"
ok "success=false" "$(python3 -c 'import json;print(str(json.load(open("/tmp/eg")).get("success")).lower())' 2>/dev/null)" "false"
ok "error_code = EMAIL_NOT_CONFIGURED" "$(python3 -c 'import json;print(json.load(open("/tmp/eg")).get("error_code"))' 2>/dev/null)" "EMAIL_NOT_CONFIGURED"
ok "★ pesan terbaca FE (kunci message ADA)" "$(python3 -c 'import json;print("ada" if json.load(open("/tmp/eg")).get("message") else "tidak")' 2>/dev/null)" "ada"
echo "    pesan: $(python3 -c 'import json;print(json.load(open("/tmp/eg")).get("message",""))' 2>/dev/null)"
ok "★ NOL baris yatim" "$(Q "SELECT count(*) FROM pending_registrations;")" "$N0"
C2=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$B/auth/signup/resend-code" -H 'Content-Type: application/json' -d "{\"email\":\"$M\"}")
ok "resend-code -> HTTP" "$C2" "503"
echo "    ANTI-ENUMERASI: email yang SUDAH ADA harus dijawab sama"
C3=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$B/auth/signup/register" -H 'Content-Type: application/json' -d "{\"email\":\"delivered+owner@resend.dev\"}")
ok "email terdaftar -> HTTP sama" "$C3" "503"

echo
echo "=== ARAH 2: KUNCI TERISI (PALSU) -> prasyarat TIDAK lagi menahan ==="
# Gateway uji DIBUAT ULANG dengan kunci palsu. Versi pertama baris ini cuma
# placeholder yang tak menyetel apa pun — dan gate melaporkannya sebagai
# "prasyarat masih menahan", padahal yang salah alatnya. Diperbaiki.
recreate FAKEKEY
KEYLEN=$(ssh root@159.89.202.160 'docker exec mh-testgw-mail sh -c "printf %s \"\${#RESEND_API_KEY}\""')
echo "    RESEND_API_KEY di gateway uji: panjang=$KEYLEN (palsu)"
[ "$KEYLEN" = "0" ] && { echo "!! kunci tak tersetel = KEGAGALAN ALAT"; exit 2; }
C4=$(curl -s -o /tmp/eg2 -w '%{http_code}' -X POST "$B/auth/signup/register" -H 'Content-Type: application/json' -d "{\"email\":\"$M\"}")
echo "    register -> HTTP $C4"
# Dengan kunci PALSU, Resend MENOLAK permintaan. Jadi yang benar di sini bukan
# 200 melainkan 503 LAGI — tapi dari sebab BERBEDA: bukan prasyarat, melainkan
# layanan email menolak. Itulah skenario "kunci terpasang lalu habis kredit",
# dan sebelum batch ini ia menghasilkan "Cek email Anda" yang bohong.
ok "★ kunci ditolak layanan -> tetap JUJUR (503)" "$C4" "503"
ok "error_code = EMAIL_SEND_FAILED (BEDA dari kunci-kosong)" "$(python3 -c 'import json;print(json.load(open("/tmp/eg2")).get("error_code"))' 2>/dev/null)" "EMAIL_SEND_FAILED"
ok "★ NOL baris yatim walau kirim gagal" "$(Q "SELECT count(*) FROM pending_registrations WHERE email='"'"'$M'"'"';")" "0"
echo "    prasyarat LEPAS (kunci ada), lalu gagal di pengiriman — dua sebab berbeda, keduanya jujur"

Q "DELETE FROM pending_registrations WHERE email LIKE 'mailgate+%';" >/dev/null
echo; echo "===== $PASS sesuai, $FAIL menyimpang ====="
[ $FAIL -eq 0 ]
