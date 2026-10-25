"""Banding QA-AGEN: LAMA vs BARU (respons + keadaan IDENTIK) dan syarat BARU (pratinjau ok iff tulis ok, pesan blok == pesan galat,
pratinjau NOL tulis, ulang ber-kunci == jawaban pertama tanpa tulis kedua). argv: lama.jsonl baru.jsonl -> LULUS/GAGAL."""
import collections, json, sys
baca = lambda p: [json.loads(l) for l in open(p)]
lama, baru = baca(sys.argv[1]), baca(sys.argv[2])
gagal = collections.Counter(); contoh = {}
def g(k, r):
    gagal[k] += 1; contoh.setdefault(k, f'{r["q"]}/{r["kasus"]}')
def pesan(galat):
    d = galat[1]
    return d.get("message") if isinstance(d, dict) else d
assert len(lama) == len(baru), (len(lama), len(baru))
hit = collections.Counter()
for a, b in zip(lama, baru):
    assert (a["q"], a["kasus"]) == (b["q"], b["kasus"])
    if a["respons"] != b["respons"]: g("respons_beda", b)
    if a["sesudah"] != b["sesudah"]: g("keadaan_beda", b)
    if not b["pratinjau_tanpa_tulis"]: g("pratinjau_menulis", b)
    pv, rs = b["pratinjau"], b["respons"]
    if "galat" in pv:
        if not ("galat" in rs and rs["galat"][0] == pv["galat"][0] == 404): g("pratinjau_galat_bukan_404", b)
        continue
    ok_tulis = "ok" in rs
    hit["ok" if ok_tulis else "blok"] += 1
    if pv["ok"]["data"]["ok"] != ok_tulis: g("pratinjau_ok_beda_tulis", b)
    if not ok_tulis and (not pv["ok"]["data"]["blocks"] or pv["ok"]["data"]["blocks"][0]["message"] != pesan(rs["galat"])): g("pesan_blok_beda", b)
    if ok_tulis:
        if b["ulang"] != rs: g("ulang_bukan_jawaban_pertama", b)
        if b["sesudah_ulang"] != b["sesudah"]: g("ulang_menulis", b)
    if b["riwayat_baru"] > 1: g("riwayat_lebih_1", b)
for nama, berkas in (("lama", sys.argv[1]), ("baru", sys.argv[2])):
    try:
        su = json.load(open(berkas + ".surel.json"))
    except FileNotFoundError:
        su = None
    if not su or su["surel_kasus"] != 0: g(f"surel_nyata_{nama}", {"q": "-", "kasus": str(su and su["surel_kasus"])})
    if not su or su["surel_kontrol"] < 1: g(f"surel_kontrol_tak_tercatat_{nama}", {"q": "-", "kasus": "-"})
    print(f"surel {nama}: kasus={su and su['surel_kasus']} kontrol={su and su['surel_kontrol']}")
print(f"kasus={len(baru)} tulis_ok={hit['ok']} diblok={hit['blok']}")
for k, v in gagal.items(): print(f"GAGAL {k}={v} contoh={contoh[k]}")
print("LULUS" if not gagal else "GAGAL")
