import asyncio, sys, uuid

sys.path.insert(0, ".")
from conftest import TestSuite

INV = "INV-2604-0028"
BILL = "PB-2604-0039"
TESTS = [
    ("VOID_INV", f"void faktur {INV}, alasan salah ketik"),
    ("VOID_INV2", f"batalkan faktur penjualan {INV}"),
    ("VOID_BILL", f"void faktur pembelian {BILL}, alasan dibatalkan vendor"),
    ("VOID_BILL2", f"batalkan bill {BILL}"),
    (
        "REG_QUOTE",
        "bikin penawaran untuk Sintia, jaket Bomber x 100 @ Rp 255.000, pajak 12 persen, hari ini",
    ),
    (
        "REG_INV",
        "bikin faktur penjualan untuk Sintia, jaket Bomber x 10 @ Rp 255.000, pajak 12 persen, hari ini",
    ),
    ("REG_RCV", "terima pembayaran dari Sintia Rp 500.000 via BCA hari ini"),
    ("VOID_BAD", "void faktur INV-9999-9999, alasan test"),
]


async def run():
    s = TestSuite()
    await s.get_token()
    for label, text in TESTS:
        cid = str(uuid.uuid4())
        r = await s.send(text, conversation_id=cid)
        d = r.get("data") or {}
        p = d.get("payload") or {}
        mtype = r.get("message_type")
        ak = d.get("action_key")
        rid = p.get("id") or p.get("sales_invoice_id") or p.get("bill_id")
        lat = r.get("latency_ms")
        print(f"{label}: mtype={mtype} action={ak} id_resolved={bool(rid)} lat={lat}ms")
        txt = r.get("text") or ""
        if mtype != "DIRECT_ACTION_PREVIEW":
            print("   text:", txt[:180])
        else:
            print("   payload_keys:", list(p.keys()))


asyncio.run(run())
