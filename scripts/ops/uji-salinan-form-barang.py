"""FORM BARANG CW (18 Nov 2026) -- uji di SALINAN: stok awal PER GUDANG (satu jurnal + satu baris kartu 'Saldo awal' per gudang), pratinjau == tulis, form-bawaan, pratinjau Ubah ber-teks.
Handler ASLI. Tulis HANYA di salinan (nama DB dicek). Tenant bersih (tanpa transaksi) dipilih otomatis; tenant berjalan diuji hanya untuk blok."""
import asyncio
import json
import os
import sys
import uuid
from decimal import Decimal as D

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as IT  # noqa: E402
from app.schemas.items import CreateItemRequest  # noqa: E402

CEK = []


def cek(nama, ok, info=""):
    CEK.append([nama, bool(ok), "" if ok else str(info)[:600]])
    print(("[ok] " if ok else "[GAGAL] ") + nama + ("" if ok else " :: " + str(info)[:300]), flush=True)


def req(tid, uid):
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""})
    r.state.user = {"tenant_id": tid, "user_id": str(uid) if uid else None, "username": "uji-salinan-form-barang", "role": "OWNER"}
    return r


def d(x):
    return x["data"] if isinstance(x, dict) else x.data


async def tenant_bersih(conn):
    for r in await conn.fetch(
            """SELECT t.id FROM "Tenant" t WHERE NOT EXISTS (SELECT 1 FROM journal_entries je WHERE je.tenant_id = t.id AND je.status = 'POSTED'
               AND je.source_type NOT IN ('OPENING', 'OPENING_BALANCE', 'OPENING_BALANCE_REVERSAL'))
               AND EXISTS (SELECT 1 FROM warehouses w WHERE w.tenant_id = t.id AND w.is_active AND NOT w.is_system)
               AND (SELECT COUNT(*) FROM account_roles ar WHERE ar.tenant_id = t.id AND ar.role_key IN ('INVENTORY_MERCHANDISE', 'EQUITY_OPENING_BALANCE')) = 2 ORDER BY t.id"""):
        return r["id"]
    return None


