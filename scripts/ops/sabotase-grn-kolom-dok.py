"""Sabotase L5b pada SALINAN pohon (argumen: <akar backend/api_gateway> <C1..C4>). Tiap sabotase WAJIB merah."""
import sys

akar, s = sys.argv[1], sys.argv[2]
A = {
    "C1": ("app/routers/goods_receipts.py", 'kel = await GD.kelompok_grn_banyak(conn, ctx["tenant_id"], rows)',
           'kel = {str(r["id"]): await GD.kelompok_grn(conn, ctx["tenant_id"], r) for r in rows}'),                 # N+1
    "C2": ("app/services/grn_daftar.py", "ON l.goods_receipt_item_id = i.id AND l.status = 'active'", "ON l.goods_receipt_item_id = i.id"),
    "C3": ("app/services/grn_daftar.py", 'for g in kelompok if g["key"] != "rekap" for d in g["docs"]]', 'for g in kelompok for d in g["docs"]]'),
    "C4": ("app/services/grn_daftar.py", "FROM purchase_orders WHERE tenant_id = $1 AND id = ANY($2::uuid[])",
           "FROM purchase_orders WHERE $1::text IS NOT NULL AND id = ANY($2::uuid[])"),
}
f, lama, baru = A[s]
p = f"{akar}/{f}"
t = open(p).read()
if t.count(lama) != 1:
    sys.exit(f"pola {s} n={t.count(lama)}")
open(p, "w").write(t.replace(lama, baru))
print(f"sabotase {s} terpasang")
