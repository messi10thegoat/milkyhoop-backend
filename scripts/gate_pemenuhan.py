"""Gerbang: status pemenuhan ikut di jalur BACA faktur -- DUA SISI.

  BASIS  -- basis URL
  SISI   -- "lama" (harus MERAH) atau "baru" (harus HIJAU)

Tiga butir:
  D  detail  -- fulfillment_status + revenue_status HADIR sebagai kunci
  L  daftar  -- keempat medan status HADIR (dua di antaranya selama ini
                DIDIAMKAN response_model meski handler menaruhnya)
  N  nilai   -- nilainya COCOK dengan /fulfillments, bukan sekadar ada
"""
import json, os, sys, urllib.request, urllib.error

UA = "mh-gate-pemenuhan/1.0"
BASIS = os.environ["BASIS"].rstrip("/")
SISI = os.environ["SISI"]
assert SISI in ("lama", "baru")
# Faktur terbit dengan pengiriman PENDING -- barang bukti yang sengaja dibiarkan
# berdiri. Kalau ia hilang, gerbang ini harus BICARA, bukan diam-diam hijau.
FAKTUR = "c9b84a15-b197-4324-8e8b-c26298798d05"
MEDAN = ("fulfillment_status", "revenue_status")


def ambil(jalur, tok):
    req = urllib.request.Request(
        BASIS + jalur, headers={"Authorization": "Bearer " + tok, "User-Agent": UA}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def main():
    d = json.dumps({"email": os.environ["MH_UJI_EMAIL"],
                    "password": os.environ["MH_UJI_SANDI"]}).encode()
    req = urllib.request.Request(BASIS + "/api/auth/login", data=d,
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        tok = json.loads(r.read())["data"]["access_token"]

    hasil = []
    st, det = ambil(f"/api/sales-invoices/{FAKTUR}", tok)
    isi = det.get("data") or {}
    if st != 200:
        print(f"BARANG BUKTI HILANG: detail {FAKTUR} -> {st}. "
              "Gerbang TIDAK menyatakan apa pun.")
        return 2
    hasil.append(("D", all(m in isi for m in MEDAN),
                  f"hadir={[m for m in MEDAN if m in isi]}"))

    st2, lst = ambil("/api/sales-invoices?limit=5", tok)
    kunci = set(lst.get("items", [{}])[0].keys()) if lst.get("items") else set()
    perlu = set(MEDAN) | {"operational_status", "accounting_status"}
    hasil.append(("L", perlu <= kunci, f"hilang={sorted(perlu - kunci) or 'nihil'}"))

    st3, f = ambil(f"/api/sales-invoices/{FAKTUR}/fulfillments", tok)
    acuan = (f.get("data") or f)
    cocok = (
        st3 == 200
        and acuan.get("fulfillment_status") == isi.get("fulfillment_status")
        and acuan.get("revenue_status") == isi.get("revenue_status")
        and isi.get("fulfillment_status") is not None
    )
    hasil.append(("N", cocok,
                  f"detail={isi.get('fulfillment_status')}/{isi.get('revenue_status')} "
                  f"fulfillments={acuan.get('fulfillment_status')}/{acuan.get('revenue_status')}"))

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
