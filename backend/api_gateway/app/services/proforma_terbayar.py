"""Terbayar proforma = TURUNAN dari pesanan (SO) terkait (BUG-006, putusan MASTER/Anton 25 Sep 2026).

Dulu: terbayar = Σ customer_deposits.amount yang menunjuk proforma_id saja -> PELUNASAN yang dibayar
lewat FAKTUR SO (RCV) dan DP yang diterima tanpa memilih proforma = "Terbayar Rp 0" selamanya.

Sekarang, per SO (Iron Law 1/16 — journal-derived, bukan cache):
  R (tertutup SO) = Σ faktur SO non-void & bukan draf: total − outstanding (compute_ar_outstanding;
                    faktur tanpa baris = tertutup penuh — logika SAMA dengan _t43_dibayar_dan_sisa)
                  + Σ SISA JURNAL uang muka bertaut SO (langsung atau lewat proformanya), non-void
                    (compute_deposit_remaining_many — net CUSTOMER_DEPOSIT_LIABILITY per deposit).
  DP yang SUDAH diterapkan ke faktur: sisa jurnalnya 0 -> hanya terhitung lewat faktur (tak ganda).
  Kelebihan bayar (OVP) / lepas pembayaran (LPS) membuat deposit TANPA sales_order_id/proforma_id ->
  tak pernah masuk R.
Alokasi ke proforma SO itu:
  eksplisit_P = Σ amount deposit non-void yang menunjuk P (perilaku lama, eksplisit menang)
  kolam       = max(0, R − Σ eksplisit)   -> dialirkan berurutan (issued_at, nomor) ke proforma
                'issued' sampai masing-masing penuh (amount − eksplisit_P).
  paid_P = eksplisit_P + bagian_P.  Draf/batal/kedaluwarsa: eksplisit saja (belum/tidak ditagih).
PAGAR BATAL tetap memakai eksplisit saja (proformas.compute_paid_amount) — maknanya "ada uang muka
yang harus direfund dulu", bukan "pesanan sudah dibayar".
"""
from decimal import Decimal

NOL = Decimal("0")


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else NOL


def alokasikan(proformas: list, tertutup: Decimal, eksplisit: dict) -> dict:
    """Murni. proformas: [{id, amount, status, issued_at, proforma_number}] satu SO.
    -> {id: {"paid", "dari_uang_muka_langsung", "dari_pesanan"}} (Decimal)."""
    total_eks = sum((eksplisit.get(p["id"], NOL) for p in proformas), NOL)
    kolam = max(NOL, tertutup - total_eks)
    hasil = {}
    urut = sorted(
        (p for p in proformas if p["status"] == "issued"),
        key=lambda p: (p["issued_at"] is None, p["issued_at"], p["proforma_number"] or ""),
    )
    for p in proformas:
        e = eksplisit.get(p["id"], NOL)
        hasil[p["id"]] = {"paid": e, "dari_uang_muka_langsung": e, "dari_pesanan": NOL}
    for p in urut:
        kurang = max(NOL, _d(p["amount"]) - hasil[p["id"]]["paid"])
        bagian = min(kolam, kurang)
        if bagian > 0:
            hasil[p["id"]]["dari_pesanan"] = bagian
            hasil[p["id"]]["paid"] += bagian
            kolam -= bagian
    return hasil


async def tertutup_pesanan(conn, tenant_id: str, so_ids: list) -> dict:
    """{so_id: {"faktur": Decimal, "uang_muka_sisa": Decimal, "total": Decimal}} — journal-derived."""
    from ..routers.customer_deposits import compute_deposit_remaining_many

    so_ids = list({s for s in so_ids if s is not None})
    out = {s: {"faktur": NOL, "uang_muka_sisa": NOL, "total": NOL} for s in so_ids}
    if not so_ids:
        return out
    fakturs = await conn.fetch(
        """SELECT id, sales_order_id, total_amount, status FROM sales_invoices
           WHERE tenant_id = $1 AND sales_order_id = ANY($2::uuid[])
             AND status NOT IN ('void', 'draft')""",
        tenant_id, so_ids,
    )
    if fakturs:
        sisa = {r["invoice_id"]: _d(r["outstanding"]) for r in await conn.fetch(
            """SELECT invoice_id, outstanding FROM compute_ar_outstanding($1)
               WHERE invoice_id = ANY($2::uuid[])""",
            tenant_id, [f["id"] for f in fakturs],
        )}
        for f in fakturs:
            # tanpa baris outstanding = tertutup penuh (lunas lewat bayar / DP / nota kredit)
            out[f["sales_order_id"]]["faktur"] += _d(f["total_amount"]) - sisa.get(f["id"], NOL)
    deps = await conn.fetch(
        """SELECT cd.id, COALESCE(cd.sales_order_id, p.sales_order_id) AS so_id
           FROM customer_deposits cd
           LEFT JOIN proformas p ON p.id = cd.proforma_id AND p.tenant_id = cd.tenant_id
           WHERE cd.tenant_id = $1 AND cd.status <> 'void'
             AND (cd.sales_order_id = ANY($2::uuid[]) OR p.sales_order_id = ANY($2::uuid[]))""",
        tenant_id, so_ids,
    )
    if deps:
        sisa_dep = await compute_deposit_remaining_many(conn, tenant_id, [d["id"] for d in deps])
        for d in deps:
            if d["so_id"] in out:
                out[d["so_id"]]["uang_muka_sisa"] += max(NOL, sisa_dep[str(d["id"])])
    for s in out.values():
        s["total"] = s["faktur"] + s["uang_muka_sisa"]
    return out


async def terbayar_proforma(conn, tenant_id: str, so_ids: list) -> dict:
    """{proforma_id: {"paid": Decimal, "paid_breakdown": {...}}} untuk SEMUA proforma SO-SO itu."""
    so_ids = list({s for s in so_ids if s is not None})
    if not so_ids:
        return {}
    pros = await conn.fetch(
        """SELECT id, sales_order_id, amount, status, issued_at, proforma_number FROM proformas
           WHERE tenant_id = $1 AND sales_order_id = ANY($2::uuid[])""",
        tenant_id, so_ids,
    )
    eks = {r["proforma_id"]: _d(r["jml"]) for r in await conn.fetch(
        """SELECT proforma_id, SUM(amount) AS jml FROM customer_deposits
           WHERE tenant_id = $1 AND status <> 'void'
             AND proforma_id = ANY($2::uuid[]) GROUP BY 1""",
        tenant_id, [p["id"] for p in pros],
    )} if pros else {}
    tertutup = await tertutup_pesanan(conn, tenant_id, so_ids)
    per_so = {}
    for p in pros:
        per_so.setdefault(p["sales_order_id"], []).append(dict(p))
    hasil = {}
    for so_id, daftar in per_so.items():
        t = tertutup[so_id]
        for pid, a in alokasikan(daftar, t["total"], eks).items():
            hasil[pid] = {
                "paid": a["paid"],
                "paid_breakdown": {
                    "dari_uang_muka_langsung": float(a["dari_uang_muka_langsung"]),
                    "dari_pesanan": float(a["dari_pesanan"]),
                    "pesanan_tertutup": float(t["total"]),
                    "pesanan_tertutup_faktur": float(t["faktur"]),
                    "pesanan_uang_muka_belum_diterapkan": float(t["uang_muka_sisa"]),
                },
            }
    return hasil
