#!/usr/bin/env python3
"""Penjaga harness salinan (putusan MASTER 7 Nov 2026): SETIAP SQL ber-"FROM <tabel>" di skrip uji wajib tersaring tenant
(tenant_id / "tenantId"), supaya hitungan tak pernah ikut membaca tenant lain (dua pelanggaran 7 Nov: count global ikut grapgrap).
Pengecualian sempit: katalog Postgres (pg_*) dan subkueri baris yang terkunci ke induk ber-tenant (journal_id = je.id).
Pakai: cek-saring-tenant.py <berkas.py|berkas.sh>...  -> rc 0 bersih, rc 3 ada SQL tanpa saring (dicetak)."""
import ast
import re
import sys

KECUALI = ("pg_database", "pg_catalog", "pg_trigger", "pg_proc", "current_database()", "l.journal_id = je.id")
KATALOG_SAJA = ("pg_locks",)  # boleh tanpa tenant HANYA bila SEMUA sasaran FROM/JOIN = katalog (campur tabel bisnis = tetap tertangkap)


def literal(path):
    teks = open(path).read()
    if path.endswith(".py"):
        pohon = ast.parse(teks)
        anak = {id(v) for n in ast.walk(pohon) if isinstance(n, ast.JoinedStr) for v in n.values}
        for n in ast.walk(pohon):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in anak:
                yield n.lineno, n.value
            elif isinstance(n, ast.JoinedStr):
                yield n.lineno, "".join(v.value if isinstance(v, ast.Constant) else "{" + ast.unparse(v.value) + "}" for v in n.values)
    else:
        for i, baris in enumerate(teks.splitlines(), 1):
            yield i, baris


def main(paths):
    salah = []
    for p in paths:
        for no, s in literal(p):
            t = " ".join(s.split())
            if not re.search(r"\bFROM\s+[\"\w]", t, re.I) or not re.search(r"\bSELECT\b|\bDELETE\b", t, re.I):
                continue
            if "tenant_id" in t or '"tenantId"' in t or "\\\"tenantId\\\"" in t or any(k in t for k in KECUALI):
                continue
            sasaran = re.findall(r"\b(?:FROM|JOIN)\s+([\"\w.]+)", t, re.I)
            if sasaran and all(x.lower() in KATALOG_SAJA for x in sasaran):
                continue
            salah.append(f"{p}:{no}: {t[:140]}")
    for s in salah:
        print("TANPA-SARING-TENANT", s)
    return 3 if salah else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
