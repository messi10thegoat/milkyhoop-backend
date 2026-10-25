"""6a: urutan status_code HTTPException + (kode, status) Blok.galat per berkas LAMA == BARU (teks boleh beda, status tidak)."""
import ast, sys
from collections import Counter
BERKAS = ["routers/bills.py", "routers/bill_payments.py", "routers/vendor_credits.py", "services/bills_service.py",
          "services/bayar_keluar_rencana.py", "services/nota_debit_rencana.py", "services/tagihan_aksi.py", "services/tagihan_rencana.py"]


def jejak(akar, f):
    out = []
    for n in ast.walk(ast.parse(open(f"{akar}/backend/api_gateway/app/{f}").read())):
        if isinstance(n, ast.Call):
            nama = getattr(n.func, "attr", getattr(n.func, "id", ""))
            if nama == "HTTPException":
                kw = {k.arg: ast.unparse(k.value) for k in n.keywords}
                out.append(("HTTP", kw.get("status_code")))
            elif nama in ("galat", "blok"):
                out.append((nama, ast.unparse(n.args[0]) if n.args else None, ast.unparse(n.args[1]) if nama == "galat" and len(n.args) > 1 else None))
    return sorted(out, key=str)


gagal = 0
for f in BERKAS:
    a, b = jejak(sys.argv[1], f), jejak(sys.argv[2], f)
    ok = a == b
    gagal += not ok
    print(("SAMA " if ok else "BEDA ") + f"{f}: {len(a)} panggilan" + ("" if ok else f" | LAMA-saja {dict(Counter(map(str, a)) - Counter(map(str, b)))} BARU-saja {dict(Counter(map(str, b)) - Counter(map(str, a)))}"))
sys.exit(1 if gagal else 0)
