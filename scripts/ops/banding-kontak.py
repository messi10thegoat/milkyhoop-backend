"""Pembanding KONTAK standar SO: argv = lama.json baru.json. Perubahan SAH saja:
 A detail: baru == lama + aksi_tersedia; aksi_tersedia == bentuk bersama dari `aksi` yang SAMA
 B/C/D/E/F hasil tulis + baris sesudahnya IDENTIK lama vs baru (refaktor PATCH / alias status / penentu buat = nol beda perilaku)
 pratinjau BARU == hasil tulisnya: edit -> label aktivitas; status aktif -> /reactivate; hapus -> DELETE; buat -> POST; G nol tulis.
 SAH (penjaga hapus dilengkapi): DELETE lama 200/500(FK) -> baru 400 berkalimat tabel BARU; aksi detail `hapus` boleh -> tidak (kalimat sama)."""
import json
import sys

lama, baru = (json.load(open(f)) for f in sys.argv[1:3])
hasil = []
JEJAK_BARU = ("pesanan penjualan", "penawaran", "proforma", "kwitansi penjualan", "pesanan pembelian", "pembayaran keluar",
              "penerimaan barang", "memiliki biaya", "tagihan berulang", "cek/giro", "aset tetap", "pemeliharaan aset", "subkontrak produksi",
              "operasi BOM")
sah_n = {"aksi_hapus": 0, "delete": 0}


def sah_jejak(m):
    return isinstance(m, str) and m.startswith("Tidak bisa menghapus") and any(x in m for x in JEJAK_BARU)


def aksi_hapus_sah(lama_aksi, baru_aksi):
    """Beda HANYA di aksi `hapus`: boleh -> tidak dengan kalimat penjaga baru."""
    la, ba = {a["kunci"]: a for a in lama_aksi}, {a["kunci"]: a for a in baru_aksi}
    if set(la) != set(ba) or any(la[k] != ba[k] for k in la if k != "hapus"):
        return False
    return la["hapus"]["boleh"] is True and ba["hapus"]["boleh"] is False and sah_jejak(ba["hapus"].get("alasan"))


def cek(nama, ok, info=""):
    hasil.append(bool(ok))
    if not ok:
        print(f"FAIL {nama}" + (f" -- {info}" if info else ""))


assert baru["baru"] is True and lama["baru"] is False
for tid in (k for k in baru if k != "baru"):
    L, B = lama[tid], baru[tid]
    n = {"A": 0, "C": 0, "D": 0, "E": 0, "F": 0}
    for k in sorted(L):
        cek(f"{tid} {k} ada di baru", k in B)
        if k.startswith("A_"):
            b = dict(B[k])
            peta = b.pop("aksi_tersedia", "HILANG")
            beda = {x for x in set(b) | set(L[k]) if b.get(x) != L[k].get(x)}
            sah = beda == {"aksi"} and aksi_hapus_sah(L[k]["aksi"], b["aksi"])
            sah_n["aksi_hapus"] += sah
            cek(f"{tid} {k} identik selain aksi_tersedia", not beda or sah, f"{beda} {L[k].get('aksi')} -> {b.get('aksi')}")
            harap = {a["kunci"]: {"tersedia": a["boleh"], "alasan": None if a["boleh"] else a.get("alasan")} for a in b["aksi"]}
            cek(f"{tid} {k} aksi_tersedia == aksi", peta == harap, f"{peta} vs {harap}")  # urutan: tes unit (JSON harness sort_keys)
            n["A"] += 1
        elif k[0] in "BCDEF":
            sah = (k.startswith(("E_c:", "E_v:")) and (L[k]["ok"] or L[k].get("status") == 500) and not B[k]["ok"]
                   and B[k].get("status") == 400 and sah_jejak(B[k].get("detail")))
            sah_n["delete"] += sah
            cek(f"{tid} {k} identik", B[k] == L[k] or sah, f"{L[k]!r} -> {B[k]!r}")
            n[k[0]] = n.get(k[0], 0) + 1
    cek(f"{tid} G nol tulis pratinjau", B["G_nol_tulis"] is True)
    for k in [x for x in B if x.startswith("C_pc:")]:
        nama = k[5:]
        p, a = B[k], B.get(f"C_ca:{nama}")
        label_akt = sorted(x.split(":")[0] for x in (a or "").splitlines())
        cek(f"{tid} {k} ok & perubahan == aktivitas", p["ok"] and sorted(x["label"] for x in p["perubahan"]) == label_akt,
            f"{p['perubahan']} vs {label_akt}")
    for k in [x for x in B if x.startswith("C_pv:")]:
        nama = k[5:]
        p, a = B[k], B.get(f"C_va:{nama}") or []
        cek(f"{tid} {k} ok & perubahan == aktivitas", p["ok"] and sorted(x["field"] for x in p["perubahan"]) == sorted(x["field_name"] for x in a),
            f"{p['perubahan']} vs {a}")
    for k in [x for x in B if x.startswith("D_pa:")]:
        nama = k[5:]
        cek(f"{tid} {k} pratinjau aktif == /status active", B[k]["ok"] == B[f"D_c2:{nama}"]["ok"], f"{B[k]} vs {B[f'D_c2:{nama}']}")
    for jenis in ("c", "v"):
        for k in [x for x in B if x.startswith(f"E_p{jenis}:")]:
            nama = k[5:]
            p, d = B[k], B[f"E_{jenis}:{nama}"]
            sama = p["ok"] == d["ok"] and (d["ok"] or p["blocks"] == [{"code": "HTTP_400", "message": d["detail"]}])
            cek(f"{tid} {k} pratinjau hapus == DELETE", sama, f"{p} vs {d}")
            n["E"] += 1
    cek(f"{tid} F pratinjau buat == POST", B["F_p_baru"]["ok"] == B["F_baru"]["ok"], f"{B['F_p_baru']} vs {B['F_baru']}")
    if "F_ganda" in B:
        g, p = B["F_ganda"], B["F_p_ganda"]
        cek(f"{tid} F pratinjau ganda == POST", (not g["ok"]) and p["blocks"][0]["code"] == g["code"] and p["blocks"][0]["message"] == g["detail"],
            f"{p} vs {g}")
    print(f"[info] {tid}: {n}")
print(f"[info] beda SAH penjaga hapus: {sah_n}")
print(f"{sum(hasil)}/{len(hasil)} lulus")
sys.exit(0 if all(hasil) else 1)
