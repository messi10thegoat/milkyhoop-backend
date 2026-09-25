"""Terbayar proforma & ringkasan pembayaran SO = TURUNAN JURNAL dari pesanan (BUG-006 / Q-011, 25 Sep 2026).

Dulu terbayar proforma = Σ customer_deposits.amount yang menunjuk proforma_id saja -> PELUNASAN yang
dibayar lewat FAKTUR SO (RCV) dan DP yang diterima tanpa memilih proforma = "Terbayar Rp 0" selamanya.

Per SO (Iron Law 1/16 — journal-derived, bukan cache/wrapper), SATU sumber: ringkasan_pesanan():
  invoiced            = Σ debit piutang jurnal INVOICE faktur SO non-void & bukan draf (sama dengan
                        invoice_debits di compute_ar_outstanding — bukan kolom total_amount)
  invoice_outstanding = Σ outstanding compute_ar_outstanding (faktur tanpa baris = 0)
  invoice_settled     = invoiced − invoice_outstanding (pembayaran + DP diterapkan + nota kredit − lepas)
  credit_note         = Σ kredit piutang jurnal CREDIT_NOTE ber-original_invoice_id faktur SO
                        (cermin cabang 2 compute_ar_outstanding) — menutup piutang TANPA uang masuk
  DP (deposit bertaut SO langsung ATAU lewat proformanya, non-void; jurnal ber-source_id deposit,
      akun CUSTOMER_DEPOSIT_LIABILITY, is_effective_journal):
    dp_received  = kredit − debit jurnal CUSTOMER_DEPOSIT
    dp_applied   = debit − kredit jurnal DEPOSIT_APPLICATION
    dp_refunded  = debit − kredit jurnal jenis lain (refund)
    dp_unapplied = compute_deposit_remaining_many (SUMBER TERPISAH — invarian
                   received − applied − refunded = unapplied diuji, bukan dipaksakan)
  "tertutup SO" (dipakai proforma) = invoice_settled + dp_unapplied.
  DP yang SUDAH diterapkan ke faktur: sisa jurnalnya 0 -> hanya terhitung lewat faktur (tak ganda).
  Kelebihan bayar (OVP) / lepas pembayaran (LPS) membuat deposit TANPA sales_order_id/proforma_id ->
  tak pernah masuk ringkasan SO.
Alokasi ke proforma SO itu:
  eksplisit_P = Σ amount deposit non-void yang menunjuk P (perilaku lama, eksplisit menang)
  kolam       = max(0, tertutup − Σ eksplisit) -> dialirkan berurutan (issued_at, nomor) ke proforma
                'issued' sampai masing-masing penuh (amount − eksplisit_P).
  paid_P = eksplisit_P + bagian_P.  Draf/batal/kedaluwarsa: eksplisit saja (belum/tidak ditagih).
PAGAR BATAL proforma tetap memakai eksplisit saja (proformas.compute_paid_amount).
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


_KOSONG = ("invoiced", "invoice_outstanding", "invoice_settled", "credit_note",
           "dp_received", "dp_applied", "dp_refunded", "dp_unapplied")


async def ringkasan_pesanan(conn, tenant_id: str, so_ids: list) -> dict:
    """{so_id: {invoiced, invoice_outstanding, invoice_settled, credit_note, dp_received,
    dp_applied, dp_refunded, dp_unapplied, tertutup}} — Decimal, journal-derived."""
    from ..routers.customer_deposits import compute_deposit_remaining_many
    from ..services.role_resolver import AccountRole, resolve_account_id_by_role

    so_ids = list({s for s in so_ids if s is not None})
    out = {s: {k: NOL for k in _KOSONG} for s in so_ids}
    if not so_ids:
        return out
    fakturs = await conn.fetch(
        """SELECT si.id, si.sales_order_id,
                  COALESCE((
                      SELECT SUM(jl.debit)
                      FROM journal_entries je
                      JOIN journal_lines jl ON jl.journal_id = je.id
                      JOIN chart_of_accounts coa ON coa.id = jl.account_id
                      WHERE je.tenant_id = $1 AND je.source_id::uuid = si.id
                        AND je.source_type = 'INVOICE'
                        AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
                        AND coa.account_type = 'RECEIVABLE' AND jl.debit > 0
                  ), 0) AS tagih
           FROM sales_invoices si
           WHERE si.tenant_id = $1 AND si.sales_order_id = ANY($2::uuid[])
             AND si.status NOT IN ('void', 'draft')""",
        tenant_id, so_ids,
    )
    if fakturs:
        ids = [f["id"] for f in fakturs]
        sisa = {r["invoice_id"]: _d(r["outstanding"]) for r in await conn.fetch(
            """SELECT invoice_id, outstanding FROM compute_ar_outstanding($1)
               WHERE invoice_id = ANY($2::uuid[])""",
            tenant_id, ids,
        )}
        cn = {r["inv"]: _d(r["kredit"]) for r in await conn.fetch(
            """SELECT cn.original_invoice_id AS inv, COALESCE(SUM(jl.credit), 0) AS kredit
               FROM credit_notes cn
               JOIN journal_entries je ON je.source_id::uuid = cn.id AND je.source_type = 'CREDIT_NOTE'
               JOIN journal_lines jl ON jl.journal_id = je.id
               JOIN chart_of_accounts coa ON coa.id = jl.account_id
               WHERE cn.tenant_id = $1 AND cn.original_invoice_id = ANY($2::uuid[])
                 AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
                 AND coa.account_type = 'RECEIVABLE' AND jl.credit > 0
               GROUP BY cn.original_invoice_id""",
            tenant_id, ids,
        )}
        for f in fakturs:
            o = out[f["sales_order_id"]]
            tagih, sisa_f = _d(f["tagih"]), sisa.get(f["id"], NOL)
            o["invoiced"] += tagih
            o["invoice_outstanding"] += sisa_f
            o["invoice_settled"] += tagih - sisa_f
            o["credit_note"] += cn.get(f["id"], NOL)
    akun_dp = await resolve_account_id_by_role(conn, tenant_id, AccountRole.CUSTOMER_DEPOSIT_LIABILITY)
    deps = await conn.fetch(
        """SELECT cd.id, COALESCE(cd.sales_order_id, p.sales_order_id) AS so_id,
                  COALESCE(SUM(jl.credit - jl.debit) FILTER (WHERE je.source_type = 'CUSTOMER_DEPOSIT'), 0) AS terima,
                  COALESCE(SUM(jl.debit - jl.credit) FILTER (WHERE je.source_type = 'DEPOSIT_APPLICATION'), 0) AS terap,
                  COALESCE(SUM(jl.debit - jl.credit) FILTER (
                      WHERE je.source_type NOT IN ('CUSTOMER_DEPOSIT', 'DEPOSIT_APPLICATION')), 0) AS lain
           FROM customer_deposits cd
           LEFT JOIN proformas p ON p.id = cd.proforma_id AND p.tenant_id = cd.tenant_id
           LEFT JOIN journal_entries je ON je.tenant_id = cd.tenant_id AND je.source_id::uuid = cd.id
                                        AND is_effective_journal(je.id)
           LEFT JOIN journal_lines jl ON jl.journal_id = je.id AND jl.account_id = $3
           WHERE cd.tenant_id = $1 AND cd.status <> 'void'
             AND (cd.sales_order_id = ANY($2::uuid[]) OR p.sales_order_id = ANY($2::uuid[]))
           GROUP BY cd.id, COALESCE(cd.sales_order_id, p.sales_order_id)""",
        tenant_id, so_ids, akun_dp,
    )
    if deps:
        sisa_dep = await compute_deposit_remaining_many(conn, tenant_id, [d["id"] for d in deps])
        for d in deps:
            if d["so_id"] not in out:
                continue
            o = out[d["so_id"]]
            o["dp_received"] += _d(d["terima"])
            o["dp_applied"] += _d(d["terap"])
            o["dp_refunded"] += _d(d["lain"])
            o["dp_unapplied"] += max(NOL, sisa_dep[str(d["id"])])
    for o in out.values():
        o["tertutup"] = o["invoice_settled"] + o["dp_unapplied"]
    return out


async def tertutup_pesanan(conn, tenant_id: str, so_ids: list) -> dict:
    """{so_id: {"faktur", "uang_muka_sisa", "total"}} — bentuk lama, dari ringkasan_pesanan."""
    r = await ringkasan_pesanan(conn, tenant_id, so_ids)
    return {s: {"faktur": o["invoice_settled"], "uang_muka_sisa": o["dp_unapplied"], "total": o["tertutup"]}
            for s, o in r.items()}


def belum_ditagih(order_total, r: dict) -> Decimal:
    """SATU definisi "belum ditagih" SO (payment_summary Q-011 DAN agregat Q-012): total SO − Σ debit
    PIUTANG jurnal INVOICE efektif, min 0. Faktur DRAF belum berjurnal -> tetap "belum ditagih"
    (putusan MASTER 25 Sep)."""
    return max(NOL, _d(order_total) - r["invoiced"])


def ringkasan_pembayaran_so(order_total, r: dict) -> dict:
    """Medan payment_summary GET /api/sales-orders/{id} (Q-011). Murni; float untuk JSON.
    paid_amount      = tertutup SO (SAMA dengan dasar terbayar proforma)
    paid_cash_amount = uang yang benar-benar masuk = invoice_settled − credit_note + dp_unapplied
                       (DP yang diterapkan ikut lewat invoice_settled; DP yang direfund tidak)."""
    total = _d(order_total)
    cash = r["invoice_settled"] - r["credit_note"] + r["dp_unapplied"]
    return {
        "order_total": float(total),
        "invoiced_amount": float(r["invoiced"]),
        "uninvoiced_amount": float(belum_ditagih(total, r)),
        "invoice_settled_amount": float(r["invoice_settled"]),
        "invoice_outstanding_amount": float(r["invoice_outstanding"]),
        "credit_note_amount": float(r["credit_note"]),
        "dp_received_amount": float(r["dp_received"]),
        "dp_applied_amount": float(r["dp_applied"]),
        "dp_refunded_amount": float(r["dp_refunded"]),
        "dp_unapplied_amount": float(r["dp_unapplied"]),
        "paid_amount": float(r["tertutup"]),
        "paid_cash_amount": float(cash),
        "remaining_amount": float(max(NOL, total - r["tertutup"])),
    }


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
