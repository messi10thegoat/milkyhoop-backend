"""Sabotase GRN-SENT-NONE pada SALINAN pohon (argumen: <akar backend/api_gateway> <S1..S3>). Tiap sabotase WAJIB merah."""
import sys

akar, s = sys.argv[1], sys.argv[2]
A = {
    "S1": ("app/services/grn_daftar.py", '"sent": {"state": "none", "sent_at": None, "viewed_at": None},  # GRN-SENT-NONE',
           '"sent": {"state": "not_sent", "sent_at": None, "viewed_at": None},  # GRN-SENT-NONE'),          # regresi asli
    "S2": ("app/services/po_daftar.py", '"sent": ({"state": "sent", "sent_at": r["sent_at"].isoformat()',
           '"sent": ({"state": "unsent", "sent_at": r["sent_at"].isoformat()'),                               # modul lain, kata lain
    "S3": ("app/services/nota_debit_daftar.py", '"sent": {"state": "none", "sent_at": None, "viewed_at": None}, "batal"',
           '"sent": {"state": "belum", "sent_at": None, "viewed_at": None}, "batal"'),
}
f, lama, baru = A[s]
p = f"{akar}/{f}"
t = open(p).read()
if t.count(lama) != 1:
    sys.exit(f"pola {s} n={t.count(lama)}")
open(p, "w").write(t.replace(lama, baru))
print(f"sabotase {s} terpasang")
