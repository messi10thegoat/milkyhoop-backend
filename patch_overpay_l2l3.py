"""L2+L3 overpayment: (L2) terjemah metode receive_payments->customer_deposits di satu helper batas;
(L3) set customer_deposits.journal_id = jurnal RP (telusur). account_id sudah CoA sah (FK-terjamin)."""
import io, sys
P = "/root/mh-law2/backend/api_gateway/app/routers/receive_payments.py"
t = io.open(P, encoding="utf-8").read()

# L2 helper sebelum _post_payment
ANCHOR = "async def _post_payment(conn, ctx: dict, payment_id: UUID) -> dict:"
HELPER = (
    "# L2: kosakata metode receive_payments ({cash, bank_transfer}) != customer_deposits\n"
    "# ({cash, transfer, check, other}). Terjemahkan di batas saat overpayment membuat uang muka;\n"
    "# metode tak dikenal -> 400 terbaca, JANGAN lolos ke CHECK (yang jadi 500 + rollback post).\n"
    "_RP_TO_DEPOSIT_METHOD = {\"cash\": \"cash\", \"bank_transfer\": \"transfer\"}\n\n\n"
    "def _deposit_payment_method(rp_method: str) -> str:\n"
    "    m = _RP_TO_DEPOSIT_METHOD.get((rp_method or \"\").strip().lower())\n"
    "    if m is None:\n"
    "        raise HTTPException(\n"
    "            status_code=400,\n"
    "            detail=f\"Metode bayar '{rp_method}' tak didukung untuk uang muka dari kelebihan bayar\",\n"
    "        )\n"
    "    return m\n\n\n"
)
if t.count(ANCHOR) != 1:
    print("GAGAL anchor _post_payment", t.count(ANCHOR)); sys.exit(1)
t = t.replace(ANCHOR, HELPER + ANCHOR)

# L2+L3: rewrite INSERT (tambah journal_id kolom + nilai, renumber; metode diterjemah)
OLD = '''        created_deposit_id = await conn.fetchval(
            """
            INSERT INTO customer_deposits (
                tenant_id, deposit_number, customer_id, customer_name,
                amount, deposit_date, payment_method,
                account_id, reference, notes,
                status, posted_at, posted_by, created_by
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'posted', NOW(), $11, $11)
            RETURNING id
        """,
            ctx["tenant_id"],
            deposit_number,
            payment["customer_id"],
            payment["customer_name"],
            payment["unapplied_amount"],
            payment["payment_date"],
            payment["payment_method"],
            payment["bank_account_id"],
            f"Overpayment from {payment['payment_number']}",
            f"Auto-created from overpayment on {payment['payment_number']}",
            ctx["user_id"],
        )'''
NEW = '''        created_deposit_id = await conn.fetchval(
            """
            INSERT INTO customer_deposits (
                tenant_id, deposit_number, customer_id, customer_name,
                amount, deposit_date, payment_method,
                account_id, journal_id, reference, notes,
                status, posted_at, posted_by, created_by
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'posted', NOW(), $12, $12)
            RETURNING id
        """,
            ctx["tenant_id"],
            deposit_number,
            payment["customer_id"],
            payment["customer_name"],
            payment["unapplied_amount"],
            payment["payment_date"],
            _deposit_payment_method(payment["payment_method"]),  # L2: kosakata deposit
            payment["bank_account_id"],  # account_id: CoA Kas/Bank (FK-terjamin sah via baris Dr RP)
            journal_id,  # L3: telusur ke jurnal RP yang memuat baris Cr Uang Muka
            f"Overpayment from {payment['payment_number']}",
            f"Auto-created from overpayment on {payment['payment_number']}",
            ctx["user_id"],
        )'''
if t.count(OLD) != 1:
    print("GAGAL anchor INSERT overpayment", t.count(OLD)); sys.exit(1)
t = t.replace(OLD, NEW)
io.open(P, "w", encoding="utf-8").write(t)
print("OK L2+L3: helper metode + journal_id di INSERT overpayment")
