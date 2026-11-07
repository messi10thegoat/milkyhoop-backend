#!/usr/bin/env python3
"""BUG-046 uji NYATA kaos LEWAT HTTP ke kode LIVE (sesudah window): pratinjau -> create+bayar_tunai -> cek DB (paid, outstanding 0,
RCV posted, cermin bank 1, jurnal bayar == pratinjau) -> replay -> void RCV -> void INV -> NET 0 (bank ledger, bank_txn, stok, AR).
Akun = MH_UJI_* (/root/mh-akun-uji.env, Collaborator kaos; BUKAN owner). Baca DB = psql baca saja."""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from datetime import date
from decimal import Decimal

BASIS, UA, TID = "http://127.0.0.1:8001", "milkyhoop-gate/1.0", os.environ["MH_UJI_TENANT"]
HASIL = []


def cek(n, ok, info=""):
    HASIL.append(ok)
    print(("PASS " if ok else "FAIL ") + n + (f" -- {info}" if info else ""), flush=True)


def sql(q):
    out = subprocess.run(["docker", "exec", "milkyhoop-dev-postgres-1", "psql", "-U", "postgres", "-d", "milkydb", "-AtF|", "-c", q],
                         capture_output=True, text=True, check=True).stdout
    return [x for x in out.strip().splitlines() if x]


def http(tok, path, badan, kunci=None):
    h = {"Content-Type": "application/json", "User-Agent": UA}
    if tok:
        h["Authorization"] = "Bearer " + tok
    if kunci:
        h["X-Idempotency-Key"] = kunci
    rq = urllib.request.Request(BASIS + path, data=json.dumps(badan).encode(), headers=h)
    try:
        with urllib.request.urlopen(rq, timeout=60) as r:
            return r.status, json.loads(r.read()), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), dict(e.headers)


def main():
    _, d, _ = http(None, "/api/auth/login", {"email": os.environ["MH_UJI_EMAIL"], "password": os.environ["MH_UJI_SANDI"]})
    tok = d.get("data", d)["access_token"]
    cust = sql(f"SELECT id||'|'||nama FROM customers WHERE tenant_id='{TID}' AND COALESCE(is_active,true) ORDER BY created_at LIMIT 1")[0].split("|")
    st = sql(f"""SELECT ws.item_id||'|'||ws.warehouse_id||'|'||p.nama_produk||'|'||ws.quantity FROM warehouse_stock ws JOIN products p ON p.id=ws.item_id
                 WHERE ws.tenant_id='{TID}' AND ws.quantity>=3 AND p.item_type='goods' ORDER BY ws.quantity DESC LIMIT 1""")[0].split("|")
    ba = sql(f"SELECT id||'|'||coa_id||'|'||account_name FROM bank_accounts WHERE tenant_id='{TID}' AND is_active AND account_type='bank' LIMIT 1")[0].split("|")
    print(f"data: pelanggan={cust[1]} barang={st[2]} stok={st[3]} rekening={ba[2]}", flush=True)

    def saldo():
        led = sql(f"""SELECT COALESCE(SUM(jl.debit-jl.credit),0) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id
                     WHERE je.tenant_id='{TID}' AND je.status='POSTED' AND jl.account_id='{ba[1]}'""")[0]
        btx = sql(f"SELECT COALESCE(SUM(amount),0) FROM bank_transactions WHERE tenant_id='{TID}' AND bank_account_id='{ba[0]}'")[0]
        stok = sql(f"SELECT quantity FROM warehouse_stock WHERE tenant_id='{TID}' AND item_id='{st[0]}' AND warehouse_id='{st[1]}'")[0]
        return Decimal(led), Decimal(btx), Decimal(stok)

    badan = {"customer_id": cust[0].upper(), "customer_name": cust[1], "invoice_date": str(date.today()), "warehouse_id": st[1],
             "items": [{"item_id": st[0], "description": st[2], "quantity": 2, "unit_price": 25000}],
             "auto_post": True, "bayar_tunai": {"bank_account_id": ba[0], "reference": "UJI-NYATA-046"}}
    s0 = saldo()
    c, pv, _ = http(tok, "/api/sales-invoices/preview-journal", badan)
    cek("pratinjau 200 + blok pembayaran, tanpa peringatan", c == 200 and pv.get("pembayaran") and not pv.get("warnings"), f"{c} {pv.get('warnings')}")
    c, r, _ = http(tok, "/api/sales-invoices", {k: v for k, v in badan.items()}, None)
    cek("tanpa kunci -> 400 kalimat final", c == 400 and r.get("detail") == "Penjualan tunai butuh kunci idempotensi.", f"{c} {r.get('detail')}")
    k = "nyata046-" + uuid.uuid4().hex
    c, r, _ = http(tok, "/api/sales-invoices", badan, k)
    cek("create + bayar_tunai 201, status paid", c == 201 and r["data"]["status"] == "paid", f"{c} {r.get('message') or r.get('detail')}")
    if c != 201:
        print(f"HASIL {sum(HASIL)}/{len(HASIL)}"); sys.exit(1)
    inv, rp = r["data"]["id"], r["data"]["pembayaran"]
    out = sql(f"SELECT COALESCE((SELECT outstanding FROM compute_ar_outstanding('{TID}') WHERE invoice_id='{inv}'),0)")[0]
    cek("outstanding 0 (compute_ar_outstanding)", Decimal(out) == 0, out)
    rps = sql(f"SELECT status||'|'||journal_id FROM receive_payments WHERE id='{rp['id']}'")[0].split("|")
    cek("RCV posted", rps[0] == "posted", rps[0])
    cek("cermin bank_transactions tepat 1", sql(f"SELECT count(*) FROM bank_transactions WHERE journal_id='{rps[1]}'")[0] == "1")
    db = sorted((n, Decimal(a), Decimal(b)) for n, a, b in (x.split("|") for x in sql(
        f"SELECT coa.name, jl.debit, jl.credit FROM journal_lines jl JOIN chart_of_accounts coa ON coa.id=jl.account_id WHERE jl.journal_id='{rps[1]}'")))
    pvl = sorted((x["account_name"], Decimal(str(x["debit"])), Decimal(str(x["credit"]))) for x in pv["journal_lines"] if x["event"] == "PAYMENT")
    cek("jurnal bayar DB == pratinjau", db == pvl, f"db={db} pv={pvl}")
    s1 = saldo()
    cek("R9: delta ledger == delta bank_txn == total", s1[0] - s0[0] == s1[1] - s0[1] == Decimal(str(r["data"]["total_amount"])), f"{s1[0]-s0[0]} {s1[1]-s0[1]}")
    c2, r2, h2 = http(tok, "/api/sales-invoices", badan, k)
    cek("replay kunci sama: id sama", r2.get("data", {}).get("id") == inv, f"{c2} {h2.get('X-Idempotent-Replay') or h2.get('x-idempotent-replay')}")
    # --- NET 0
    c, v, _ = http(tok, f"/api/receive-payments/{rp['id']}/void", {"reason": "uji nyata BUG-046 net 0"})
    cek("void RCV", c == 200, f"{c} {v.get('detail') or v.get('message')}")
    c, v, _ = http(tok, f"/api/sales-invoices/{inv}/void", {"reason": "uji nyata BUG-046 net 0"})
    cek("void INV", c == 200, f"{c} {v.get('detail') or v.get('message')}")
    s2 = saldo()
    cek("NET 0: bank ledger, bank_txn, stok kembali", s2 == s0, f"sebelum={s0} sesudah={s2}")
    print(f"INV={r['data']['invoice_number']} RCV={rp['payment_number']}")
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


main()
