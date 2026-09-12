"""Gerbang log audit tingkat dokumen -- DUA SISI.

Dijalankan terhadap SATU basis URL dan menyatakan LAMA atau BARU. Semua angka
acuan diambil dari DB produksi 10 Sep 2026 dan disebut di sini supaya kalau
datanya bergeser, gerbangnya yang bicara -- bukan diam-diam berubah arti.

  BASIS   -- basis URL (mis. https://milkyhoop.com atau http://127.0.0.1:8002)
  SISI    -- "lama" (harus MERAH di keempat butir) atau "baru" (harus HIJAU)

Empat butir, masing-masing kontrol merahnya sendiri:
  T  pagar tenant     -- nol surel milik tenant lain di sapuan penuh
  F  penyaring        -- dua NILAI berbeda -> himpunan BERBEDA (bukan sekadar 200)
  C  atribusi hapus   -- entity_number + pelaku sampai ke respons, bukan cuma DB
  H  riwayat entitas  -- /audit-logs/entity/{t}/{id} menjawab 200, bukan 500
"""
import json
import os
import sys
import urllib.request

# Cloudflare menolak User-Agent bawaan urllib dengan 403 sebelum permintaan
# sampai ke aplikasi -- 403 itu milik ALAT, bukan produk.
UA = "mh-gate-audit/1.0"
BASIS = os.environ["BASIS"].rstrip("/")
SISI = os.environ["SISI"]
assert SISI in ("lama", "baru"), SISI

# Surel milik TENANT LAIN (grapgrap-manado, subbidel-kolsani, adhita-ariyani).
# Diukur lewat JOIN audit_logs -> User: 7 + 4 + 2 = 13 baris.
SUREL_ASING = {
    "grapmanado@gmail.com",
    "subbidel@kolsani.com",
    "adhita.nicolyn@gmail.com",
}
# Dokumen terhapus yang barisnya ADA di audit_logs, tenant kaos-biru.
DOK_TIPE = "sales_orders"
DOK_ID = "b84d94cd-9399-4f87-81a6-d68c0400586f"
DOK_NOMOR = "SO-2609-0213"
# Jumlah baris DOCUMENT_DELETED per entity_type saat gerbang ini ditulis
# (11 Sep 2026). INFORMATIF SAJA -- sengaja TIDAK di-assert; lihat catatan di
# lengan [F]. Disimpan supaya pergeserannya bisa dibaca, bukan dipatok.
HARAP_SO, HARAP_QUOTE = 79, 47


