# Header = HTTPMessage (TAK peka huruf; starlette mengirim nama huruf kecil). JANGAN dict(...) -- 2 Nov: replay=None merah palsu.
# Uji nyata Milkybook v2 di KAOS lewat rute ASLI (05 §7 chaos, tingkat BE). Izin pemilik LANGSUNG 2 Nov: flag kaos ON, login API,
# LLM <= $0,80 (kunci MILKYBOOK di server, lewat /v2/tafsir), tulis TES E2E lalu dibatalkan (net 0). Kredensial dari env, tak dicetak.
import json, os, sys, time, urllib.error, urllib.request, uuid

BASIS = "http://127.0.0.1:8001"
V2 = "/api/milkybook/v2"
MAKS_TAFSIR = 8
hasil = []


def panggil(metode, jalur, tok=None, body=None, kunci=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"User-Agent": "milkyhoop-mb2-ch/1.0"}
    if data:
        h["Content-Type"] = "application/json"
    if tok:
        h["Authorization"] = "Bearer " + tok
    if kunci:
        h["X-Idempotency-Key"] = kunci
    req = urllib.request.Request(BASIS + jalur, data=data, headers=h, method=metode)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers, int((time.monotonic() - t0) * 1000)
    except urllib.error.HTTPError as e:
        try:
            isi = json.loads(e.read() or b"{}")
        except Exception:
            isi = {}
        return e.code, isi, e.headers, int((time.monotonic() - t0) * 1000)


def galat(d):
    x = d.get("detail")
    return f"code={x.get('code')} kalimat=«{x.get('message')}»" if isinstance(x, dict) else f"detail=«{x}»"


def catat(kode, ok, ket):
    hasil.append((kode, ok))
    print(f"{'LULUS' if ok else 'GAGAL'} {kode}: {ket}", flush=True)


def masuk():
    s, d, _, _ = panggil("POST", "/api/auth/login", body={"email": os.environ["MH_UJI_EMAIL"], "password": os.environ["MH_UJI_SANDI"]})
    if s != 200:
        print(f"LOGIN GAGAL status={s}"); sys.exit(2)
    return d.get("data", d)["access_token"]


def tafsir(tok, kode, tahap, teks, terima, syarat=None):
    s, d, _, ms = panggil("POST", f"{V2}/tafsir", tok, {"teks": teks, "tahap": tahap})
    if s != 200:
        catat(kode, False, f"tafsir status={s} {str(d)[:200]}"); return d
    alat = [c.get("name") for c in d.get("tool_calls") or []]
    ok = d.get("intent") in terima and d.get("sumber") == "llm" and (syarat(d, alat) if syarat else True)
    catat(kode, ok, f"«{teks}» tahap={tahap} -> intent={d.get('intent')} slot={d.get('slot')} sumber={d.get('sumber')} "
                    f"alat={alat} {ms}ms | {d.get('reply')}")
    return d


