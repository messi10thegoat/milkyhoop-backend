"""Premis penomoran jurnal "satu ruang nama" (L2-C1 + C2, 23 Sep 2026).

Jurnal lepas pembayaran bernomor = nomor uang muka LPS-nya, jurnal uang muka bernomor = nomor
uang mukanya (DEP-...). Pra-cek "nomor sudah dipakai jurnal lain?" lalu INSERT aman dari ras HANYA
karena TIDAK ADA penulis lain yang menerbitkan nomor jurnal ber-awalan DEP-/LPS- (nomor uang muka
unik per tenant: baris penghitungnya di-upsert berurutan). Tes ini menjadikan premis itu TERUKUR:
  1. tak ada pemanggil get_next_journal_number dengan awalan literal 'DEP' / 'LPS';
  2. setiap pemanggil yang awalannya BUKAN literal (variabel, f-string, $N yang tak terikat
     literal) wajib ada di DAFTAR_IZIN beserta alasannya -- supaya premis tak bisa dilanggar
     diam-diam lewat penerusan;
  3. kedua pra-cek masih ada di sumbernya.
Bentuk yang dipindai: teks SQL "get_next_journal_number($1, 'X')" / "($1, $2)" (dengan $N
diselesaikan ke argumen panggilan yang membawa SQL itu) dan panggilan Python
get_next_journal_number(conn, tenant, prefix, ...).
"""
import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
TERLARANG = {"DEP", "LPS"}
SQL = re.compile(r"get_next_journal_number\(\s*[^,()]+?\s*,\s*([^,()]+?)\s*[,)]", re.S)

# Awalan non-literal yang DIIZINKAN = PEMBUNGKUS yang meneruskan parameternya ke fungsi DB.
# (berkas relatif app/, fungsi, ekspresi awalan) -> (alasan, nama pembungkus, indeks arg awalan pada
# pemanggilnya). Izin hanya sah karena PEMANGGIL pembungkus ikut dipindai (test_pembungkus_...):
# mereka wajib memberi awalan literal di luar TERLARANG. Entri yang tak lagi cocok dengan situs
# mana pun = basi -> merah (daftar izin tak boleh membusuk jadi cek kosong).
DAFTAR_IZIN = {
    ("routers/journals.py", "get_next_journal_number", "prefix"):
        ("pembungkus Python jurnal manual; pemanggil = 'JV'", "get_next_journal_number", 2),
    ("services/kernel_document_executor.py", "_next_journal_number", "prefix"):
        ("pembungkus kernel dokumen; pemanggil = 'DI'", "_next_journal_number", 2),
}


def _teks(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{EXPR}" for v in node.values)
    return None


def _fungsi_induk(tree):
    induk = {}
    for f in ast.walk(tree):
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(f):
                induk.setdefault(id(n), f.name)
    return induk


def enumerasi(app=APP):
    """-> list of (file, func, lineno, 'literal'|'nonliteral', value_or_expr)."""
    hasil = []
    for p in sorted(app.rglob("*.py")):
        src = p.read_text(errors="ignore")
        if "get_next_journal_number" not in src:
            continue
        rel = str(p.relative_to(app))
        tree = ast.parse(src)
        induk = _fungsi_induk(tree)
        terpakai = set()
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            nama = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            fn = induk.get(id(n), "<modul>")
            if nama == "get_next_journal_number":  # panggilan Python
                arg = n.args[2] if len(n.args) > 2 else next(
                    (k.value for k in n.keywords if k.arg in ("prefix", "p_prefix")), None)
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    hasil.append((rel, fn, n.lineno, "literal", arg.value))
                else:
                    hasil.append((rel, fn, n.lineno, "nonliteral", ast.unparse(arg) if arg else "<default JV>"))
                continue
            for i, a in enumerate(n.args):
                t = _teks(a)
                if not t or "get_next_journal_number" not in t:
                    continue
                terpakai.add(id(a))
                for m in SQL.finditer(t):
                    tok = m.group(1).strip()
                    lit = re.fullmatch(r"'([^'{}]*)'", tok)  # '{EXPR}' dari f-string = BUKAN literal
                    par = re.fullmatch(r"\$(\d+)", tok)
                    if lit:
                        hasil.append((rel, fn, n.lineno, "literal", lit.group(1)))
                    elif par and len(n.args) > i + int(par.group(1)) and isinstance(
                            n.args[i + int(par.group(1))], ast.Constant) and isinstance(n.args[i + int(par.group(1))].value, str):
                        hasil.append((rel, fn, n.lineno, "literal", n.args[i + int(par.group(1))].value))
                    else:
                        ekspr = ast.unparse(n.args[i + int(par.group(1))]) if par and len(n.args) > i + int(par.group(1)) else tok
                        hasil.append((rel, fn, n.lineno, "nonliteral", ekspr))
        # SQL yang ditaruh di variabel dulu (tak langsung jadi argumen panggilan) = tak terselesaikan
        for n in ast.walk(tree):
            t = _teks(n) if isinstance(n, (ast.Constant, ast.JoinedStr)) else None
            if t and "get_next_journal_number" in t and id(n) not in terpakai and SQL.search(t):
                for m in SQL.finditer(t):
                    tok = m.group(1).strip()
                    lit = re.fullmatch(r"'([^'{}]*)'", tok)  # '{EXPR}' dari f-string = BUKAN literal
                    hasil.append((rel, induk.get(id(n), "<modul>"), n.lineno,
                                  "literal" if lit else "nonliteral", lit.group(1) if lit else tok))
    return hasil


def _diizinkan(rel, fn, ekspr):
    return (rel, fn, ekspr) in DAFTAR_IZIN


def panggilan_pembungkus(nama, idx, app=APP):
    """Semua panggilan Python ke pembungkus `nama` -> (file, lineno, 'literal'|'nonliteral', nilai)."""
    hasil = []
    for p in sorted(app.rglob("*.py")):
        src = p.read_text(errors="ignore")
        if nama not in src:
            continue
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.Call):
                f = n.func
                if (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)) != nama:
                    continue
                arg = n.args[idx] if len(n.args) > idx else next((k.value for k in n.keywords if k.arg == "prefix"), None)
                if arg is None:
                    hasil.append((str(p.relative_to(app)), n.lineno, "literal", "<default>"))
                elif isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    hasil.append((str(p.relative_to(app)), n.lineno, "literal", arg.value))
                else:
                    hasil.append((str(p.relative_to(app)), n.lineno, "nonliteral", ast.unparse(arg)))
    return hasil