def panggil(jalur, tok):
    req = urllib.request.Request(
        BASIS + jalur,
        headers={"Authorization": "Bearer " + tok, "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"_body": (e.read() or b"")[:200].decode("utf8", "replace")}


def masuk():
    data = json.dumps(
        {"email": os.environ["MH_UJI_EMAIL"], "password": os.environ["MH_UJI_SANDI"]}
    ).encode()
    req = urllib.request.Request(
        BASIS + "/api/auth/login", data=data,
        headers={"Content-Type": "application/json", "User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read())
    return d.get("data", d)["access_token"]


def main():
    tok = masuk()
    hasil = []

    # --- T: pagar tenant --------------------------------------------------
    surel = set()
    skip, total = 0, None
    while True:
        st, d = panggil(f"/api/audit-logs?limit=200&skip={skip}", tok)
        if st != 200:
            hasil.append(("T", False, f"sapuan gagal {st}"))
            break
        total = d.get("total")
        for it in d.get("items", []):
            if it.get("user_email"):
                surel.add(it["user_email"])
        if not d.get("has_more") or not d.get("items"):
            break
        skip += 200
        if skip > 4000:
            break
    else:
        pass
    if not any(h[0] == "T" for h in hasil):
        bocor = surel & SUREL_ASING
        hasil.append(
            ("T", not bocor,
             f"total={total} surel_unik={len(surel)} asing={sorted(bocor) or 'nol'}")
        )

    # --- F: penyaring dua NILAI berbeda ------------------------------------
    st1, d1 = panggil(f"/api/audit-logs?entity_type={DOK_TIPE}&limit=200", tok)
    st2, d2 = panggil("/api/audit-logs?entity_type=quotes&limit=200", tok)
    id1 = {i["id"] for i in d1.get("items", [])}
    id2 = {i["id"] for i in d2.get("items", [])}
    tipe1 = {i.get("entity_type") for i in d1.get("items", [])}
    tipe2 = {i.get("entity_type") for i in d2.get("items", [])}
    # JUMLAH BARIS TIDAK DI-ASSERT LAGI. Versi pertama menuntut
    # `len(id1) == 79 and len(id2) == 47` -- jumlah yang diukur 11 Sep 2026.
    # Penghapusan dokumen terus berjalan (12 Sep: 83 dan 51, dari penghapusan
    # biasa oleh pemilik), jadi patokan itu MEMERAH ATAS PERILAKU YANG BENAR
    # dan akan memerah lagi tiap kali satu dokumen dihapus. Gerbang yang merah
    # atas keadaan benar adalah gerbang yang orang belajar abaikan.
    #
    # Yang sebenarnya dijaga lengan ini: PENYAIRNG BENAR-BENAR MENYARING.
    # Itu sifat, bukan bilangan -- dan sifatnya tetap merah di kode lama, yang
    # mengabaikan penyaring sehingga kedua permintaan mengembalikan baris LOGIN
    # yang sama (himpunan identik, entity_type terbaca None).
    lulus_f = (
        st1 == st2 == 200
        and id1 and id2                           # dua-duanya berisi
        and not (id1 & id2)                       # himpunan BERBEDA
        and tipe1 == {DOK_TIPE}                   # yang diminta, yang datang
        and tipe2 == {"quotes"}                   # dua arah, bukan satu
    )
    hasil.append(
        ("F", lulus_f,
         f"{DOK_TIPE}={len(id1)} quotes={len(id2)} (jumlah INFORMATIF, tak "
         f"di-assert) irisan={len(id1 & id2)} tipe_terbaca={tipe1 or 'kosong'}/"
         f"{tipe2 or 'kosong'}")
    )

    # --- C: atribusi penghapusan sampai ke RESPONS --------------------------
    st3, d3 = panggil(f"/api/audit-logs?entity_id={DOK_ID}&limit=50", tok)
    baris = [i for i in d3.get("items", []) if i.get("entity_id") == DOK_ID]
    lulus_c = (
        st3 == 200
        and len(baris) == 1
        and baris[0].get("entity_number") == DOK_NOMOR
        and baris[0].get("user_id")
        and baris[0].get("action") == "DOCUMENT_DELETED"
    )
    hasil.append(
        ("C", lulus_c,
         f"cocok={len(baris)} nomor={baris[0].get('entity_number') if baris else None} "
         f"pelaku={'ada' if baris and baris[0].get('user_id') else 'NIHIL'}")
    )

    # --- H: riwayat per-entitas --------------------------------------------
    st4, d4 = panggil(f"/api/audit-logs/entity/{DOK_TIPE}/{DOK_ID}", tok)
    isi = (d4.get("data") or {}) if st4 == 200 else {}
    lulus_h = st4 == 200 and isi.get("entity_number") == DOK_NOMOR and isi.get("history")
    hasil.append(
        ("H", lulus_h,
         f"http={st4} nomor={isi.get('entity_number')} baris={len(isi.get('history') or [])}")
    )

    print(f"=== {SISI.upper()} @ {BASIS}")
    for kode, ok, ket in hasil:
        print(f"  [{kode}] {'HIJAU' if ok else 'MERAH'}  {ket}")

    semua_hijau = all(ok for _, ok, _ in hasil)
    if SISI == "baru":
        print("PUTUSAN:", "LULUS" if semua_hijau else "GAGAL")
        return 0 if semua_hijau else 1
    # Sisi LAMA: gerbang ini HANYA berarti kalau keempatnya benar-benar merah.
    semua_merah = all(not ok for _, ok, _ in hasil)
    print("PUTUSAN:", "KONTROL MERAH SAH" if semua_merah else
          "KONTROL TIDAK MEMERAH -- gerbang tak membuktikan apa pun")
    return 0 if semua_merah else 1


sys.exit(main())
