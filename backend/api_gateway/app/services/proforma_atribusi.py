"""Atribusi uang muka SO ke proforma — SATU aturan untuk plafon tagihan, PDF proforma, dan "Sudah Dibayar"
(putusan MASTER 28 Sep 2026, celah proforma 1+2).

Data nyata jarang menautkan uang muka ke proforma (grapgrap 2 dari 44), jadi:
  1. TAUTAN MENANG: uang muka ber-proforma_id = milik proforma itu.
  2. PENCOCOKAN NOMINAL (heuristik untuk data TAK TERTAUT): uang muka tanpa proforma_id, urut waktu dibuat, dipasangkan ke
     proforma 'issued' SO yang sama (urut issued_at, nomor) yang SISA TERBUKANYA (amount - tertaut) PERSIS sama nominalnya.
     Tiap uang muka & tiap proforma dipasangkan paling banyak SEKALI.
  3. Sisanya = uang DITERIMA DI LUAR TAGIHAN (tak_tertagih).
Terukur 28 Sep pada 254 SO nyata: hasil plafon = rumus max(issued, diterima) di semua SO; berbeda HANYA pada kasus "DP
dulu, lalu proforma ditagih NETO darinya" — di situ pencocokan benar (sisa 0), max() salah (repro journey).
Perbaikan sejati = MENAUTKAN saat uang muka dicatat (tiket FE/WORKSPACE); tak ada backfill tautan (putusan pemilik).
Uang = Decimal (Law 25). Murni: tanpa DB; pemuat ada di muat_atribusi().
"""
from decimal import Decimal

NOL = Decimal("0")
TOL = Decimal("0.005")


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else NOL


def atribusikan(proformas: list, deposits: list) -> dict:
    """proformas: [{id, amount, status, issued_at, proforma_number}] satu SO.
    deposits: [{id, amount, proforma_id, created_at}] uang muka SO berstatus BUKAN void/draft.
    -> {"per_proforma": {pid: {"tertaut", "dicocokkan"}}, "pasangan": {dep_id: pid},
        "tak_tertagih": Decimal, "dep_tak_tertagih": [dep_id], "diterima": Decimal}"""
    ids = {p["id"] for p in proformas}
    per = {p["id"]: {"tertaut": NOL, "dicocokkan": NOL} for p in proformas}
    diterima = NOL
    bebas, tautan = [], {}
    for d in deposits:
        amt = _d(d["amount"])
        diterima += amt
        if d.get("proforma_id") is not None:
            if d["proforma_id"] in ids:
                per[d["proforma_id"]]["tertaut"] += amt
                tautan[d["id"]] = d["proforma_id"]
            # tertaut ke proforma di luar daftar (mis. dikecualikan pemanggil) -> bukan uang bebas, tak dihitung ulang
            continue
        bebas.append(d)
    kandidat = sorted(
        (p for p in proformas if p.get("status") == "issued"),
        key=lambda p: (p.get("issued_at") is None, p.get("issued_at"), p.get("proforma_number") or ""),
    )
    terpakai, pasangan, tak, dep_tak = set(), {}, NOL, []
    for d in sorted(bebas, key=lambda d: (d.get("created_at") is None, d.get("created_at"), str(d["id"]))):
        amt = _d(d["amount"])
        for p in kandidat:
            if p["id"] in terpakai:
                continue
            if abs((_d(p["amount"]) - per[p["id"]]["tertaut"]) - amt) <= TOL:
                per[p["id"]]["dicocokkan"] += amt
                terpakai.add(p["id"])
                pasangan[d["id"]] = p["id"]
                break
        else:
            tak += amt
            dep_tak.append(d["id"])
    return {"per_proforma": per, "pasangan": pasangan, "tak_tertagih": tak, "dep_tak_tertagih": dep_tak,
            "diterima": diterima, "tautan": tautan,
            "nomor": {p["id"]: p.get("proforma_number") for p in proformas}}


def atribusi_uang_muka(hasil: dict, dep_id) -> dict:
    """Atribusi SATU uang muka dari hasil atribusikan(): tautan langsung menang, lalu hasil pencocokan nominal,
    selain itu di luar tagihan (null). Uang muka draf/void tak ada di hasil -> null."""
    if dep_id in hasil["tautan"]:
        pid, cara = hasil["tautan"][dep_id], "tautan"
    elif dep_id in hasil["pasangan"]:
        pid, cara = hasil["pasangan"][dep_id], "cocok"
    else:
        return {"attributed_proforma_id": None, "attributed_proforma_number": None, "attribution": None}
    return {"attributed_proforma_id": str(pid), "attributed_proforma_number": hasil["nomor"].get(pid),
            "attribution": cara}


