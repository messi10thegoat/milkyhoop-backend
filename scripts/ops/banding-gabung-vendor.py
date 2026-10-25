"""Banding GABUNG-VENDOR (mode baru). argv: baru.jsonl [lama.jsonl]. Per pasangan:
 diblok  -> pratinjau ok=False, galat tulis 409 berpesan == blok pertama pratinjau, GL/AP/vendor TAK berubah (dibuktikan: ap_sesudah==ap_sebelum).
 lolos   -> pratinjau ok=True; AP tujuan sesudah == AP tujuan sebelum + AP sumber sebelum; AP sumber sesudah == 0; AP vendor lain sama;
            total AP sama; GL (per akun + jumlah jurnal) IDENTIK; sumber nonaktif; 0 tagihan tersisa di sumber; jejak = 3 (1 audit + 2 aktivitas);
            ulang ber-kunci == jawaban pertama tanpa tulis.
 semua   -> pratinjau NOL tulis."""
import json, sys
from decimal import Decimal as D
baru = [json.loads(l) for l in open(sys.argv[1])]
gagal, lolos, blok = [], 0, 0
for r in baru:
    nm = f"{r['sumber']}->{r['tujuan']}"
    if not r["pratinjau_tanpa_tulis"]: gagal.append(f"pratinjau_menulis {nm}")
    pv = r["pratinjau"]["ok"]["data"]
    s_id, t_id = list(r["ap_sebelum"])
    if "galat" in r["respons"]:
        blok += 1
        st, det = r["respons"]["galat"]
        if pv["ok"] or st != 409 or not pv["blocks"] or det.get("message") != pv["blocks"][0]["message"]: gagal.append(f"blok_beda {nm} {r['respons']}")
        if r["ap_sesudah"] != r["ap_sebelum"] or not r["gl_sama"] or r["jejak_baru"] != 0: gagal.append(f"blok_tapi_menulis {nm}")
        continue
    lolos += 1
    if not pv["ok"]: gagal.append(f"pratinjau_blok_tulis_lolos {nm}")
    if D(r["ap_sesudah"][t_id]) != D(r["ap_sebelum"][t_id]) + D(r["ap_sebelum"][s_id]): gagal.append(f"ap_tujuan {nm} {r['ap_sebelum']} -> {r['ap_sesudah']}")
    if D(r["ap_sesudah"][s_id]) != 0: gagal.append(f"ap_sumber_sisa {nm}")
    if not r["ap_lain_sama"] or r["ap_total_sesudah"] != r["ap_total_sebelum"]: gagal.append(f"ap_lain_atau_total {nm}")
    if not r["gl_sama"]: gagal.append(f"GL_berubah {nm}")
    if r["sumber_aktif_sesudah"] or r["sisa_di_sumber"]: gagal.append(f"sumber_belum_bersih {nm}")
    if r["jejak_baru"] != 3: gagal.append(f"jejak {nm} {r['jejak_baru']}")
    if r["ulang"] != r["respons"] or not r["ulang_tanpa_tulis"]: gagal.append(f"ulang {nm}")
if len(sys.argv) > 2:
    lama = [json.loads(l) for l in open(sys.argv[2])]
    print("LAMA respons:", sorted({json.dumps(x["respons"])[:90] for x in lama}))
print(f"pasangan={len(baru)} lolos={lolos} diblok={blok}")
for g in gagal[:12]: print("GAGAL", g)
print("LULUS" if not gagal and lolos > 0 else "GAGAL")
