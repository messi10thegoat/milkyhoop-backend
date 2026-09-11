"""Gerbang daftar Penerimaan -- DUA SISI.

  BASIS -- basis URL (https://milkyhoop.com atau http://127.0.0.1:8003)
  SISI  -- "lama" (harus MERAH semua) atau "baru" (harus HIJAU semua)

Id jurnal di bawah diukur di DB 12 Sep 2026 -- id, BUKAN tebakan dari awalan
nomor. Kalau salah satunya hilang dari data, gerbang BICARA, bukan hijau.

  [K] KONTRAK   -- tiap baris membawa settlement_type + source_document_type/
                   id/number (KUNCI, bukan nilai). Pelajaran fulfillment_status:
                   medan yang tak dideklarasikan di response_model dibuang diam.
  [F] KARANGAN  -- baris non-kas: payment_method DAN source_type null
  [R] PEMBALIK  -- nol dari tiga jurnal pembalik ada di daftar
  [D] DETAIL    -- enam jurnal non-kas menjawab 200 dengan settlement_type
                   yang benar; tiga pembalik tetap 404 (DISENGAJA: bukan
                   pelunasan, dan tak ada di daftar untuk diketuk)
  [C] COCOK     -- Σ total_amount baris posted
                   = total_received + total_settled_noncash
  [N] NORMAL    -- penerimaan SUNGGUHAN: detail menjawab settlement_type =
                   RECEIVE_PAYMENT, sama dengan barisnya di daftar. Butir ini
                   ditambahkan karena tambalan pertamaku hanya mengisi jalur
                   cadangan -- jalur normal akan pulang null, dan daftar serta
                   detail berbeda pendapat tentang dokumen yang sama.
"""
import json, os, sys, urllib.request, urllib.error

UA = "mh-gate-terima/1.0"
BASIS = os.environ["BASIS"].rstrip("/")
SISI = os.environ["SISI"]
assert SISI in ("lama", "baru"), SISI

NONKAS = {
    "be109c77-240a-44e3-b75c-809fe34a22c6": "CREDIT_NOTE",          # CN-2609-0001 (dok CN-2609-0006)
    "d22e043a-85e4-41c7-90b3-e01d2b643d49": "DEPOSIT_APPLICATION",  # DA-2608-0001
    "928fc5f8-f699-45ff-b118-00df1947f446": "DEPOSIT_APPLICATION",  # DA-2609-0001
    "24ac625d-f32b-40cf-9bc6-f051b669866d": "DEPOSIT_APPLICATION",  # DA-2609-0002
    "16e19b96-3103-4cb3-a96b-7d1089d5b820": "DEPOSIT_APPLICATION",  # DA-2609-0003
    "737f0837-1b52-4204-aea6-b340b6735eca": "DEPOSIT_APPLICATION",  # DA-2609-0004
}
PEMBALIK = {
    "e1b720f1-0ebf-4236-910b-c09e0b62ac0e",  # REV-2609-0002
    "1ad9d096-5c60-487a-ad00-e3dd49df1898",  # REV-2609-0003
    "ada6854e-2c33-4cb8-aa6e-d33f064c254e",  # REV-2609-0004
}
KUNCI = ("settlement_type", "source_document_type",
         "source_document_id", "source_document_number")