SQL_UANG_MUKA_SO = """
SELECT cd.id, cd.deposit_number, cd.deposit_date, cd.amount, cd.status, cd.payment_method, cd.proforma_id,
       b.account_name, b.bank_name, b.account_number, b.account_holder_name, coa.name AS coa_name
FROM customer_deposits cd
LEFT JOIN LATERAL (
    SELECT ba.account_name, ba.bank_name, ba.account_number, ba.account_holder_name FROM bank_accounts ba
    WHERE ba.tenant_id = cd.tenant_id
      AND (ba.id = cd.bank_account_id OR (cd.bank_account_id IS NULL AND ba.coa_id = cd.account_id))
    ORDER BY (ba.id = cd.bank_account_id) DESC NULLS LAST, ba.is_active DESC LIMIT 1
) b ON true
LEFT JOIN chart_of_accounts coa ON coa.id = cd.account_id AND coa.tenant_id = cd.tenant_id
WHERE cd.tenant_id = $1 AND cd.sales_order_id = $2 AND cd.status <> 'void'
ORDER BY cd.created_at, cd.id
"""


def label_rekening(r) -> str | None:
    """Rekening penerima dalam format blok bayar PDF: 'BCA 8295032185 a.n. <pemilik>' (pemilik lewat
    faktur_cetak.pemilik_rekening dari account_holder_name; nama akun TIDAK dicetak sebagai pemilik). Kas tanpa
    nomor -> nama akun Kas & Bank; tanpa akun Kas & Bank -> nama CoA."""
    from . import faktur_cetak
    inti = " ".join(x.strip() for x in (r["bank_name"], r["account_number"]) if x and x.strip() and x.strip() != "-")
    if inti and r["account_number"] and r["account_number"].strip() not in ("", "-"):
        an = faktur_cetak.pemilik_rekening(r["bank_name"], None, r["account_holder_name"])
        return f"{inti} a.n. {an}" if an else inti
    return r["account_name"] or r["coa_name"]


async def uang_muka_so(conn, tenant_id: str, so_id) -> list:
    """deposits[] detail SO (bukan void) + atribusi dari muat_atribusi — SUMBER YANG SAMA dengan plafon tagihan,
    PDF proforma dan 'Sudah Dibayar'."""
    rows = await conn.fetch(SQL_UANG_MUKA_SO, tenant_id, so_id)
    hasil = (await muat_atribusi(conn, tenant_id, [so_id])).get(so_id) if rows else None
    out = []
    for r in rows:
        atr = atribusi_uang_muka(hasil, r["id"]) if hasil else atribusi_uang_muka(
            {"tautan": {}, "pasangan": {}, "nomor": {}}, r["id"])
        out.append({
            "id": str(r["id"]), "deposit_number": r["deposit_number"], "amount": r["amount"], "status": r["status"],
            "deposit_date": r["deposit_date"].isoformat() if r["deposit_date"] else None,
            "payment_method": r["payment_method"], "account_name": label_rekening(r),
            "proforma_id": str(r["proforma_id"]) if r["proforma_id"] else None,
            **atr,
        })
    return out


async def muat_atribusi(conn, tenant_id: str, so_ids: list, exclude_proforma_id=None) -> dict:
    """{so_id: hasil atribusikan()} — proforma SO (semua status; hanya 'issued' yang bisa dicocokkan) + uang muka SO
    (langsung ber-sales_order_id ATAU lewat proformanya) berstatus BUKAN void/draft. exclude_proforma_id: proforma itu
    & uang muka yang menunjuknya dikeluarkan (pagar: menghitung ulang plafon untuk proforma yang sedang diubah)."""
    so_ids = [s for s in {s for s in so_ids if s is not None}]
    if not so_ids:
        return {}
    pros = await conn.fetch(
        """SELECT id, sales_order_id, amount, status, issued_at, proforma_number FROM proformas
           WHERE tenant_id = $1 AND sales_order_id = ANY($2::uuid[])
             AND ($3::uuid IS NULL OR id <> $3::uuid)""",
        tenant_id, so_ids, exclude_proforma_id,
    )
    deps = await conn.fetch(
        """SELECT cd.id, cd.amount, cd.proforma_id, cd.created_at,
                  COALESCE(cd.sales_order_id, p.sales_order_id) AS so_id
           FROM customer_deposits cd
           LEFT JOIN proformas p ON p.id = cd.proforma_id AND p.tenant_id = cd.tenant_id
           WHERE cd.tenant_id = $1 AND cd.status NOT IN ('void', 'draft')
             AND (cd.sales_order_id = ANY($2::uuid[]) OR p.sales_order_id = ANY($2::uuid[]))
             AND ($3::uuid IS NULL OR cd.proforma_id IS DISTINCT FROM $3::uuid)""",
        tenant_id, so_ids, exclude_proforma_id,
    )
    per_so_p, per_so_d = {s: [] for s in so_ids}, {s: [] for s in so_ids}
    for p in pros:
        per_so_p[p["sales_order_id"]].append(dict(p))
    for d in deps:
        if d["so_id"] in per_so_d:
            per_so_d[d["so_id"]].append(dict(d))
    return {s: atribusikan(per_so_p[s], per_so_d[s]) for s in so_ids}
