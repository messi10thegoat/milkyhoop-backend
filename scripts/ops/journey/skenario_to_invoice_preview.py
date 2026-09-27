"""to-invoice/preview + to-invoice {post:true} (27 Sep 2026, BACKEND; CW "Buat faktur" WORKSPACE).
Di salinan kaos lewat API nyata. Tiap pratinjau: potret SEMUA tabel public (jumlah baris) + nilai sequence
SEBELUM == SESUDAH (nol tulisan). Paritas pratinjau == hasil buat+terbit (total, pajak, diskon, ongkir, jatuh
tempo+sumber, baris, uang muka per deposit, sisa sesudah DP) pada:
  A  qty parsial + diskon/ongkir SO pro-rata + termin NET 14 SO + uang muka 400rb; idempotensi (replay/409); notes
  A2 sisa (tanpa items) -> faktur terakhir menyerap sisa diskon/ongkir; Σ faktur == total SO
  B  uang muka > faktur (dibatasi sisa tagihan)
  C  SO tanpa termin -> termin pelanggan/bawaan
  D  tanpa post: draf; rencana DP pratinjau == GET /deposit-plan faktur draf
  E  ATOMIK: penerapan uang muka digagalkan di tengah posting -> 500 dan NOL baris baru di seluruh DB
  F  404 berkode SO_TIDAK_ADA; galat validasi pratinjau == galat to-invoice (qty melebihi)."""
import uuid
from datetime import timedelta
from decimal import Decimal as D

from journey_lib import NAMA, PELANGGAN, T
from skenario_kirim_non_stok import _barang

KAS = "28ec7814-f10e-4688-af93-ef82f1b6f71f"
SQL_POTRET = """
SELECT 'T:' || c.relname AS k,
       (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from public.%I', c.relname), false, true, '')))[1]::text AS v
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
UNION ALL
SELECT 'S:' || sequencename, COALESCE(last_value, 0)::text FROM pg_sequences WHERE schemaname = 'public'
"""


async def _potret(J):
    async with J.pool.acquire() as c:
        return {r["k"]: r["v"] for r in await c.fetch(SQL_POTRET)}


def _beda(a, b):
    return {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}


async def _so(J, nama, pid, terms=None, disc=0, ship=0, baris=((10, 100000), (5, 40000))):
    b = {"order_date": J.hari.isoformat(), "customer_id": PELANGGAN, "customer_name": NAMA,
         "expected_ship_date": (J.hari + timedelta(days=3)).isoformat(), "discount_amount": disc,
         "shipping_amount": ship,
         "items": [{"item_id": pid, "description": f"TES E2E TOINV {nama} {i}", "quantity": str(q), "unit_price": h}
                   for i, (q, h) in enumerate(baris)]}
    if terms:
        b["payment_terms"] = terms
    _, r = await J.langkah(f"{nama}_so", "POST", "/api/sales-orders", b,
                           headers={"X-Idempotency-Key": f"journey-{uuid.uuid4()}"})
    so = ((r or {}).get("data") or {}).get("id")
    await J.langkah(f"{nama}_confirm", "POST", f"/api/sales-orders/{so}/confirm")
    _, det = await J.langkah(f"{nama}_detail", "GET", f"/api/sales-orders/{so}")
    return so, [x["id"] for x in det["data"]["items"]]


async def _dp(J, nama, so, jumlah):
    await J.langkah(f"{nama}_dp", "POST", "/api/customer-deposits", {
        "customer_id": PELANGGAN, "customer_name": NAMA, "amount": jumlah, "deposit_date": J.hari.isoformat(),
        "payment_method": "cash", "account_id": KAS, "sales_order_id": so, "auto_post": True,
        "idempotency_key": f"journey-dp-{uuid.uuid4()}"})


async def _pratinjau(J, nama, so, body, harap=(200,)):
    sebelum = await _potret(J)
    st, r = await J.langkah(f"{nama}_preview", "POST", f"/api/sales-orders/{so}/to-invoice/preview", body, harap=harap)
    beda = _beda(sebelum, await _potret(J))
    if beda:
        J.gagal(f"{nama}_nol_tulisan", f"pratinjau MENULIS: {dict(list(beda.items())[:8])}")
    return st, r


def _n(v):
    return D(str(v)).quantize(D("0.01")) if v is not None else None


