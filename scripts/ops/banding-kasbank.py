"""Pembanding KAS & BANK standar SO: argv = lama.json baru.json. Perubahan SAH saja:
 A baca: baru == lama pada SEMUA medan lama (medan aditif standar SO boleh: saldo, position_text, aksi, aksi_semua, documents, doc_count,
   aksi_tersedia, {k}_count/_value, sum_*).
 C tulis void: efek (jurnal pembalik, cermin, status, alasan) + respons sukses IDENTIK; void kedua = ditolak dengan STATUS sama (teks lama
   Inggris -> kalimat berkode = SAH, kontrak §5).
 B/G (cek pohon BARU) -> semua lulus."""
import json
import sys

lama, baru = (json.load(open(f)) for f in sys.argv[1:3])
ADITIF = {"saldo", "position_text", "aksi", "aksi_semua", "documents", "doc_count", "aksi_tersedia", "draf_count", "draf_value",
          "bulan_ini_count", "bulan_ini_value", "saldo_minus_count", "nonaktif_count"}
hasil = []


def cek(nama, ok, info=""):
    hasil.append(bool(ok))
    if not ok:
        print(f"FAIL {nama}" + (f" -- {info}" if info else ""))


def sama_lama(a, b, jalur):
    """Semua kunci lama ada + sama; kunci baru hanya ADITIF."""
    if isinstance(a, dict) and isinstance(b, dict):
        lebih = set(b) - set(a) - ADITIF
        cek(f"{jalur} kunci baru aditif", not lebih, lebih)
        for k in a:
            cek(f"{jalur}.{k} ada", k in b)
            if k in b:
                sama_lama(a[k], b[k], f"{jalur}.{k}")
    else:
        cek(jalur, a == b, f"{a!r} -> {b!r}")


assert baru["baru"] is True and lama["baru"] is False
n_sah = 0
for tid in (k for k in baru if k not in ("baru", "CEK")):
    L, B = lama[tid], baru[tid]
    for k in sorted(L):
        cek(f"{tid} {k} ada di baru", k in B)
        if k not in B:
            continue
        if k.startswith("A_"):
            sama_lama(L[k], B[k], f"{tid} {k}")
        elif k.endswith("_ulang"):
            cek(f"{tid} {k} void kedua: status sama", L[k] == B[k], (L[k], B[k]))
            n_sah += not L[k]["ok"]
        elif k.startswith("C_"):
            cek(f"{tid} {k} efek + respons identik", L[k] == B[k], json.dumps([L[k], B[k]])[:600])
gagal_b = [c for c in baru["CEK"] if not c[1]]
for c in gagal_b:
    print(f"FAIL {c[0]} -- {c[2]}")
print(f"[info] cek pohon BARU (B/G/pratinjau==tulis): {len(baru['CEK']) - len(gagal_b)}/{len(baru['CEK'])}")
print(f"[info] void kedua ditolak (status sama; teks berkode = SAH): {n_sah}")
print(f"{sum(hasil)}/{len(hasil)} banding lulus")
sys.exit(0 if all(hasil) and not gagal_b else 1)