def ambil(jalur, tok):
    req = urllib.request.Request(BASIS + jalur, headers={
        "Authorization": "Bearer " + tok, "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def masuk():
    d = json.dumps({"email": os.environ["MH_UJI_EMAIL"],
                    "password": os.environ["MH_UJI_SANDI"]}).encode()
    req = urllib.request.Request(BASIS + "/api/auth/login", data=d, headers={
        "Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["data"]["access_token"]


def main():
    tok = masuk()
    baris, skip = [], 0
    while True:
        st, d = ambil(f"/api/receive-payments?limit=100&skip={skip}", tok)
        if st != 200:
            print(f"DAFTAR GAGAL {st} -- gerbang TIDAK menyatakan apa pun.")
            return 2
        baris += d.get("items", [])
        if not d.get("has_more"):
            break
        skip += 100
    per_id = {b["id"]: b for b in baris}
    hasil = []

    # [K]
    tanpa = [b["payment_number"] for b in baris if not all(k in b for k in KUNCI)]
    hasil.append(("K", not tanpa, f"{len(baris)} baris; tanpa kunci kontrak: {len(tanpa)}"))

    # [F]
    nonkas_di_daftar = [per_id[i] for i in NONKAS if i in per_id]
    karang = [b["payment_number"] for b in nonkas_di_daftar
              if b.get("payment_method") is not None or b.get("source_type") is not None]
    hasil.append(("F", bool(nonkas_di_daftar) and not karang,
                  f"non-kas di daftar {len(nonkas_di_daftar)}/6; masih mengarang: {karang or 'nihil'}"))

    # [R]
    bocor = sorted(i[:8] for i in PEMBALIK if i in per_id)
    hasil.append(("R", not bocor, f"pembalik di daftar: {len(bocor)}/3 {bocor or ''}"))

    # [D]
    salah = []
    for i, jenis in NONKAS.items():
        st, d = ambil(f"/api/receive-payments/{i}", tok)
        dat = d.get("data") or {}
        if st != 200 or dat.get("settlement_type") != jenis or dat.get("payment_method") is not None:
            salah.append(f"{i[:8]}:{st}/{dat.get('settlement_type')}")
    rev_bukan_404 = []
    for i in PEMBALIK:
        st, _ = ambil(f"/api/receive-payments/{i}", tok)
        if st != 404:
            rev_bukan_404.append(f"{i[:8]}:{st}")
    hasil.append(("D", not salah and not rev_bukan_404,
                  f"non-kas salah: {salah or 'nihil'}; pembalik bukan-404: {rev_bukan_404 or 'nihil'}"))

    # [C]
    st, ring = ambil("/api/receive-payments/summary", tok)
    rd = ring.get("data") or {}
    jumlah_daftar = sum(int(b.get("total_amount") or 0) for b in baris if b.get("status") == "posted")
    tr, tn = rd.get("total_received"), rd.get("total_settled_noncash")
    cocok = tr is not None and tn is not None and jumlah_daftar == tr + tn
    hasil.append(("C", cocok,
                  f"Σ daftar posted={jumlah_daftar:,} · total_received={tr} · total_settled_noncash={tn}"
                  + (f" · selisih={jumlah_daftar - tr - tn:,}" if tr is not None and tn is not None
                     else f" · selisih-tanpa-nama={jumlah_daftar - (tr or 0):,}")))

    # [N]
    rcv = [b for b in baris if b.get("status") == "posted" and b["id"] not in NONKAS
           and b["id"] not in PEMBALIK]
    if not rcv:
        hasil.append(("N", False, "tak ada penerimaan sungguhan di daftar -- tak bisa diuji"))
    else:
        salah_n = []
        for b in rcv[:3]:
            st, d = ambil(f"/api/receive-payments/{b['id']}", tok)
            dat = d.get("data") or {}
            if st != 200 or dat.get("settlement_type") != "RECEIVE_PAYMENT":
                salah_n.append(f"{b['payment_number']}:{st}/{dat.get('settlement_type')}")
        hasil.append(("N", not salah_n,
                      f"diuji {min(3, len(rcv))} dari {len(rcv)}; salah: {salah_n or 'nihil'}"))

    print(f"=== {SISI.upper()} @ {BASIS}")
    for k, ok, ket in hasil:
        print(f"  [{k}] {'HIJAU' if ok else 'MERAH'}  {ket}")
    if SISI == "baru":
        ok = all(o for _, o, _ in hasil)
        print("PUTUSAN:", "LULUS" if ok else "GAGAL")
        return 0 if ok else 1
    merah = all(not o for _, o, _ in hasil)
    print("PUTUSAN:", "KONTROL MERAH SAH" if merah else
          "KONTROL TIDAK MEMERAH -- gerbang tak membuktikan apa pun")
    return 0 if merah else 1


sys.exit(main())