async def _paritas(J, nama, pv, hasil):
    p = pv["data"]
    inv = hasil["data"]["invoice_id"]
    _, det = await J.langkah(f"{nama}_faktur", "GET", f"/api/sales-invoices/{inv}")
    x = det.get("data", det)
    beda = []
    for kp, kx in (("total", "total_amount"), ("tax", "tax_amount"), ("subtotal", "subtotal"),
                   ("discount", "discount_amount"), ("shipping", "shipping_amount")):
        if _n(p[kp]) != _n(x.get(kx) or 0):
            beda.append(f"{kp}: pratinjau {p[kp]} != faktur {x.get(kx)}")
    if p["due_date"] != str(x.get("due_date"))[:10] or p["due_date"] != hasil["data"]["due_date"]:
        beda.append(f"due_date {p['due_date']} != {x.get('due_date')}/{hasil['data']['due_date']}")
    if p["due_date_source"] != hasil["data"]["due_date_source"]:
        beda.append(f"due_date_source {p['due_date_source']} != {hasil['data']['due_date_source']}")
    bx = [(_n(i.get("quantity")), _n(i.get("unit_price"))) for i in (x.get("items") or [])]
    bp = [(_n(l["qty"]), _n(l["unit_price"])) for l in p["lines"]]
    if bx != bp:
        beda.append(f"baris {bp} != {bx}")
    dpp = sorted((d["deposit_id"], _n(d["amount"])) for d in p["deposit_plan"])
    dph = sorted((d["deposit_id"], _n(d["amount"])) for d in hasil["data"]["applied_deposits"])
    if dpp != dph:
        beda.append(f"uang muka {dpp} != {dph}")
    if _n(p["remaining_after_dp"]) != _n(D(str(p["total"])) - D(str(hasil["data"]["total_applied"]))):
        beda.append(f"sisa sesudah DP {p['remaining_after_dp']} vs total-applied")
    if beda:
        J.gagal(f"{nama}_paritas", "; ".join(beda))
    else:
        print(f"PARITAS {nama}: total {p['total']} pajak {p['tax']} jt {p['due_date']} ({p['due_date_source']}) "
              f"DP {dpp} sisa {p['remaining_after_dp']}", flush=True)
    return x