async def bersih(conn, tid):
    uid = await conn.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', tid)
    g1 = await conn.fetchrow("SELECT id, name FROM warehouses WHERE tenant_id = $1 AND is_active AND NOT is_system ORDER BY is_default DESC, created_at LIMIT 1", tid)
    g2 = await conn.fetchrow(
        "INSERT INTO warehouses (tenant_id, code, name, is_active) VALUES ($1, $2, $3, true) RETURNING id, name", tid, f"UJI{uuid.uuid4().hex[:4].upper()}", "Gudang UJI B")
    r = req(tid, uid)
    fb = d(await IT.form_bawaan_barang(r))
    cek(f"{tid} form-bawaan: gudang >= 2, stok awal tersedia, tanggal bawaan, berlaku barang+jasa", len(fb["gudang"]) >= 2 and fb["stok_awal"]["tersedia"] is True and fb["stok_awal"]["tanggal_bawaan"]
        and "berlaku_barang" in fb and "berlaku_jasa" in fb and fb["gudang_bawaan_id"], fb)
    nama = f"UJI-FB-{uuid.uuid4().hex[:6]}"
    kon = [{"conversion_unit": "UJI-Dus", "conversion_factor": 12}]
    isi = lambda **kw: {"name": nama, "base_unit": "Pcs", "item_type": "goods", "track_inventory": True, "purchase_price": 1000, "sales_price": 2000, "conversions": kon,
                        "stok_awal": [{"warehouse_id": str(g1["id"]), "quantity": 2, "unit": "UJI-Dus", "unit_cost": 12000}, {"warehouse_id": str(g2["id"]), "quantity": 5}], **kw}
    pv = d(await IT.preview_create_item(r, isi()))
    cek(f"{tid} pratinjau Buat: ok + 2 efek per gudang + peringatan jurnal + berlaku", pv["ok"] and len(pv["rencana"]["efek"]) == 2 and pv["rencana"]["efek"][0]["qty_text"] == "2 UJI-Dus = 24 Pcs"
        and pv["rencana"]["peringatan"] and "Ekuitas Saldo Awal" in pv["rencana"]["peringatan"][0] and pv["rencana"]["berlaku"] and pv["rencana"]["stok_awal_total_text"], pv)
    bad = d(await IT.preview_create_item(r, isi(stok_awal=[{"warehouse_id": str(g1["id"]), "quantity": 1, "unit": "UJI-Tak-Ada"}, {"warehouse_id": str(g1["id"]), "quantity": 1}])))
    kd = [b["code"] for b in bad["blocks"]]
    cek(f"{tid} pratinjau: SEMUA blok stok awal sekaligus (satuan tak dikenal + gudang ganda?)", not bad["ok"] and "SATUAN_TAK_TERKONVERSI" in kd and any(b.get("field") == "stok_awal[0].unit" for b in bad["blocks"]), bad["blocks"])
    jasa = d(await IT.preview_create_item(r, isi(item_type="service", track_inventory=False, conversions=[])))
    cek(f"{tid} pratinjau jasa + stok_awal: STOK_AWAL_BUKAN_BARANG_STOK", not jasa["ok"] and "STOK_AWAL_BUKAN_BARANG_STOK" in [b["code"] for b in jasa["blocks"]], jasa)

    c = await IT.create_item(r, CreateItemRequest(**isi()))
    iid = uuid.UUID(str(d(c)["id"]))
    led = await conn.fetch("SELECT movement_type, warehouse_id, quantity_in, unit_cost, total_cost, average_cost, quantity_balance, journal_id FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2 ORDER BY created_at, id", tid, iid)
    per = {str(x["warehouse_id"]): x for x in led}
    cek(f"{tid} tulis: 2 baris kartu OPENING_BALANCE per gudang (24 & 5 satuan dasar)", len(led) == 2 and all(x["movement_type"] == "OPENING_BALANCE" for x in led)
        and D(per[str(g1["id"])]["quantity_in"]) == 24 and D(per[str(g2["id"])]["quantity_in"]) == 5, [dict(x) for x in led])
    cek(f"{tid} tulis: harga per satuan dasar (12000/12=1000) & WAC berjalan", D(per[str(g1["id"])]["unit_cost"]) == 1000 and D(per[str(g2["id"])]["average_cost"]) == 1000 and max(D(x["quantity_balance"]) for x in led) == 29, [dict(x) for x in led])
    jid = {x["journal_id"] for x in led}
    jl = await conn.fetch("SELECT l.debit, l.credit, c.account_code FROM journal_lines l JOIN journal_entries e ON e.id = l.journal_id AND e.tenant_id = $1 JOIN chart_of_accounts c ON c.id = l.account_id AND c.tenant_id = $1 WHERE e.id = ANY($2::uuid[])", tid, list(jid))
    cek(f"{tid} tulis: SATU jurnal, Dr = Cr = 29000 (24000+5000)", len(jid) == 1 and sum(D(x["debit"]) for x in jl) == 29000 == sum(D(x["credit"]) for x in jl) and len(jl) == 2, [dict(x) for x in jl])
    sa_doc = await conn.fetchval("SELECT count(*) FROM stock_adjustments WHERE tenant_id = $1 AND notes ILIKE '%' || $2 || '%'", tid, nama)
    cek(f"{tid} tulis: BUKAN dokumen Penyesuaian (0 dokumen)", sa_doc == 0, sa_doc)
    cek(f"{tid} pratinjau == tulis (efek: gudang, qty, nilai)", all(any(e["warehouse_id"] == str(x["warehouse_id"]) and D(e["qty"]) == D(x["quantity_in"]) and D(e["nilai"]) == D(x["total_cost"]) for e in pv["rencana"]["efek"]) for x in led), pv["rencana"]["efek"])
    h = await IT.get_item_history(r, iid, page=1, limit=20, warehouse_id=None, date_from=None, date_to=None)
    teks = [m.get("movement_type_text") or m.get("jenis_text") or m.get("type_text") for m in h["data"]]
    cek(f"{tid} kartu stok: baris saldo awal berteks 'Saldo awal'", len(h["data"]) == 2 and all(t == "Saldo awal" for t in teks), teks)
    ulang = None
    try:
        await IT.create_item(r, CreateItemRequest(**isi()))
        ulang = "lolos"
    except Exception as e:  # noqa: BLE001
        ulang = getattr(e, "status_code", type(e).__name__)
    cek(f"{tid} nama sama dua kali ditolak 409 (bukan stok awal ganda)", ulang == 409, ulang)
    # kompat: opening_stock lama (tanpa stok_awal) -> satu gudang bawaan
    kom = await IT.create_item(r, CreateItemRequest(name=nama + " lama", base_unit="Pcs", item_type="goods", track_inventory=True, purchase_price=1000, opening_stock=3, opening_stock_rate=1000))
    n_kom = await conn.fetchval("SELECT count(*) FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2 AND movement_type = 'OPENING_BALANCE'", tid, uuid.UUID(str(d(kom)["id"])))
    cek(f"{tid} kompat opening_stock lama: 1 baris saldo awal", n_kom == 1, n_kom)
    # pratinjau UBAH ber-teks: ganti akun penjualan ke akun pendapatan lain
    a_lain = await conn.fetchval("SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_type = 'REVENUE' AND is_active AND NOT is_header ORDER BY account_code DESC LIMIT 1", tid)
    eu = d(await IT.preview_ubah_barang(r, iid, {"sales_account_id": str(a_lain), "is_returnable": False}))
    pert = {p["medan"]: p for p in (eu["rencana"] or {}).get("perubahan", [])}
    cek(f"{tid} pratinjau Ubah: teks akun + boolean + peringatan kedepan", "sales_account_id" in pert and pert["sales_account_id"]["ke_text"] not in (None, "-", str(a_lain)) and pert["sales_account_id"]["medan_text"] == "Akun penjualan"
        and any(p["code"] == "AKUN_PAJAK_BERLAKU_KEDEPAN" for p in eu["peringatan"]), eu)