def utama():
    tok = masuk()
    print("login OK (kredensial owner kaos; sesi web kaos ikut keluar)")

    # --- baca: pelanggan & barang nyata kaos (dipakai skenario berikut)
    s, bar, _, _ = panggil("GET", f"{V2}/barang?q=kaos&batas=3", tok)
    catat("R1-barang", s == 200 and bar.get("items"), f"status={s} n={len(bar.get('items') or [])} contoh={[(b['nama'], b['harga']) for b in (bar.get('items') or [])[:3]]}")
    s, pel, _, _ = panggil("GET", f"{V2}/pelanggan?batas=3", tok)
    catat("R2-pelanggan", s == 200, f"status={s} n={len(pel.get('items') or [])}")
    nama_pel = ((pel.get("items") or [{}])[0].get("nama") or "Rocky").split()[0].lower()

    # --- CH tingkat BE lewat lapisan LLM (aturan FE belum ada; tafsir = cadangan yang sama)
    n = 0
    for kode, tahap, teks, terima, syarat in [
        ("CH04", "cust", f"{nama_pel} udah bayar belum?", {"answer_question"}, lambda d, a: "open_invoices" in a or "search_customers" in a),
        ("CH05", "cust", "omzet bulan ini gimana", {"answer_question", "clarify"}, lambda d, a: d.get("slot") != "customer"),
        ("CH06", "cust", "toko sebelah yang kemarin datang ke workshop", {"clarify"}, None),
        ("CH07", "cust", "Sudah ada pelanggan atas nama Inggrid belum ya", {"check_exists", "answer_question"}, lambda d, a: "search_customers" in a),
        ("CH09", "more", "ga jadi", {"switch_task", "clarify"}, None),
        ("CH10", "more", "harga hoodie navy berapa?", {"answer_question"}, lambda d, a: "search_items" in a),
        ("CH03L", "more", "catat bensin 50rb", {"switch_task"}, None),
    ]:
        if n >= MAKS_TAFSIR:
            break
        tafsir(tok, kode, tahap, teks, terima, syarat); n += 1

    # --- CH03 catat biaya: pratinjau -> simpan -> ulang kunci sama -> isi beda kunci sama -> void (net 0)
    s, p, _, _ = panggil("GET", f"{V2}/biaya/pratinjau?untuk=bensin&jumlah=50000", tok)
    catat("CH03-pratinjau", s == 200 and p.get("dari") and p.get("akun"),
          f"status={s} dari={p.get('dari', {}) and p['dari'].get('nama')} akun={p.get('akun', {}) and p['akun'].get('nama')} siap={p.get('siap')} kurang={p.get('kurang')}")
    if s == 200 and p.get("siap"):
        kunci = f"tes-e2e-ch03-{uuid.uuid4()}"
        badan = {"untuk": "TES E2E bensin CH03", "jumlah": 50000, "dari_id": p["dari"]["id"], "akun_id": p["akun"]["id"]}
        s1, d1, _, _ = panggil("POST", f"{V2}/biaya", tok, badan, kunci)
        eid = (d1.get("data") or {}).get("id")
        catat("CH03-simpan", s1 == 200 and eid, f"status={s1} nomor={(d1.get('data') or {}).get('expense_number')} pesan={d1.get('message')}")
        s2, d2, h2, _ = panggil("POST", f"{V2}/biaya", tok, badan, kunci)
        catat("CH03-tekan-dua-kali", s2 == 200 and (d2.get("data") or {}).get("id") == eid and h2.get("X-Idempotent-Replay") == "true",
              f"status={s2} id_sama={(d2.get('data') or {}).get('id') == eid} replay={h2.get('X-Idempotent-Replay')}")
        s3, d3, _, _ = panggil("POST", f"{V2}/biaya", tok, {**badan, "jumlah": 50001}, kunci)
        catat("CH03-kunci-sama-isi-beda", s3 == 409, f"status={s3} {galat(d3)}")
        s4, d4, _, _ = panggil("POST", f"{V2}/biaya", tok, {k: v for k, v in badan.items() if k != "akun_id"}, f"{kunci}-x")
        catat("CH03-akun-kosong-422", s4 == 422 and isinstance(d4.get("detail"), dict) and d4["detail"].get("code") == "PILIH_DARI_AKUN_DULU", f"status={s4} {galat(d4)}")
        if eid:
            s5, d5, _, _ = panggil("POST", f"/api/expenses/{eid}/void", tok, {"reason": "TES E2E CH03 dibatalkan (net 0)"})
            catat("CH03-void", s5 == 200, f"status={s5}")

    # --- CH08 parkir: percakapan + draf + parkir, lalu muncul di Riwayat
    s, pc, _, _ = panggil("POST", f"{V2}/percakapan", tok, {"judul": "TES E2E CH08 Penawaran"})
    pid = pc.get("id")
    if pid:
        draf = {"stage": "more", "customer": {"name": "TES E2E CH08", "isNew": True}, "lines": [{"name": "Kaos", "qty": 2}]}
        s2, d2, _, _ = panggil("PUT", f"{V2}/percakapan/{pid}", tok, {"versi": pc.get("versi", 1), "draf": None, "parkir": [draf]})
        s3, d3, _, _ = panggil("PUT", f"{V2}/percakapan/{pid}", tok, {"versi": pc.get("versi", 1), "draf": draf, "parkir": []})
        s4, d4, _, _ = panggil("GET", f"{V2}/percakapan", tok)
        ada = [x for x in d4.get("items") or [] if x.get("id") == pid]
        catat("CH08-parkir-riwayat", s2 == 200 and s3 == 409 and ada and ada[0].get("parkir") == 1,
              f"simpan={s2} versi_basi={s3} {galat(d3)} di_riwayat={bool(ada)} parkir={ada and ada[0].get('parkir')}")
    else:
        catat("CH08-parkir-riwayat", False, f"buat percakapan status={s}")

    # --- simpan penawaran atomik: pelanggan baru TES E2E + barang nyata; tekan dua kali = 1 penawaran; lalu hapus (net 0)
    b0 = (bar.get("items") or [None])[0]
    if b0:
        kunci = f"tes-e2e-q-{uuid.uuid4()}"
        badan = {"master_baru": [{"kind": "customer", "temp_id": "c-baru", "data": {"name": "TES E2E CH Pelanggan Baru"}}],
                 "penawaran": {"quote_date": time.strftime("%Y-%m-%d"), "customer_id": "c-baru", "customer_name": "TES E2E CH Pelanggan Baru",
                               "items": [{"item_id": b0["id"], "description": b0["nama"], "quantity": 2, "unit_price": int(b0["harga"] or 1000)}]}}
        s1, d1, _, _ = panggil("POST", f"{V2}/penawaran", tok, badan, kunci)
        q = d1.get("data") or {}
        cid = next((m["id"] for m in q.get("master_baru") or [] if m["kind"] == "customer"), None)
        s2, d2, h2, _ = panggil("POST", f"{V2}/penawaran", tok, badan, kunci)
        catat("Q-atomik-tekan-dua-kali", s1 == 200 and q.get("id") and (d2.get("data") or {}).get("id") == q.get("id"),
              f"status={s1}/{s2} nomor={q.get('quote_number')} pelanggan_baru={bool(cid)} replay={h2.get('X-Idempotent-Replay')}")
        if q.get("id"):
            s3, _, _, _ = panggil("DELETE", f"/api/quotes/{q['id']}", tok)
            catat("Q-hapus", s3 == 200, f"status={s3}")
        if cid:
            s4, _, _, _ = panggil("DELETE", f"/api/customers/{cid}", tok)
            catat("Q-hapus-pelanggan", s4 == 200, f"status={s4}")

    n_ok = sum(1 for _, ok in hasil if ok)
    print(f"RINGKASAN: {n_ok}/{len(hasil)} lulus; panggilan tafsir={n}")
    return 0 if n_ok == len(hasil) else 1


sys.exit(utama())
