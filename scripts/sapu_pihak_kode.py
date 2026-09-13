"""Sapuan KODE kelas uuid-vs-varchar pihak (versi 2). Dua kolom varchar saja (information_schema):
credit_notes.customer_id, customer_deposits.customer_id.

Versi 1 memakai pasangan triple-quote dan MELEWATKAN dua situs yang sudah diketahui (receive_payments.py
INSERT customer_deposits ~1818 dan WHERE customer_id = body ~1202) -> hasilnya tak sah. Versi 2:
JENDELA BARIS di sekitar setiap penyebutan nama tabel varchar (tak bergantung pasangan kutip), plus
fungsi DB dari pg_proc.

KONTROL POSITIF (ketiganya WAJIB tertangkap, kalau tidak -> sapuan TAK SAH):
  K1 credit_notes.py:1507 banding Python
  K2 receive_payments.py INSERT INTO customer_deposits (lebih bayar)
  K3 receive_payments.py SELECT FROM customer_deposits ... customer_id = $ (bayar dari DP)
"""
import os
import re
import subprocess

ROOT = "/root/milkyhoop-dev/backend/api_gateway/app"
V = ("credit_notes", "customer_deposits")
JENDELA = 18
situs = []  # (kategori, lokasi, cuplikan)


def pindai_teks(nama, teks):
    b = teks.split("\n")
    for i, ln in enumerate(b):
        low = ln.lower()
        for t in V:
            if not re.search(rf"\b{t}\b", low):
                continue
            blok = "\n".join(b[i:i + JENDELA]).lower()
            blok1 = blok.split(";")[0]
            if re.search(rf"insert\s+into\s+{t}\b", low) and "customer_id" in blok1[:1500]:
                situs.append(("TULIS-INSERT", f"{nama}:{i+1}", t))
            if re.search(rf"update\s+{t}\b", low) and re.search(r"\bcustomer_id\s*=", blok1[:600]):
                situs.append(("TULIS-UPDATE", f"{nama}:{i+1}", t))
            if re.search(rf"\bfrom\s+{t}\b|\bjoin\s+{t}\b", low):
                am = re.search(rf"\b{t}\s+(?:as\s+)?([a-z_][a-z0-9_]*)", low)
                alias = am.group(1) if am and am.group(1) not in ("where", "on", "set", "join", "left", "inner", "group", "order", "limit") else t
                for m in re.finditer(rf"\b(?:{re.escape(alias)}\.)?customer_id\b(\s*::\s*\w+)?\s*(=|<>|!=)\s*([^\s,)]+)", blok1[:900]):
                    kanan = m.group(3)
                    cast_kiri = bool(m.group(1))
                    cast_kanan = "::" in kanan
                    if kanan.startswith("$"):
                        situs.append(("BACA-PARAM", f"{nama}:{i+1}", f"{t} customer_id{m.group(1) or ''} {m.group(2)} {kanan}"))
                    elif ".customer_id" in kanan and not (cast_kiri or cast_kanan):
                        situs.append(("JOIN-SQL-tanpa-cast", f"{nama}:{i+1}", f"{t} customer_id {m.group(2)} {kanan}"))
                    elif ".customer_id" in kanan:
                        situs.append(("JOIN-SQL-cast", f"{nama}:{i+1}", f"{t} customer_id{m.group(1) or ''} {m.group(2)} {kanan}"))
        if re.search(r'\[\s*["\']customer_id["\']\s*\]', ln) and re.search(r"(!=|==)", ln):
            situs.append(("BANDING-PY", f"{nama}:{i+1}", ln.strip()[:120]))


for d, _, fs in os.walk(ROOT):
    if "__pycache__" in d:
        continue
    for f in fs:
        if f.endswith(".py"):
            p = os.path.join(d, f)
            pindai_teks(p.replace(ROOT + "/", ""), open(p, encoding="utf-8", errors="ignore").read())

sql = subprocess.run(["docker", "exec", "milkyhoop-dev-postgres-1", "psql", "-U", "postgres", "-d", "milkydb", "-At", "-c",
                      "SELECT p.proname || E'\\x1f' || pg_get_functiondef(p.oid) || E'\\x1e' FROM pg_proc p "
                      "JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind='f'"],
                     capture_output=True, text=True, check=True)
nfn = 0
for blok in sql.stdout.split("\x1e"):
    if "\x1f" in blok:
        nama, isi = blok.split("\x1f", 1)
        nfn += 1
        if any(t in isi.lower() for t in V):
            pindai_teks("DB:" + nama.strip(), isi)

situs = sorted(set(situs))
kat = {}
for k, l, c in situs:
    kat.setdefault(k, []).append((l, c))
for k in sorted(kat):
    print(f"\n== {k}: {len(kat[k])}")
    for l, c in kat[k]:
        print(f"   {l}  {c}")
print(f"\nfungsi DB dipindai: {nfn}")
k1 = any("credit_notes.py:150" in l and k == "BANDING-PY" for k, l, _ in situs)
k2 = any("receive_payments.py" in l and k == "TULIS-INSERT" and c == "customer_deposits" for k, l, c in situs)
k3 = any("receive_payments.py" in l and k == "BACA-PARAM" and "customer_deposits" in c for k, l, c in situs)
print(f"KONTROL K1={k1} K2={k2} K3={k3} -> {'SAH' if k1 and k2 and k3 else 'TAK SAH'}")
