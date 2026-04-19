import asyncio
from conftest import TestSuite

PROMPTS = [
    (
        "QUOTE",
        "bikin penawaran untuk Sintia, jaket Bomber x 100 @ Rp 255.000, pajak 12 persen, hari ini",
    ),
    (
        "SO",
        "buat pesanan untuk Sintia, jaket Bomber x 50 @ Rp 255.000, pajak 12 persen, hari ini",
    ),
    (
        "INV",
        "bikin faktur penjualan untuk Sintia, jaket Bomber x 10 @ Rp 255.000, pajak 12 persen, hari ini",
    ),
    ("RCV", "terima pembayaran dari Sintia Rp 500.000 via BCA hari ini"),
    ("CN", "buat nota kredit untuk Sintia Rp 100.000, alasan barang rusak"),
    (
        "CUST",
        "tambah pelanggan baru nama PT Cakrawala Digital, email info@cakra.id, telepon 081234567",
    ),
]


async def run():
    s = TestSuite()
    for label, text in PROMPTS:
        try:
            r = await s.send(text)
            d = r.get("data") or {}
            p = d.get("payload") or {}
            rc = d.get("review_card") or {}
            ak = d.get("action_key")
            mtype = r.get("message_type")
            items = p.get("items")
            items_type = type(items).__name__ if items is not None else "None"
            items_len = len(items) if isinstance(items, list) else 0
            has_items = bool(rc.get("items"))
            has_totals = bool(rc.get("totals"))
            print(f"=== {label} === type={mtype} action={ak}")
            print(
                f"  items: type={items_type} len={items_len}  rc.items={has_items}  rc.totals={has_totals}"
            )
            if mtype != "DIRECT_ACTION_PREVIEW":
                txt = (r.get("text") or "")[:220]
                print(f"  text: {txt}")
        except Exception as e:
            print(f"{label} FAILED: {type(e).__name__}: {e}")


asyncio.run(run())
