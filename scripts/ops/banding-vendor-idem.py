"""Banding VENDOR-IDEM. argv: lama.jsonl baru.jsonl [lama2.jsonl]. Baris vendor nyata: respons + keadaan IDENTIK LAMA vs BARU (dan LAMA vs LAMA2
bila diberi -- bukti deterministik, TANPA masker apa pun). Baris <TES>: BARU ulang ber-kunci == jawaban pertama untuk buat/status/hapus, aktivitas
status +1 (bukan +2), sisa sesudah rollback 0; LAMA dicatat apa adanya (ulang = galat/aktivitas ganda) sebagai pembanding."""
import json, sys
baca = lambda p: [json.loads(l) for l in open(p)]
lama, baru = baca(sys.argv[1]), baca(sys.argv[2])
gagal = []
if len(sys.argv) > 3:
    l2 = baca(sys.argv[3])
    beda = [(a["v"], a["kasus"]) for a, b in zip(lama[:-1], l2[:-1]) if a != b]
    print(f"LAMA vs LAMA2: {len(lama) - 1} kasus, beda={len(beda)}")
    if beda: gagal.append(f"tak_deterministik={beda[:3]}")
assert len(lama) == len(baru)
for a, b in zip(lama[:-1], baru[:-1]):
    if (a["v"], a["kasus"]) != (b["v"], b["kasus"]): gagal.append("urutan"); break
    if a["respons"] != b["respons"]: gagal.append(f"respons_beda {a['v']}/{a['kasus']}")
    if a["sesudah"] != b["sesudah"]: gagal.append(f"keadaan_beda {a['v']}/{a['kasus']}")
tl, tb = lama[-1], baru[-1]
print("LAMA ulang:", {k: (tl[k] if not isinstance(tl[k], dict) else list(tl[k])[0]) for k in ("buat_2", "status_2", "hapus_2", "jumlah_nama")}, "aktivitas sesudah status:", tl["sesudah_status"]["aktivitas"])
for k in ("buat", "status", "hapus"):
    if tb[f"{k}_2"] != tb[f"{k}_1"]: gagal.append(f"BARU {k}_2 bukan jawaban pertama: {tb[f'{k}_2']}")
    if tb[f"{k}_1"] != tl[f"{k}_1"]: gagal.append(f"{k}_1 beda LAMA vs BARU")
if tb["jumlah_nama"] != 1: gagal.append("jumlah_nama")
if tb["sesudah_status"]["aktivitas"] != tl["sesudah_status"]["aktivitas"] - (1 if "ok" in tl["status_2"] else 0): gagal.append("aktivitas_status")
if tb["sisa_sesudah_rollback"] or tl["sisa_sesudah_rollback"]: gagal.append("sisa_rollback")
print(f"kasus vendor nyata={len(baru) - 1}")
for g in gagal: print("GAGAL", g)
print("LULUS" if not gagal else "GAGAL")
