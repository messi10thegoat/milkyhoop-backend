"""Pagar akun ber-lapisan-turunan / ber-modul (Law 31 Gate 4 + Law 29) — SATU fungsi untuk semua jalur yang akunnya
dipilih PENGGUNA atau AI: jurnal manual (routers/journals) dan jalur legacy chat DOCUMENT_INTAKE
(services/kernel_document_executor). 5 Okt 2026 (MASTER): dipindah VERBATIM dari journals.validate_no_derived_layer_accounts
supaya intake memakai pagar yang SAMA, bukan salinan.

- Uang Muka Pelanggan (peran CUSTOMER_DEPOSIT_LIABILITY) -> modul Uang Muka
- RECEIVABLE/PAYABLE -> modul Payment/Settlement
- Persediaan/HPP -> modul Stock Adjustment (persediaan=False untuk intake legacy: jalur itu MENULIS inventory_movements
  sendiri bersama jurnalnya, jadi lapisan ganda tetap sinkron di sana)
- Bank CoA -> boleh
"""
from fastapi import HTTPException


async def pagar_akun_modul(conn, tenant_id: str, account_ids: list, persediaan: bool = True) -> None:
    """Tolak (HTTPException 400) bila salah satu akun milik modul. account_ids = list UUID."""

    # 5 Okt 2026: akun peran Uang Muka Pelanggan (LIABILITY, jadi tak tertangkap cek tipe AR/AP di bawah)
    from .pagar_uang_muka import tolak_jurnal_manual
    await tolak_jurnal_manual(conn, tenant_id, account_ids)

    # Check RECEIVABLE and PAYABLE accounts
    ar_ap_accounts = await conn.fetch(
        """
        SELECT id, account_code, name, account_type
        FROM chart_of_accounts
        WHERE id = ANY($1) AND account_type IN ('RECEIVABLE', 'PAYABLE')
          AND tenant_id = $2
    """,
        account_ids,
        tenant_id,
    )

    if ar_ap_accounts:
        names = ", ".join(f"{a['account_code']} {a['name']}" for a in ar_ap_accounts)
        acct_type = ar_ap_accounts[0]["account_type"]
        raise HTTPException(
            status_code=400,
            detail=(
                f"Manual journal tidak boleh menyentuh akun {acct_type}. "
                f"Akun: {names}. "
                f"Gunakan modul Payment/Settlement untuk transaksi piutang/hutang."
            ),
        )

    if not persediaan:
        return

    # Check inventory/COGS accounts (default + product-level overrides)
    inventory_cogs_rows = await conn.fetch(
        """
        SELECT DISTINCT coa_id FROM (
            SELECT id AS coa_id FROM chart_of_accounts
            WHERE account_code IN ('1-10600', '5-10100') AND tenant_id = $1
            UNION
            SELECT inventory_account_id AS coa_id FROM products
            WHERE tenant_id = $1 AND inventory_account_id IS NOT NULL
            UNION
            SELECT cogs_account_id AS coa_id FROM products
            WHERE tenant_id = $1 AND cogs_account_id IS NOT NULL
        ) sub WHERE coa_id IS NOT NULL
    """,
        tenant_id,
    )

    blocked_ids = {row["coa_id"] for row in inventory_cogs_rows}
    blocked_lines = [aid for aid in account_ids if aid in blocked_ids]

    if blocked_lines:
        blocked_accounts = await conn.fetch(
            """
            SELECT account_code, name FROM chart_of_accounts
            WHERE id = ANY($1) AND tenant_id = $2
        """,
            blocked_lines,
            tenant_id,
        )
        names = ", ".join(f"{a['account_code']} {a['name']}" for a in blocked_accounts)
        raise HTTPException(
            status_code=400,
            detail=(
                f"Manual journal tidak boleh menyentuh akun Persediaan/HPP. "
                f"Akun: {names}. "
                f"Gunakan modul Stock Adjustment untuk transaksi inventory."
            ),
        )
