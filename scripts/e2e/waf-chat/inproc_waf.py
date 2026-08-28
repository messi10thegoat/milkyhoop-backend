#!/usr/bin/env python3
"""GATE T146 in-process — menguji WAFMiddleware dari BERKAS yang ditunjuk.

ARTEFAK GATE. Bukan kode produk. TIDAK menyentuh apa pun yang dilayani
produksi: memuat modul middleware dari path berkas lewat importlib, memasangnya
di aplikasi Starlette kosong milik gate ini sendiri, dan memanggilnya lewat
TestClient (in-process, nol soket keluar).

Pakai:  python3 inproc_waf.py <path/ke/waf_middleware.py> <LABEL>
Keluar 0 bila semua kasus sesuai harapan, 1 bila ada yang tidak.
"""
import importlib.util
import sys
import uuid

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

SRC = sys.argv[1]
LABEL = sys.argv[2] if len(sys.argv) > 2 else SRC

spec = importlib.util.spec_from_file_location("waf_under_test_" + uuid.uuid4().hex, SRC)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


async def sink(request):
    return PlainTextResponse("SINK_OK")


app = Starlette(routes=[Route("/{rest:path}", sink, methods=["GET", "POST"])])
app.add_middleware(mod.WAFMiddleware, enabled=True, strict_mode=False)
client = TestClient(app)

CHAT_MSG = "/api/v3/chat/message"
CHAT_STREAM = "/api/v3/chat/message/stream"
CHAT_EDIT = "/api/v3/chat/action/edit"

DASH = "--"


def chat_body(text):
    return {"conversation_id": "00000000-0000-4000-8000-000000000000", "text": text}


# (id, deskripsi, harapan: "PASS"=lolos WAF / "BLOCK"=diblokir, fungsi pemanggil)
CASES = []


def case(cid, desc, expect, fn):
    CASES.append((cid, desc, expect, fn))


# --- SISI MERAH: prosa manusia ber-"--" pada TIGA path chat ---
case("R1", "chat /message/stream prosa ber-dashdash", "PASS",
     lambda: client.post(CHAT_STREAM, json=chat_body("halo, berapa saldo kas " + DASH + " sekarang")))
case("R2", "chat /message prosa ber-dashdash (kalimat tak berbahaya)", "PASS",
     lambda: client.post(CHAT_MSG, json=chat_body("tolong jelaskan laporan " + DASH + " yang mana yang cocok")))
case("R3", "chat /action/edit prosa ber-dashdash", "PASS",
     lambda: client.post(CHAT_EDIT, json={"pending_action_id": "x", "text": "ubah harga " + DASH + " jadi 120000"}))

# --- KONTROL POSITIF: kalimat sama TANPA dashdash harus selalu lolos ---
case("C1", "chat /message/stream kalimat sama tanpa dashdash", "PASS",
     lambda: client.post(CHAT_STREAM, json=chat_body("halo, berapa saldo kas xx sekarang")))

# --- PAGAR: "--" TIDAK dicabut di luar tiga path itu ---
case("G1a", "non-chat /api/auth/login body dashdash", "BLOCK",
     lambda: client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "x " + DASH + " y"}))
case("G1b", "non-chat path lain body dashdash", "BLOCK",
     lambda: client.post("/api/__gate_nonexistent__", json={"q": "a " + DASH + " b"}))
case("G6", "chat_history /api/v3/chat/history body dashdash (bukan prefix)", "BLOCK",
     lambda: client.post("/api/v3/chat/history", json={"q": "a " + DASH + " b"}))
case("G7", "/api/v3/chat/confirm body dashdash (bukan prefix)", "BLOCK",
     lambda: client.post("/api/v3/chat/confirm", json={"q": "a " + DASH + " b"}))
case("G10", "/api/v3/chat/message/streamXX (bukan path eksak)", "BLOCK",
     lambda: client.post(CHAT_STREAM + "XX", json=chat_body("a " + DASH + " b")))

# --- PAGAR: lapisan lain tak tersentuh, bahkan pada path chat ---
# KOREKSI PREMIS (terbukti empiris): str(QueryParams("q=a -- b")) == "q=a+--+b",
# spasi di-urlencode jadi "+", sehingga pola :54 (--\s*$|--\s+) memang TIDAK
# PERNAH cocok untuk kasus ini di lapisan URL — baik sebelum maupun sesudah fix.
# Jadi harapannya PASS, dan nilainya sebagai pagar adalah: harus IDENTIK di kedua
# versi. Pagar URL yang benar-benar hidup diuji oleh G8c.
case("G8", "dashdash di QUERY URL path chat (pra-ada: lolos di kedua versi)", "PASS",
     lambda: client.post(CHAT_MSG + "?q=a%20" + DASH + "%20b", json=chat_body("halo")))
