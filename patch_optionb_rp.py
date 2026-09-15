"""Option B — receive_payments.py: C1 cabut L3 (journal_id NULL), C5 void RP cek-pakai, C6 cabut pagar."""
import io, sys
P = "/root/mh-law2/backend/api_gateway/app/routers/receive_payments.py"
t = io.open(P, encoding="utf-8").read()


def rep(old, new, tag):
    global t
    if t.count(old) != 1:
        print(f"GAGAL {tag}: cacah {t.count(old)}"); sys.exit(1)
    t = t.replace(old, new)


# C1: cabut L3 (hapus journal_id kolom+nilai dari INSERT overpayment; renumber $12->$11)
rep(
'''                account_id, journal_id, reference, notes,
                status, posted_at, posted_by, created_by
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'posted', NOW(), $12, $12)''',
'''                account_id, reference, notes,
                status, posted_at, posted_by, created_by
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'posted', NOW(), $11, $11)''', "C1 kolom")
rep(
'''            payment["bank_account_id"],  # account_id: CoA Kas/Bank (FK-terjamin sah via baris Dr RP)
            journal_id,  # L3: telusur ke jurnal RP yang memuat baris Cr Uang Muka
            f"Overpayment from {payment['payment_number']}",''',
'''            payment["bank_account_id"],  # account_id: CoA Kas/Bank (FK-terjamin sah via baris Dr RP; atribusi & telusur via receive_payments.created_deposit_id)
            f"Overpayment from {payment['payment_number']}",''', "C1 nilai")

# C6: cabut pagar sementara
rep(
'''    # PAGAR SEMENTARA (2026-09-14): kelebihan bayar (unapplied>0) membuat customer_deposit yang
    # liabilitasnya dibukukan di jurnal RP (source_id=RP) -> TAK ter-atribusi ke deposit -> saldo
    # journal-derived 0 -> deposit TAK bisa di-apply. Tolak SEBELUM menulis apa pun sampai atribusi
    # (option B) live; nol deposit-overpay baru lahir. Dicabut setelah fix atribusi.
    if payment["unapplied_amount"] and payment["unapplied_amount"] > 0:
        raise HTTPException(
            status_code=400,
            detail="Kelebihan bayar belum dapat diproses; catat pembayaran sebesar sisa tagihan",
        )

    # Get account IDs''',
'''    # Get account IDs''', "C6 pagar")

# C5: void RP cek-pakai deposit-overpay
rep(
'''                if payment["status"] == "draft":
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot void draft payment. Delete it instead.",
                    )

                # Create reversal journal''',
'''                if payment["status"] == "draft":
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot void draft payment. Delete it instead.",
                    )

                # Option B: uang muka overpay yang SUDAH dipakai/dikembalikan tak boleh divoid lewat
                # void RP (pemakaian/refund uang muka = jurnal terpisah). Tolak; batalkan pemakaian dulu.
                if payment["created_deposit_id"]:
                    _dep_used = await conn.fetchrow(
                        "SELECT deposit_number, amount_applied, amount_refunded "
                        "FROM customer_deposits WHERE id = $1",
                        payment["created_deposit_id"],
                    )
                    if _dep_used and (
                        (_dep_used["amount_applied"] or 0) > 0
                        or (_dep_used["amount_refunded"] or 0) > 0
                    ):
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"Uang muka {_dep_used['deposit_number']} dari kelebihan bayar ini "
                                "sudah dipakai/dikembalikan. Batalkan pemakaian/pengembalian uang muka "
                                "itu dulu sebelum membatalkan pembayaran."
                            ),
                        )

                # Create reversal journal''', "C5 void RP cek-pakai")

io.open(P, "w", encoding="utf-8").write(t)
print("OK receive_payments: C1 cabut L3 + C5 void RP cek-pakai + C6 cabut pagar")
