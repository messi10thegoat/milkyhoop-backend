"""BUG-249: customers.name NULL di semua baris; nama pelanggan = customers.nama (satu sumber).

Penjaga statik: tak boleh ada SQL di kode Python yang membaca <alias customers>.name atau
'SELECT name FROM customers'. Alias dihitung per pernyataan SQL (blok triple-quote atau baris), jadi
alias yang sama untuk chart_of_accounts di pernyataan lain tidak salah tandai.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]   # .../backend (berkas ini di backend/api_gateway/tests/unit/)
BUKAN_ALIAS = {"WHERE", "ON", "LEFT", "JOIN", "SET", "ORDER", "GROUP", "LIMIT", "FOR", "INNER", "AND"}


def _berkas():
    for p in ROOT.rglob("*.py"):
        s = str(p.relative_to(ROOT))
        if "/tests/" in s or s.startswith("tests/") or "/libs/milkyhoop_prisma/" in s:
            continue   # tes + kode prisma hasil generate (55rb baris, bukan SQL tulisan tangan)
        yield p


def _pernyataan(teks):
    """(nomor_baris_awal, isi) untuk tiap blok triple-quote dan tiap baris di luarnya."""
    def baris_ke(pos):
        return teks.count("\n", 0, pos) + 1

    def per_baris(awal, akhir):
        no = baris_ke(awal)
        for baris in teks[awal:akhir].split("\n"):
            yield no, re.sub(r"\s#.*$", "", baris)
            no += 1

    pos = 0
    for m in re.finditer(r'"""(.*?)"""', teks, re.S):
        yield from per_baris(pos, m.start())
        yield baris_ke(m.start()), m.group(1)
        pos = m.end()
    yield from per_baris(pos, len(teks))


def test_tak_ada_pembaca_customers_name():
    temuan = []
    for p in _berkas():
        for no, st in _pernyataan(p.read_text(errors="ignore")):
            if "customers" not in st:
                continue
            alias = {"customers"}
            for m in re.finditer(r"(?:FROM|JOIN)\s+customers\s+(?:AS\s+)?(\w+)", st, re.I):
                if m.group(1).upper() not in BUKAN_ALIAS:
                    alias.add(m.group(1))
            for a in alias:
                if re.search(r"\b" + a + r"\.name\b", st):
                    temuan.append(f"{p.relative_to(ROOT)}:{no} {a}.name")
            if re.search(r"SELECT\s+name\s+FROM\s+customers\b", st, re.I):
                temuan.append(f"{p.relative_to(ROOT)}:{no} SELECT name FROM customers")
    assert not temuan, "customers.name dibaca (pakai customers.nama): " + "; ".join(temuan)