async def berjalan(conn, tid):
    r = req(tid, await conn.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', tid))
    fb = d(await IT.form_bawaan_barang(r))
    w = await conn.fetchval("SELECT id FROM warehouses WHERE tenant_id = $1 AND is_active AND NOT is_system LIMIT 1", tid)
    cek(f"{tid} (usaha berjalan) form-bawaan: stok awal TIDAK tersedia + alasan", fb["stok_awal"]["tersedia"] is False and "Penyesuaian" in (fb["stok_awal"]["alasan"] or ""), fb["stok_awal"])
    pv = d(await IT.preview_create_item(r, {"name": f"UJI-FB-{uuid.uuid4().hex[:6]}", "base_unit": "Pcs", "item_type": "goods", "track_inventory": True, "purchase_price": 1000,
                                            "stok_awal": [{"warehouse_id": str(w), "quantity": 1}]}))
    cek(f"{tid} (usaha berjalan) pratinjau: STOK_AWAL_USAHA_BERJALAN berkalimat", not pv["ok"] and "STOK_AWAL_USAHA_BERJALAN" in [b["code"] for b in pv["blocks"]], pv["blocks"])


async def utama():
    pool = await IT.get_pool()
    async with pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        if not db.startswith("scratch_fb_"):
            sys.exit(f"BUKAN salinan: {db}")
        t0 = await tenant_bersih(conn)
        if not t0:
            print("[GAGAL] tak ada tenant bersih (tanpa transaksi, gudang, peran akun) di salinan", flush=True)
            CEK.append(["tenant bersih", False, ""])
        else:
            await bersih(conn, t0)
        await berjalan(conn, "kaos-biru-konveksi")
    json.dump({"CEK": CEK}, open(os.environ["KELUAR"], "w"), indent=1)
    gagal = [c for c in CEK if not c[1]]
    print(f"[info] {len(CEK)} cek, {len(gagal)} gagal", flush=True)
    sys.exit(1 if gagal or not CEK else 0)

asyncio.run(utama())
