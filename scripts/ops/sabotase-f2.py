"""Sabotase F2 pada SALINAN pohon (argumen: <akar backend/api_gateway> <S1..S4>). Tiap sabotase WAJIB membuat tes F2 merah."""
import sys

akar, s = sys.argv[1], sys.argv[2]
p = f"{akar}/app/services/tagihan_aksi.py"
t = open(p).read()
GANTI = {
    "S1": ("""               UNION ALL
               SELECT vc.credit_number, true FROM vendor_credits vc
               WHERE vc.tenant_id = $2 AND vc.original_bill_id = $1 AND vc.status NOT IN ('draft', 'void')
""", ""),                                                                          # cabang tertaut dibuang (= kode lama)
    "S2": ("vc.status NOT IN ('draft', 'void')", "vc.status NOT IN ('void')"),    # draf ikut memblok
    "S3": ("WHERE vc.tenant_id = $2 AND vc.original_bill_id = $1", "WHERE vc.original_bill_id = $1"),  # lintas tenant
    "S4": ('"Batalkan nota debit itu dulu" if any(r["tertaut"] for r in nd) else ', ""),  # langkah selalu "Lepas"
}
lama, baru = GANTI[s]
if t.count(lama) != 1:
    sys.exit(f"pola {s} n={t.count(lama)}")
open(p, "w").write(t.replace(lama, baru))
print(f"sabotase {s} terpasang")