# KOREKSI PREMIS KEDUA (terbukti empiris): sebab yang sama membuat lapisan URL
# BUTA terhadap SETIAP pola yang butuh spasi -- str(QueryParams) mengubah spasi
# jadi "+", sehingga "UNION ALL SELECT" pun tidak cocok. Ini KELEMAHAN YANG SUDAH
# ADA di master, IDENTIK sebelum dan sesudah fix, dan DI LUAR cakupan T146.
# Dicatat di sini supaya tidak hilang. Pagar URL yang hidup dibuktikan oleh G8c.
case("G8b", "SQLi berspasi di QUERY URL (kelemahan PRA-ADA, sama di 2 versi)", "PASS",
     lambda: client.post(CHAT_MSG + "?q=UNION%20ALL%20SELECT%20NULL", json=chat_body("halo")))
case("G8c", "dashdash di AKHIR query path chat (pola --\\s*$ di lapisan URL)", "BLOCK",
     lambda: client.post(CHAT_MSG + "?q=a" + DASH, json=chat_body("halo")))
case("G9", "dashdash di HEADER pada path chat (lapisan header)", "BLOCK",
     lambda: client.post(CHAT_MSG, json=chat_body("halo"), headers={"X-Gate-Probe": "a " + DASH + " b"}))

# --- PAGAR: ancaman NYATA di jalur chat tetap diblokir ---
case("G2", "SQLi telanjang di body chat", "BLOCK",
     lambda: client.post(CHAT_MSG, json=chat_body("1' OR '1'='1' UNION ALL SELECT NULL" + DASH)))
case("G2b", "DROP TABLE di body chat", "BLOCK",
     lambda: client.post(CHAT_MSG, json=chat_body("lalu DROP TABLE journals ya")))
case("G2c", "INSERT INTO di body chat", "BLOCK",
     lambda: client.post(CHAT_MSG, json=chat_body("jalankan INSERT INTO journals nih")))
case("G3a", "path traversal di body chat", "BLOCK",
     lambda: client.post(CHAT_MSG, json=chat_body("coba ../ ini")))
case("G3b", "blok-komentar /* */ di body chat", "BLOCK",
     lambda: client.post(CHAT_MSG, json=chat_body("coba /* ini */ ya")))
case("G3c", "XSS <script> di body chat", "BLOCK",
     lambda: client.post(CHAT_MSG, json=chat_body("<script>alert(1)</script>")))

# --- PAGAR: header & multipart seperti semula ---
case("G4", "header pola SQLi pada /api/items", "BLOCK",
     lambda: client.get("/api/items", headers={"X-Gate-Probe": "1' OR '1'='1' UNION ALL SELECT NULL" + DASH}))
case("G4c", "kontrol header jinak pada /api/items", "PASS",
     lambda: client.get("/api/items", headers={"X-Gate-Probe": "benign"}))
case("G5", "multipart CSV bank ber-dashdash", "PASS",
     lambda: client.post(
         "/api/__gate_nonexistent__",
         content=b"--BND\r\nContent-Disposition: form-data; name=\"file\"; filename=\"bank.csv\"\r\n"
                 b"Content-Type: text/csv\r\n\r\ntanggal,ket,jumlah\r\n2026-01-01,TRF -- masuk,1000\r\n--BND--\r\n",
         headers={"Content-Type": "multipart/form-data; boundary=BND"},
     ))

print("=== IN-PROCESS WAF GATE :: %s ===" % LABEL)
print("    sumber: %s" % SRC)
ok = 0
bad = 0
for cid, desc, expect, fn in CASES:
    r = fn()
    blocked = r.status_code == 403 and "WAF" in r.text
    actual = "BLOCK" if blocked else "PASS"
    verdict = "OK " if actual == expect else "BEDA"
    if actual == expect:
        ok += 1
    else:
        bad += 1
    print("%-5s %-4s harap=%-5s dapat=%-5s http=%-4s bytes=%-5s  %s"
          % (verdict, cid, expect, actual, r.status_code, len(r.content), desc))
print("--- %s: sesuai=%d  TIDAK-sesuai=%d  total=%d ---" % (LABEL, ok, bad, len(CASES)))
sys.exit(0 if bad == 0 else 1)
