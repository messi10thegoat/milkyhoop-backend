"""Pembanding PERSEDIAAN standar SO: argv = lama.json baru.json. Perubahan SAH saja:
 A baca: baru == lama pada SEMUA medan lama (aditif standar SO boleh); nomor jurnal penyesuaian LAMA tetap.
 C tulis: jurnal (akun/debit/kredit), pembalik, ledger (qty/biaya/gudang), total, warehouse_stock IDENTIK lama vs baru, KECUALI yang
   diputuskan: nomor jurnal baru = nomor dokumen (lama "SA-<nomor>"); gudang ledger = gudang UTAMA (lama: tertua; kaos/nyawit sama);
   jumlah gudang utama sesudah set-default: lama bisa 2, baru tepat 1 (U8). Status nonaktif gudang berstok: keduanya ditolak 400.
 B/G (cek pohon BARU) -> semua lulus."""
import json
import sys

lama, baru = (json.load(open(f)) for f in sys.argv[1:3])
ADITIF = {"jenis_text", "position_text", "aksi", "aksi_semua", "documents", "doc_count", "aksi_tersedia", "warehouse_id", "warehouse_name",
          "draf_count", "draf_value", "bulan_ini_count", "bulan_ini_value", "nilai_stok"}
hasil, sah = [], {"nomor_jurnal": 0, "utama": 0}


def cek(nama, ok, info=""):
    hasil.append(bool(ok))
    if not ok:
        print(f"FAIL {nama}" + (f" -- {str(info)[:500]}" if info else ""))


def sama_lama(a, b, jalur):
    if isinstance(a, dict) and isinstance(b, dict):
        cek(f"{jalur} kunci baru aditif", not (set(b) - set(a) - ADITIF), set(b) - set(a) - ADITIF)
        for k in a:
            cek(f"{jalur}.{k} ada", k in b)
            if k in b:
                sama_lama(a[k], b[k], f"{jalur}.{k}")
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            sama_lama(x, y, f"{jalur}[{i}]")
    else:
        cek(jalur, a == b, f"{a!r} -> {b!r}")


assert baru["baru"] is True and lama["baru"] is False
for tid in (k for k in baru if k not in ("baru", "CEK")):
    L, B = lama[tid], baru[tid]
    for k in sorted(L):
        cek(f"{tid} {k} ada di baru", k in B)
        if k not in B:
            continue
        if k.startswith("A_"):
            sama_lama(L[k], B[k], f"{tid} {k}")
        elif k.endswith("_post") or k.endswith("_void"):
            a, b = dict(L[k]), dict(B[k])
            ea, eb = dict(a.pop("efek")), dict(b.pop("efek"))
            na, nb = ea.pop("jurnal_nomor"), eb.pop("jurnal_nomor")
            if na != nb:  # putusan MASTER: data baru = nomor dokumen
                cek(f"{tid} {k} nomor jurnal lama SA-<dok> -> dokumen", na == f"SA-{nb}", (na, nb))
                sah["nomor_jurnal"] += 1
            cek(f"{tid} {k} respons + efek identik", a == b and ea == eb, json.dumps([L[k], B[k]])[:600])
        elif k == "C_wh_jumlah_utama":
            cek(f"{tid} gudang utama baru == 1", B[k] == 1, (L[k], B[k]))
            sah["utama"] += L[k] != B[k]
        else:
            cek(f"{tid} {k} identik", L[k] == B[k], (L[k], B[k]))
gagal = [c for c in baru["CEK"] if not c[1]]
for c in gagal:
    print(f"FAIL {c[0]} -- {c[2]}")
print(f"[info] cek pohon BARU (pratinjau == tulis, nol tulis, Inv-1, satu utama): {len(baru['CEK']) - len(gagal)}/{len(baru['CEK'])}")
print(f"[info] beda SAH: {sah}")
print(f"{sum(hasil)}/{len(hasil)} banding lulus")
sys.exit(0 if all(hasil) and not gagal else 1)
