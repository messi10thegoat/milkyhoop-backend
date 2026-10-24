"""Sabotase AS-G2e pada SALINAN pohon. argv: <akar api_gateway salinan> <nama>. Tiap ganti TEPAT sekali.
merah_riwayat  : penulis draf mencatat SALES_ORDER_UPDATED DUA kali (harness LAMA vs BARU + riwayat<=1 WAJIB merah)
merah_pratinjau: pratinjau draf mengabaikan kolom DP di after (harness pratinjau==PATCH WAJIB merah pada kasus dp)
U1 : pratinjau draf tanpa readonly (tes unit merah)  U2 : penentu draf menulis (tes unit merah)"""
import sys
from pathlib import Path

A = Path(sys.argv[1]) / "app/routers/sales_orders.py"
SAB = {
    "merah_riwayat": [('''            source="api:sales_orders.update",
        )


KUNCI_ANGKA_DRAF''', '''            source="api:sales_orders.update",
        )
        await catat_riwayat(
            conn, ctx["tenant_id"], "sales_orders", order["id"], None, "SALES_ORDER_UPDATED",
            ctx["user_id"], "Pesanan diubah (" + ", ".join(r["ubah"]) + ")", {"fields": r["ubah"]},
            source="api:sales_orders.update",
        )


KUNCI_ANGKA_DRAF''')],
    "merah_pratinjau": [('''            if f in after:
                after[f] = angka(v)''', '''            if f in after and not f.startswith("dp_"):
                after[f] = angka(v)''')],
    "U1": [('''        async with conn.transaction(readonly=True):
            draf = await conn.fetchrow(''', '''        async with conn.transaction():
            draf = await conn.fetchrow(''')],
    "U2": [('''    return {"kolom": kolom, "items": calculated_items if body.items is not None else None,''',
            '''    await conn.execute("UPDATE sales_orders SET updated_at = NOW() WHERE id = $1", so_id)
    return {"kolom": kolom, "items": calculated_items if body.items is not None else None,''')],
}
s = A.read_text()
for lama, baru in SAB[sys.argv[2]]:
    assert s.count(lama) == 1, (sys.argv[2], lama[:60], s.count(lama))
    s = s.replace(lama, baru)
A.write_text(s)
print(f"sabotase AS-G2e {sys.argv[2]} terpasang")
