"""Banding GABUNG-VENDOR (mode baru). argv: baru.jsonl [lama.jsonl]. Per pasangan:
 diblok  -> pratinjau ok=False, galat tulis 409 berpesan == blok pertama pratinjau, GL/AP/vendor TAK berubah (dibuktikan: ap_sesudah==ap_sebelum).
 lolos   -> pratinjau ok=True; AP tujuan sesudah == AP tujuan sebelum + AP sumber sebelum; AP sumber sesudah == 0; AP vendor lain sama;
            total AP sama; GL (per akun + jumlah jurnal) IDENTIK; sumber nonaktif; 0 tagihan tersisa di sumber; jejak = 3 (1 audit + 2 aktivitas);
            ulang ber-kunci == jawaban pertama tanpa tulis.
 semua   -> pratinjau NOL tulis."""
import json, sys
from decimal import Decimal as D
baru = [json.loads(l) for l in open(sys.argv[1])]
import os
PETA = json.load(open(os.environ["PETA_NAMA_ID"])) if os.environ.get("PETA_NAMA_ID") else {}  # hanya untuk berkas lama tanpa *_id
gagal, lolos, blok = [], 0, 0
for r in baru:
    nm = f"{r['sumber']}->{r['tujuan']}"
    if not r["pratinjau_tanpa_tulis"]: gagal.append(f"pratinjau_menulis {nm}")
    pv = r["pratinjau"]["ok"]["data"]
    # id EKSPLISIT (dulu dari urutan kunci ap_sebelum -- json sort_keys mengurutkan abjad -> sumber/tujuan tertukar; bug banding 25 Okt)
    s_id, t_id = (r["sumber_id"], r["tujuan_id"]) if "sumber_id" in r else (PETA[r["sumber"]], PETA[r["tujuan"]])
    if r["respons"].get("galat", [0])[0] >= 500: gagal.append(f"500 {nm}")  # 500 = selalu GAGAL (bukan blok)
    if r.get("tanpa_status_sumber"):  # syarat MASTER: pasangan bersumber tagihan tanpa status WAJIB berakhir BLOK, bukan dilewati
        tb_ = r["respons"].get("galat")
        if not (tb_ and tb_[0] == 409 and isinstance(tb_[1], dict) and tb_[1].get("code") == "TAGIHAN_TANPA_STATUS"): gagal.append(f"tanpa_status_tak_diblok {nm} {r['respons']}")
    if "galat" in r["respons"]:
        blok += 1
        st, det = r["respons"]["galat"]
        if pv["ok"] or st != 409 or not pv["blocks"] or det.get("message") != pv["blocks"][0]["message"]: gagal.append(f"blok_beda {nm} {r['respons']}")
        if r["ap_sesudah"] != r["ap_sebelum"] or not r["gl_sama"] or r["jejak_baru"] != 0 or r["dok_sesudah"] != r["dok_sebelum"]: gagal.append(f"blok_tapi_menulis {nm}")
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
    for tb, a in r["dok_sebelum"].items():  # syarat MASTER: PO/GRN/UM ikut pindah utuh
        z = r["dok_sesudah"][tb]
        if z["s"] != 0 or z["t"] != a["s"] + a["t"]: gagal.append(f"dok_{tb} {nm} {a} -> {z}")
    if not r["qty_sama"]: gagal.append(f"qty_PO_berubah {nm}")
if len(sys.argv) > 2:
    lama = [json.loads(l) for l in open(sys.argv[2])]
    print("LAMA respons:", sorted({json.dumps(x["respons"])[:90] for x in lama}))
ts = sum(1 for r in baru if r.get("tanpa_status_sumber"))
if ts == 0: gagal.append("tak satu pun pasangan bersumber tagihan tanpa status (penolak tak teruji)")
grn = sum(1 for r in baru if r.get("dok_sebelum", {}).get("goods_receipts", {}).get("s"))
print(f"pasangan={len(baru)} lolos={lolos} diblok={blok} tanpa_status={ts} pasangan_ber_GRN_sumber={grn} kirim/terima={sorted({str((r.get('po_kirim'), r.get('po_terima'))) for r in baru})}")
if grn == 0: gagal.append("tak satu pun pasangan punya GRN di sumber (syarat MASTER tak teruji)")
for g in gagal[:12]: print("GAGAL", g)
print("LULUS" if not gagal and lolos > 0 else "GAGAL")
