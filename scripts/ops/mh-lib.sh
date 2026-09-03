#!/usr/bin/env bash
# mh-lib.sh — resolusi kontainer + probe HTTP bersama untuk mh-restart/mh-recreate.
#
# KENAPA BERKAS INI ADA
# 2026-09-03: `mh-restart.sh api_gateway` GAGAL dengan "kontainer 'api_gateway'
# tidak ada", karena mh-restart menuntut NAMA KONTAINER sedangkan mh-recreate
# menuntut NAMA SERVICE. Dua alat bersaudara, dua bahasa berbeda -- dan yang
# salah menebak dihukum kegagalan, bukan diterjemahkan.
#
# Nama kontainer di mesin ini memang TIDAK konsisten: api_gateway dan minio
# tanpa sufiks `-1`, sisanya dengan `-1`. Menebak dari nama service karena itu
# selalu rapuh; `docker compose ps -q` adalah satu-satunya sumber yang sahih.
#
# ⚠️ PELAJARAN YANG LEBIH MAHAL DARIPADA BUG-NYA SENDIRI
# Ketika restart itu gagal, gerbang "LIVE" tiket T226 tetap melaporkan 7/7 --
# karena harness in-process (TestClient) membaca KODE SUMBER, bukan kontainer
# yang berjalan. Jadi ia hijau atas kontainer yang belum di-restart sama
# sekali. Hijau yang membuktikan hal yang salah.
#
# ATURAN: gerbang "LIVE" WAJIB lewat HTTP NYATA ke kontainer.
# Harness in-process membuktikan kode sumber benar; ia TIDAK BISA membuktikan
# kontainer menyajikannya. Untuk klaim "sudah live", pakai curl ke port, lalu
# periksa efeknya di DB.
set -euo pipefail

TREE=${TREE:-/root/milkyhoop-dev}

# Probe HTTP per-service. Service yang TIDAK ada di sini sengaja tak diberi
# probe palsu: lebih baik berkata "tak ada probe" daripada meminjam healthz
# milik proses lain dan melaporkan sehat atas sesuatu yang mati (kelas
# kegagalan yang sudah pernah terjadi pada recreate `frontend`).
mh_probe_url() {
    # Hanya service yang probe-nya TERBUKTI 200. chatbot_service (7003) dan
    # ragcrud_service (7001) sengaja TIDAK diberi probe: /health di kedua
    # port itu DIUJI 2026-09-03 dan menjawab 000 (tak ada HTTP dari host).
    # Memasangnya akan membuat kedua service MUSTAHIL di-restart lewat
    # skrip ini -- probe yang selalu merah.
    case "$1" in
        api_gateway)      echo "http://localhost:8001/healthz" ;;
        frontend)         echo "http://localhost:3001/BUILD_INFO.json" ;;
        *)                echo "" ;;
    esac
}

# Terima NAMA SERVICE compose ATAU NAMA KONTAINER penuh. Menetapkan MH_CTR
# dan MH_SVC.
mh_resolve() {
    local arg="$1" id
    if [ -z "$arg" ]; then
        echo "GAGAL: argumen kosong." >&2
        mh_daftar_kontainer
        exit 2
    fi

    # (a) Sudah nama kontainer? `docker container inspect`, BUKAN `docker
    # inspect` -- yang terakhir juga mencocokkan IMAGE bernama sama, sehingga
    # tebakan "sukses" atas sebuah image lalu `docker logs` gagal.
    if docker container inspect "$arg" >/dev/null 2>&1; then
        MH_CTR="$arg"
        MH_SVC="${arg#milkyhoop-dev-}"; MH_SVC="${MH_SVC%-1}"
        return 0
    fi

    # (b) Nama service: tanya compose, jangan menebak sufiks.
    if id=$(cd "$TREE" && docker compose ps -q "$arg" 2>/dev/null) && [ -n "$id" ]; then
        MH_CTR=$(docker container inspect -f '{{.Name}}' "$id" 2>/dev/null | sed 's|^/||')
        [ -n "$MH_CTR" ] || MH_CTR="$id"
        MH_SVC="$arg"
        return 0
    fi

    echo "GAGAL: '$arg' bukan nama service compose maupun nama kontainer yang ada." >&2
    mh_daftar_kontainer
    exit 2
}

mh_daftar_kontainer() {
    echo "" >&2
    echo "Service compose yang tersedia (service -> kontainer):" >&2
    (cd "$TREE" && docker compose ps --format '  {{.Service}} -> {{.Name}}' 2>/dev/null) >&2 \
        || echo "  (tak bisa membaca compose)" >&2
}

mh_started_at() {
    docker container inspect -f '{{.State.StartedAt}}' "$1" 2>/dev/null || echo "?"
}

# Probe HTTP NYATA. Mengembalikan 0 hanya kalau benar-benar 200.
mh_tunggu_probe() {
    local svc="$1" url code i
    url=$(mh_probe_url "$svc")
    if [ -z "$url" ]; then
        echo "CATATAN: tak ada probe HTTP yang ditetapkan untuk service '$svc'." >&2
        echo "         Kontainer berjalan, tapi KESEHATANNYA TIDAK DIBUKTIKAN." >&2
        return 3
    fi
    for i in $(seq 1 12); do
        code=$(curl -s -o /dev/null -w '%{http_code}' "$url" 2>/dev/null) || code=000
        if [ "$code" = "200" ]; then
            echo "probe $url: 200 (percobaan $i)"
            return 0
        fi
        sleep 5
    done
    echo "PERINGATAN: $url belum 200 sesudah ~60 detik (terakhir: $code)." >&2
    return 1
}

mh_arsip_log() {
    local ctr="$1" suffix="${2:-}" dir=/root/logs out
    mkdir -p "$dir"
    out="$dir/${ctr}-$(date +%s)${suffix}.log"
    if ! docker logs "$ctr" > "$out" 2>&1; then
        echo "GAGAL: tak bisa mengarsipkan log '$ctr'. DIBATALKAN." >&2
        rm -f "$out"
        exit 1
    fi
    echo "arsip : $out ($(stat -c%s "$out") byte)"
}
