"""PAGAR SEMENTARA overpayment: sampai atribusi jurnal deposit-overpay beres (option B), tolak
unapplied>0 di _post_payment SEBELUM menulis apa pun (nol deposit-overpay baru lahir)."""
import io, sys
P = "/root/mh-law2/backend/api_gateway/app/routers/receive_payments.py"
t = io.open(P, encoding="utf-8").read()
OLD = '''    if payment["status"] != "draft":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot post payment with status '{payment['status']}'",
        )

    # Get account IDs'''
NEW = '''    if payment["status"] != "draft":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot post payment with status '{payment['status']}'",
        )

    # PAGAR SEMENTARA (2026-09-14): kelebihan bayar (unapplied>0) membuat customer_deposit yang
    # liabilitasnya dibukukan di jurnal RP (source_id=RP) -> TAK ter-atribusi ke deposit -> saldo
    # journal-derived 0 -> deposit TAK bisa di-apply. Tolak SEBELUM menulis apa pun sampai atribusi
    # (option B) live; nol deposit-overpay baru lahir. Dicabut setelah fix atribusi.
    if payment["unapplied_amount"] and payment["unapplied_amount"] > 0:
        raise HTTPException(
            status_code=400,
            detail="Kelebihan bayar belum dapat diproses; catat pembayaran sebesar sisa tagihan",
        )

    # Get account IDs'''
if t.count(OLD) != 1:
    print("GAGAL anchor", t.count(OLD)); sys.exit(1)
io.open(P, "w", encoding="utf-8").write(t.replace(OLD, NEW))
print("OK pagar sementara overpayment di _post_payment")