def test_tak_ada_awalan_literal_terlarang():
    buruk = [h for h in enumerasi() if h[3] == "literal" and h[4] in TERLARANG]
    assert not buruk, f"penerbit nomor jurnal DEP-/LPS- selain nomor uang muka: {buruk}"


def test_awalan_nonliteral_wajib_diizinkan():
    liar = [h for h in enumerasi() if h[3] == "nonliteral" and not _diizinkan(h[0], h[1], h[4])]
    assert not liar, f"awalan nomor jurnal non-literal tanpa izin (bisa melanggar premis diam-diam): {liar}"


def test_pembungkus_diizinkan_dipanggil_dengan_literal_aman():
    for (_rel, _fn, _e), (_alasan, nama, idx) in DAFTAR_IZIN.items():
        buruk = [h for h in panggilan_pembungkus(nama, idx) if h[2] != "literal" or h[3] in TERLARANG]
        assert not buruk, f"pemanggil pembungkus {nama} memberi awalan non-literal/terlarang: {buruk}"


def test_daftar_izin_tak_basi():
    situs = {(h[0], h[1], h[4]) for h in enumerasi() if h[3] == "nonliteral"}
    basi = [k for k in DAFTAR_IZIN if k not in situs]
    assert not basi, f"entri izin tak cocok situs mana pun (hapus): {basi}"


def test_pra_cek_masih_ada():
    rp = (APP / "routers/receive_payments.py").read_text()
    cd = (APP / "routers/customer_deposits.py").read_text()
    assert "journal_number = dep_no" in rp and "get_next_journal_number($1, 'LPJ')" in rp
    assert "journal_number = dep[\"deposit_number\"]" in cd and "get_next_journal_number($1, 'DPJ')" in cd


def test_pemindai_bisa_merah(tmp_path):
    """Kontrol merah alat ukur: pohon buatan dengan pelanggaran di tiap bentuk -> semua tertangkap."""
    (tmp_path / "x.py").write_text(
        "async def a(conn, t, p):\n"
        "    await conn.fetchval(\"SELECT get_next_journal_number($1, 'LPS')\", t)\n"
        "    await conn.fetchval(\"SELECT get_next_journal_number($1, $2)\", t, 'DEP')\n"
        "    await conn.fetchval(\"SELECT get_next_journal_number($1, $2)\", t, p)\n"
        "    await conn.fetchval(f\"SELECT get_next_journal_number($1, '{p}')\", t)\n"
        "    q = \"SELECT get_next_journal_number($1, $2)\"\n"
        "    await get_next_journal_number(conn, t, p)\n"
        "    await self._next_journal_number(conn, t, p)\n"
    )
    h = sorted((x[3], x[4]) for x in enumerasi(tmp_path))
    assert h == sorted([("literal", "DEP"), ("literal", "LPS"), ("nonliteral", "p"), ("nonliteral", "'{EXPR}'"),
                        ("nonliteral", "$2"), ("nonliteral", "p")]), h
    assert [x[2:] for x in panggilan_pembungkus("_next_journal_number", 2, tmp_path)] == [("nonliteral", "p")]
