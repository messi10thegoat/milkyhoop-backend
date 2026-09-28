"""deposits[] detail SO + atribusi (28 Sep 2026) di salinan prod, lengan B (app penuh).
Paritas terhadap plafon tagihan (rincian_tagih, sumber GET /sales-orders/{id}/proformas billable_breakdown):
  Σ amount uang muka ber-attribution null & bukan draf == received_not_billed; setiap id atribusi = proforma SO itu.
kaos: SEMUA SO ber-uang-muka lewat RUTE NYATA (GET /api/sales-orders/{id} + GET .../proformas).
grapgrap: 3 SO fixture (019-09-26, SO-2609-0001, 009-08-26) lewat layanan yang SAMA dipakai rute (uang_muka_so +
rincian_tagih) — token harness hanya kaos. Harapan literal: ketiganya uang mukanya 'cocok'."""
from decimal import Decimal

from journey_lib import T

GG = "grapgrap-manado"
GG_SO = {"019-09-26": "cocok", "SO-2609-0001": "cocok", "009-08-26": "cocok"}


def _nol_sum(deps):
    return sum((Decimal(str(d["amount"])) for d in deps if d.get("attribution") is None and d.get("status") != "draft"),
               Decimal(0))


async def jalankan(J):
    if J.lengan == "A":
        print("lengan A: dilewati")
        return
    async with J.pool.acquire() as c:
        sos = await c.fetch("""SELECT DISTINCT so.id FROM sales_orders so JOIN customer_deposits cd
                               ON cd.sales_order_id = so.id AND cd.tenant_id = so.tenant_id
                               WHERE so.tenant_id = $1 AND cd.status <> 'void'""", T)
    n, cocok, tautan, luar = 0, 0, 0, 0
    for r in sos:
        so = str(r["id"])
        _, d = await J.langkah(f"so_{so[:8]}", "GET", f"/api/sales-orders/{so}")
        deps = ((d or {}).get("data") or d or {}).get("deposits") or []
        _, p = await J.langkah(f"pf_{so[:8]}", "GET", f"/api/sales-orders/{so}/proformas")
        bb = (p or {}).get("billable_breakdown") or ((p or {}).get("data") or {}).get("billable_breakdown") or {}
        pf_ids = {x.get("id") for x in ((p or {}).get("data") or (p or {}).get("items") or []) if isinstance(x, dict)}
        if not bb:
            J.gagal(f"{so[:8]}_breakdown", str(p)[:160])
            continue
        if abs(float(_nol_sum(deps)) - float(bb["received_not_billed"])) > 0.005:
            J.gagal(f"{so[:8]}_paritas", f"null={_nol_sum(deps)} plafon={bb['received_not_billed']}")
        for x in deps:
            for k in ("deposit_date", "payment_method", "account_name", "attribution"):
                if k not in x:
                    J.gagal(f"{so[:8]}_medan_{k}", str(x)[:160])
            if x.get("attribution") == "cocok" and pf_ids and x["attributed_proforma_id"] not in pf_ids:
                J.gagal(f"{so[:8]}_id_asing", str(x)[:160])
            if x.get("attribution") and not x.get("attributed_proforma_number") and x["attribution"] == "cocok":
                J.gagal(f"{so[:8]}_nomor", str(x)[:160])
            cocok += x.get("attribution") == "cocok"
            tautan += x.get("attribution") == "tautan"
            luar += x.get("attribution") is None
        n += 1
    print(f"kaos: {n} SO ber-uang-muka lewat rute; atribusi cocok={cocok} tautan={tautan} null={luar}", flush=True)
    if n == 0 or cocok + tautan == 0:
        J.gagal("kaos_cakupan", f"n={n} cocok={cocok} tautan={tautan} (alat tak menyentuh atribusi)")

    catat = []
    PA = J.mod("services.proforma_atribusi")
    PF = J.mod("routers.proformas")
    async with J.pool.acquire() as c:
        for nomor, harap in GG_SO.items():
            so = await c.fetchrow("SELECT id, total_amount FROM sales_orders WHERE tenant_id=$1 AND order_number=$2", GG, nomor)
            if not so:
                J.gagal(f"gg_{nomor}_ada", "SO tak ada")
                continue
            deps = await PA.uang_muka_so(c, GG, so["id"])
            rb = await PF.rincian_tagih(c, GG, so["id"], float(so["total_amount"]))
            catat.append(f"grapgrap {nomor}: {[(x['deposit_number'], x['attribution'], x['attributed_proforma_number'], x['account_name']) for x in deps]} plafon_luar={rb['received_not_billed']}")
            if not deps or any(x["attribution"] != harap for x in deps if x["status"] != "draft"):
                J.gagal(f"gg_{nomor}_harap_{harap}", str([(x["deposit_number"], x["attribution"]) for x in deps]))
            if abs(float(_nol_sum(deps)) - float(rb["received_not_billed"])) > 0.005:
                J.gagal(f"gg_{nomor}_paritas", f"null={_nol_sum(deps)} plafon={rb['received_not_billed']}")
    with open("/out/so_uang_muka_ringkas.txt", "w") as f:
        f.write(f"kaos n={n} cocok={cocok} tautan={tautan} null={luar}\n" + "\n".join(catat) + "\n")