async def jalankan(J):
    pid = await _barang(J, "TOINV", False)
    if not pid:
        return
    # ---------------- A: parsial + diskon/ongkir + NET 14 + DP + idempotensi + notes
    so, (l1, l2) = await _so(J, "A", pid, terms="NET 14", disc=50000, ship=30000)
    await _dp(J, "A", so, 400000)
    body = {"items": [{"so_item_id": l1, "quantity": 4}], "post": True, "notes": "TES E2E catatan faktur"}
    _, pv = await _pratinjau(J, "A", so, body)
    kunci = f"journey-toinv-{uuid.uuid4()}"
    n0 = (await _potret(J))["T:sales_invoices"]
    _, h = await J.langkah("A_to_invoice", "POST", f"/api/sales-orders/{so}/to-invoice", {**body, "idempotency_key": kunci})
    x = await _paritas(J, "A", pv, h)
    if h["data"].get("status") not in ("posted", "partial", "paid") or x.get("notes") != "TES E2E catatan faktur":
        J.gagal("A_status_notes", f"status {h['data'].get('status')} notes {x.get('notes')!r}")
    _, h2 = await J.langkah("A_replay", "POST", f"/api/sales-orders/{so}/to-invoice", {**body, "idempotency_key": kunci})
    if h2["data"]["invoice_id"] != h["data"]["invoice_id"] or int((await _potret(J))["T:sales_invoices"]) != int(n0) + 1:
        J.gagal("A_replay_satu_faktur", f"{h2['data'].get('invoice_id')} vs {h['data']['invoice_id']}")
    _, r409 = await J.langkah("A_kunci_isi_beda_409", "POST", f"/api/sales-orders/{so}/to-invoice",
                              {**body, "items": [{"so_item_id": l1, "quantity": 1}], "idempotency_key": kunci}, harap=(409,))
    if ((r409 or {}).get("detail") or {}).get("code") != "IDEMPOTENCY_KEY_REUSED":
        J.gagal("A_409_kode", str(r409)[:200])
    # ---------------- A2: sisa -> faktur terakhir menyerap sisa
    _, pv2 = await _pratinjau(J, "A2", so, {"post": True})
    _, h3 = await J.langkah("A2_to_invoice", "POST", f"/api/sales-orders/{so}/to-invoice", {"post": True})
    await _paritas(J, "A2", pv2, h3)
    _, sod = await J.langkah("A2_so", "GET", f"/api/sales-orders/{so}")
    jml = _n(D(str(pv["data"]["total"])) + D(str(pv2["data"]["total"])))
    if jml != _n(sod["data"]["total_amount"]):
        J.gagal("A2_sigma_faktur_total_so", f"Σ faktur {jml} != SO {sod['data']['total_amount']}")
    # ---------------- B: DP > faktur
    # DP 200rb (<= total SO 210rb: plafon DP), faktur hanya baris 10rb -> DP dibatasi sisa tagihan 10rb
    so_b, (b1, b2) = await _so(J, "B", pid, baris=((2, 100000), (1, 10000)))
    await _dp(J, "B", so_b, 200000)
    bb = {"items": [{"so_item_id": b2}], "post": True}
    _, pvb = await _pratinjau(J, "B", so_b, bb)
    _, hb = await J.langkah("B_to_invoice", "POST", f"/api/sales-orders/{so_b}/to-invoice", bb)
    await _paritas(J, "B", pvb, hb)
    if _n(pvb["data"]["remaining_after_dp"]) != _n(0) or _n(pvb["data"]["deposit_plan"][0]["amount"]) != _n(pvb["data"]["total"]):
        J.gagal("B_dp_dibatasi", str(pvb["data"])[:300])
    # ---------------- C: tanpa termin SO
    so_c, _ = await _so(J, "C", pid, baris=((1, 75000),))
    _, pvc = await _pratinjau(J, "C", so_c, {"post": True})
    _, hc = await J.langkah("C_to_invoice", "POST", f"/api/sales-orders/{so_c}/to-invoice", {"post": True})
    await _paritas(J, "C", pvc, hc)
    if pv["data"]["due_date_source"] == pvc["data"]["due_date_source"]:
        J.gagal("C_sumber_termin_beda", f"A {pv['data']['due_date_source']} == C {pvc['data']['due_date_source']}")
    # ---------------- D: tanpa post (draf)
    so_d, _ = await _so(J, "D", pid, baris=((3, 50000),))
    await _dp(J, "D", so_d, 60000)
    _, pvd = await _pratinjau(J, "D", so_d, {})
    _, hd = await J.langkah("D_to_invoice", "POST", f"/api/sales-orders/{so_d}/to-invoice", {})
    if hd["data"].get("status") != "draft" or hd["data"].get("applied_deposits"):
        J.gagal("D_draf", str(hd["data"])[:200])
    _, plan = await J.langkah("D_deposit_plan", "GET", f"/api/sales-invoices/{hd['data']['invoice_id']}/deposit-plan")
    pl = (plan or {}).get("data") or plan
    pl = pl.get("plan", pl.get("deposits", pl)) if isinstance(pl, dict) else pl
    dg = sorted((str(p["deposit_id"]), _n(p.get("planned_amount"))) for p in (pl or []) if _n(p.get("planned_amount") or 0) > 0)
    dpv = sorted((d["deposit_id"], _n(d["amount"])) for d in pvd["data"]["deposit_plan"])
    if dg != dpv or _n(pvd["data"]["total"]) != _n(hd["data"].get("total", pvd["data"]["total"])):
        J.gagal("D_rencana_dp", f"pratinjau {dpv} != deposit-plan {dg}")
    # ---------------- E: atomik
    so_e, _ = await _so(J, "E", pid, baris=((2, 90000),))
    await _dp(J, "E", so_e, 50000)
    SI = J.mod("routers.sales_invoices")
    asli = SI._auto_apply_so_deposits

    async def _gagal(*a, **k):
        raise RuntimeError("SABOTASE journey: penerapan uang muka gagal di tengah posting")
    sebelum = await _potret(J)
    SI._auto_apply_so_deposits = _gagal
    try:
        await J.langkah("E_to_invoice_gagal", "POST", f"/api/sales-orders/{so_e}/to-invoice", {"post": True}, harap=(500,))
    finally:
        SI._auto_apply_so_deposits = asli
    beda = {k: v for k, v in _beda(sebelum, await _potret(J)).items() if k.startswith("T:")}
    if beda:
        J.gagal("E_atomik_nol_baris", f"gagal di tengah meninggalkan baris: {beda}")
    # ---------------- F: 404 berkode + validasi sama
    _, r404 = await J.langkah("F_404", "POST", f"/api/sales-orders/{uuid.uuid4()}/to-invoice/preview", {}, harap=(404,))
    if ((r404 or {}).get("detail") or {}).get("code") != "SO_TIDAK_ADA":
        J.gagal("F_404_kode", str(r404)[:200])
    lebih = {"items": [{"so_item_id": b2, "quantity": 99}], "post": True}
    _, e1 = await J.langkah("F_preview_lebih", "POST", f"/api/sales-orders/{so_b}/to-invoice/preview", lebih, harap=(400,))
    _, e2 = await J.langkah("F_toinv_lebih", "POST", f"/api/sales-orders/{so_b}/to-invoice", lebih, harap=(400,))
    if (e1 or {}).get("detail") != (e2 or {}).get("detail"):
        J.gagal("F_galat_sama", f"{e1} != {e2}")
