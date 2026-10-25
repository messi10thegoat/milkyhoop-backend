"""Sabotase PO-IM pada SALINAN pohon (tiap ubahan diterapkan TEPAT sekali; dicek dulu). argv: <api_gateway> <S1|S2|M_kosong>
S1 = If-Match dipindah SEBELUM replay idem (bug AS-GP4b lama); S2 = If-Match dihapus (= kode lama); M_kosong = dihapus juga (kontrol merah harness)."""
import sys
p = sys.argv[1] + "/app/routers/purchase_orders.py"; m = sys.argv[2]
s = open(p).read()
baris = '                await assert_if_match_row(request, "purchase_orders", po_id)  # PO-IM: sesudah replay & kunci\n'
idem = '                _kp, _sd, _lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "PO_UPDATE"'
assert s.count(baris) == 1 and s.count(idem) == 1, "titik sabotase tak ditemukan tepat sekali"
s = s.replace(baris, "")
if m == "S1":
    s = s.replace(idem, baris + idem)
elif m not in ("S2", "M_kosong"):
    sys.exit("mode?")
open(p, "w").write(s)
print("terpasang", m)
