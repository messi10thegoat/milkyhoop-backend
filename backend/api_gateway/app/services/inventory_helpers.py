"""
Inventory Helpers
=================
Shared inventory operations for all sales flows (invoices, receipts, POS).
This is the CANONICAL implementation — all flows should call these helpers
instead of implementing their own COGS/inventory logic.

Architecture:
- record_inventory_outbound(): Sale flows (invoice, receipt, POS)
- record_inventory_inbound(): Purchase flows (bill)
- resolve_inventory_accounts(): Product-level → tenant-default account resolution

References:
- milkyhoop-inventory skill: Inventory & COGS Integrity Framework
- milkyhoop-ironlaws skill: Accounting Iron Laws
"""
import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional
from uuid import UUID, uuid4

from . import fifo_lapisan
from .ledger_kartu import tulis_kartu
from ..utils.tanggal_tenant import tanggal_dokumen
from .role_resolver import (
    AccountRole,
    resolve_account_id_by_role,
)

logger = logging.getLogger(__name__)


async def resolve_inventory_accounts(conn, tenant_id: str, product_id: UUID) -> dict:
    """
    Resolve COGS and Inventory GL accounts.
    Priority: product-level (products.cogs_account_id / inventory_account_id)
              → tenant role mapping (AccountRole.COGS_SALES / INVENTORY_MERCHANDISE).

    Fase F6/F7 migration: removed hardcoded literals '5-10100' / '1-10600'.
    Tenant fallback now goes through account_roles per Iron Laws 1, 18, 27.

    Returns {"cogs_account_id": uuid, "inventory_account_id": uuid}.

    Raises AccountRoleUnmappedError if role lookup needed but tenant has no
    mapping for COGS_SALES or INVENTORY_MERCHANDISE (Iron Law: no silent
    fallback to literal account codes).
    """
    product_accounts = await conn.fetchrow(
        "SELECT cogs_account_id, inventory_account_id FROM products WHERE id = $1",
        product_id,
    )

    cogs_acct = product_accounts["cogs_account_id"] if product_accounts else None
    inv_acct = product_accounts["inventory_account_id"] if product_accounts else None

    if not cogs_acct:
        logger.warning(
            "Product %s (tenant %s) has NULL cogs_account_id; "
            "substituting tenant COGS_SALES role default",
            product_id,
            tenant_id,
        )
        cogs_acct = await resolve_account_id_by_role(
            conn, tenant_id, AccountRole.COGS_SALES
        )
    if not inv_acct:
        logger.warning(
            "Product %s (tenant %s) has NULL inventory_account_id; "
            "substituting tenant INVENTORY_MERCHANDISE role default",
            product_id,
            tenant_id,
        )
        inv_acct = await resolve_account_id_by_role(
            conn, tenant_id, AccountRole.INVENTORY_MERCHANDISE
        )

    return {"cogs_account_id": cogs_acct, "inventory_account_id": inv_acct}


async def get_cost_for_sale(conn, tenant_id: str, product_id: UUID, qty=None) -> dict:
    """
    Get unit cost for a sale using WAC with fallback to purchase_price.
    Returns {"unit_cost": Decimal, "cost_source": str}.
    qty (FIFO unit 2, pratinjau): barang FIFO aktif -> rencana lapisan yang SAMA dengan penulis (tanpa kunci); kurang -> 409 seperti posting.
    """
    if qty and await fifo_lapisan.fifo_aktif(conn, tenant_id, product_id):
        try:
            ambil = await fifo_lapisan.rencana_keluar(conn, tenant_id, product_id, Decimal(str(qty)))
        except fifo_lapisan.StokFifoKurang as e:
            from .galat_berkode import GalatBerkode
            raise GalatBerkode(409, "STOK_FIFO_KURANG", f"Stok tidak cukup: diminta {e.diminta:f}, tersedia {e.tersedia:f}.")
        total = sum((a.nilai for a in ambil), Decimal("0"))
        return {"unit_cost": (total / Decimal(str(qty))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "cost_source": "FIFO", "total_cost": total}
    avg_cost = await conn.fetchval(
        "SELECT get_weighted_average_cost($1, $2)", tenant_id, product_id
    )
    cost_source = "WEIGHTED_AVG"

    if not avg_cost or avg_cost == 0:
        avg_cost = await conn.fetchval(
            "SELECT COALESCE(purchase_price_amount, purchase_price, 0) FROM products WHERE id = $1",
            product_id,
        )
        cost_source = "PURCHASE_PRICE"

    return {
        "unit_cost": Decimal(str(avg_cost)) if avg_cost else Decimal("0"),
        "cost_source": cost_source,
    }


async def _fifo_ambil(conn, tenant_id: str, product_id, quantity):
    """None = bukan barang FIFO aktif. Selain itu daftar Ambil (sudah terkunci). T4: kurang -> 409 STOK_FIFO_KURANG."""
    if not await fifo_lapisan.fifo_aktif(conn, tenant_id, product_id):
        return None
    try:
        return await fifo_lapisan.ambil_keluar(conn, tenant_id, product_id, quantity)
    except fifo_lapisan.StokFifoKurang as e:
        from .galat_berkode import GalatBerkode
        raise GalatBerkode(409, "STOK_FIFO_KURANG", f"Stok tidak cukup: diminta {e.diminta:f}, tersedia {e.tersedia:f}.")


async def assert_gudang_tidak_terkunci(conn, tenant_id: str, warehouse_id, source_type: Optional[str] = None, source_id=None) -> None:
    """STOK OPNAME (V509, putusan pemilik 24 Nov 2026): gudang yang sedang DIHITUNG (stock_opnames.status='hitung') menolak SEMUA gerakan stok
    baru (juga pembalik/void), kecuali gerakan Penyesuaian Stok milik opname itu sendiri. Lapis 1 = pesan 409 rapi sebelum menulis;
    lapis 2 = trigger DB trg_gudang_terkunci (jaring untuk jalur inline lama yang tak lewat helper). Predikat SAMA dengan trigger."""
    if warehouse_id is None:
        return
    nomor = await conn.fetchval(
        "SELECT o.opname_number FROM stock_opnames o WHERE o.tenant_id = $1 AND o.warehouse_id = $2 AND o.status = 'hitung' "
        "AND NOT ($3::text = 'STOCK_ADJUSTMENT' AND o.adjustment_id IS NOT NULL AND o.adjustment_id::text = $4::text) LIMIT 1",
        tenant_id, warehouse_id, source_type, str(source_id) if source_id is not None else None)
    if nomor:
        from .galat_berkode import GalatBerkode
        raise GalatBerkode(409, "GUDANG_TERKUNCI", f"Gudang sedang dihitung ({nomor}); selesaikan atau batalkan opname dulu.")


async def record_inventory_outbound(
    conn,
    tenant_id: str,
    product_id: UUID,
    product_code: Optional[str],
    product_name: str,
    warehouse_id: UUID,
    quantity: float,
    source_type: str,
    source_id: UUID,
    source_number: str,
    user_id: Optional[UUID],
    notes: str,
    receipt_date=None,
    jurnal_pemanggil: Optional[UUID] = None,
) -> dict:
    """
    Standard outbound flow: get WAC, insert inventory_ledger, create COGS journal.
    jurnal_pemanggil (BUG-053 nota debit perpetual): nilai persediaan sudah dibukukan di jurnal PEMANGGIL -> tanpa jurnal HPP, kartu stok
    bertaut jurnal itu.
    DB trigger auto-updates warehouse_stock.

    MUST be called within an existing transaction (conn.transaction()).

    Returns {
        "ledger_id": uuid,
        "journal_id": uuid | None,
        "unit_cost": Decimal,
        "total_cost": Decimal (2dp HALF_UP; dulu int() memotong sen -> jurnal HPP != kartu stok, Law 25),
        "cost_source": str,
    }
    """
    # PAGAR GUDANG (11 Sep 2026). `warehouse_id` sudah BERTIPE `UUID` di tanda
    # tangan ini -- bukan `Optional` -- tapi Python tak menegakkannya, dan
    # selama berbulan-bulan pemanggil mengirim None tanpa satu pun keberatan.
    # Akibatnya: trigger `trg_update_warehouse_stock` mencocokkan
    # `warehouse_id = NEW.warehouse_id`, NULL tak pernah cocok, baris masuk ke
    # `inventory_ledger` tapi TIDAK PERNAH ke cache gudang. Layar menjumlahkan
    # ledger dan menjanjikan stok; `/fulfill` membaca cache dan menolak 409.
    # Terukur: 11 baris, 130 unit, 3 item, dan ia akan terulang -- 26 dari 34
    # work order dan 73 dari 74 produk tak punya gudang untuk dijadikan
    # cadangan.
    #
    # Ini SATU tempat yang menutup jalur tulis BERIKUTNYA. Pemanggil yang tak
    # tahu harus mengisi gudang kini MENDENGARNYA, bukan menulis kekosongan
    # dengan tenang.
    await assert_gudang_tidak_terkunci(conn, tenant_id, warehouse_id, source_type, source_id)  # Stok Opname: gudang dihitung = tolak

    if warehouse_id is None:
        raise ValueError(
            f"warehouse_id wajib untuk gerakan persediaan "
            f"({source_type} {source_number}, produk {product_id}). "
            "Menulis NULL akan membuat barang masuk buku tapi tak pernah "
            "masuk stok gudang -- terlihat ada di layar, ditolak saat dikirim."
        )

    # t10-tanggal-bisnis: cadangan = hari ini zona tenant, bukan date.today().
    movement_date = receipt_date or await tanggal_dokumen(conn, tenant_id)

    # FIFO unit 2 (flag fifo_v1 + barang fifo + costing_mulai): HPP = Σ nilai lapisan yang dimakan (T4: kurang -> 409). Selain itu jalur WAC lama.
    ambil = await _fifo_ambil(conn, tenant_id, product_id, quantity)
    if ambil is not None:
        total_cost = sum((a.nilai for a in ambil), Decimal("0"))
        unit_cost = (total_cost / Decimal(str(quantity))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)  # kolom 2dp; total_cost = penentu (Σ lapisan)
        cost_source = "FIFO"
    else:
        # 1. Get weighted average cost
        cost_info = await get_cost_for_sale(conn, tenant_id, product_id)
        unit_cost = cost_info["unit_cost"]
        cost_source = cost_info["cost_source"]
        # Law 25 (6 Nov 2026): dulu int(float x float) MEMOTONG sen -> jurnal HPP < nilai kartu stok (qty x unit_cost) -> HC15 bergeser
        total_cost = (Decimal(str(quantity)) * Decimal(str(unit_cost))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if total_cost == 0:
        logger.warning(
            f"Zero COGS for product {product_id} (source: {cost_source}). "
            f"Skipping COGS journal but still recording ledger movement."
        )

    # 2. Get current balance
    current_balance = await conn.fetchval(
        "SELECT COALESCE(SUM(quantity_in - quantity_out), 0) FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2",
        tenant_id,
        product_id,
    )
    new_balance = float(current_balance) - quantity
    if new_balance < 0:
        import logging

        logging.getLogger("inventory").warning(
            f"NEGATIVE INVENTORY: product_id={product_id} balance={new_balance} warehouse={warehouse_id}"
        )

    # Get average_cost snapshot (FIFO: nilai sisa lapisan ÷ qty sisa SESUDAH baris ini, aturan 3.3)
    avg_cost_snapshot = (
        await fifo_lapisan.rata_sisa_sesudah(conn, tenant_id, product_id, ambil) if ambil is not None
        else await conn.fetchval("SELECT get_weighted_average_cost($1, $2)", tenant_id, product_id)
    ) or unit_cost

    # 3. Insert inventory_ledger (trigger handles warehouse_stock)
    ledger_id = uuid4()
    await tulis_kartu(
        conn,
        id=ledger_id, tenant_id=tenant_id, product_id=product_id, product_code=product_code, product_name=product_name, movement_type='SALE',
        movement_date=movement_date, source_type=source_type, source_id=source_id, source_number=source_number, quantity_in=0,
        quantity_out=quantity, quantity_balance=new_balance, unit_cost=unit_cost, total_cost=total_cost, average_cost=avg_cost_snapshot,
        warehouse_id=warehouse_id, created_by=user_id, notes=notes)
    if ambil is not None:
        await fifo_lapisan.catat_pakai(conn, tenant_id, ledger_id, ambil)

    # 4. Create COGS journal (if cost > 0)
    journal_id = jurnal_pemanggil
    if total_cost > 0 and jurnal_pemanggil is None:
        accounts = await resolve_inventory_accounts(conn, tenant_id, product_id)
        hpp_acct = accounts["cogs_account_id"]
        inv_acct = accounts["inventory_account_id"]

        if hpp_acct and inv_acct:
            journal_id = uuid4()

            # Determine journal source_type based on flow
            journal_source_type = {
                "SALES_INVOICE": "SALES_INVOICE_COGS",
                "POS_SALE": "SALES_RECEIPT_COGS",
            }.get(source_type, f"{source_type}_COGS")

            # ND-BUAT (29 Okt 2026): SATU jurnal HPP per PANGGILAN (= per baris dokumen). Dulu nomornya selalu "COGS-{nomor}" -> dokumen
            # dengan >= 2 baris barang dilacak bertabrakan di uq_je_tenant_number -> posting 500 (nota debit 2 baris; terukur harness ND-BUAT,
            # 0 jurnal HPP ND pernah terposting di produksi). Nomor ke-2 dst diberi akhiran -2, -3 ... dihitung dari jurnal HPP sumber YANG SAMA
            # di transaksi pemanggil (penulis memegang kunci dokumennya) -> nomor pertama TETAP "COGS-{nomor}" seperti dulu.
            ke = await conn.fetchval(
                "SELECT count(*) FROM journal_entries WHERE tenant_id = $1 AND source_type = $2 AND source_id = $3",
                tenant_id, journal_source_type, source_id,
            ) or 0
            nomor_jurnal = f"COGS-{source_number}" if not ke else f"COGS-{source_number}-{ke + 1}"

            await conn.execute(
                """
                INSERT INTO journal_entries (
                    id, tenant_id, journal_number, journal_date,
                    description, source_type, source_id,
                    total_debit, total_credit, status, created_by
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8, 'DRAFT', $9)
                """,
                journal_id,
                tenant_id,
                nomor_jurnal,
                movement_date,
                f"HPP {source_number}",
                journal_source_type,
                source_id,
                total_cost,
                user_id,
            )

            await conn.execute(
                """
                INSERT INTO journal_lines (journal_id, account_id, memo, debit, credit, line_number)
                VALUES ($1, $2, 'HPP Barang Dagang', $3, 0, 1),
                       ($1, $4, 'Persediaan Barang Dagang', 0, $3, 2)
                """,
                journal_id,
                hpp_acct,
                total_cost,
                inv_acct,
            )

            # 5. Link journal to ledger

            # Post the journal (triggers hash chain: Law 20)
            await conn.execute(
                "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", journal_id
            )
    if journal_id:
        await conn.execute(
            "UPDATE inventory_ledger SET journal_id = $1 WHERE id = $2",
            journal_id,
            ledger_id,
        )

    return {
        "ledger_id": ledger_id,
        "journal_id": journal_id,
        "unit_cost": unit_cost,
        "total_cost": total_cost,
        "cost_source": cost_source,
    }


async def record_inventory_inbound(
    conn,
    tenant_id: str,
    product_id: UUID,
    product_code: Optional[str],
    product_name: str,
    warehouse_id: UUID,
    quantity: float,
    unit_cost: float,
    source_type: str,
    source_id: UUID,
    source_number: str,
    user_id: Optional[UUID],
    notes: str,
    movement_date=None,
    journal_id: Optional[UUID] = None,
    batch_id: Optional[UUID] = None,
    movement_type: str = "PURCHASE",
    storage_location_id: Optional[UUID] = None,
    transaction_unit: Optional[str] = None,
    transaction_quantity=None,
    conversion_factor=None,
) -> dict:
    """
    Standard inbound flow: calculate new WAC, insert inventory_ledger.
    DB trigger auto-updates warehouse_stock.

    CANONICAL WAC source: this is the ONLY place inbound WAC is computed
    (milkyhoop-inventory Rule 3, L09 convergence). bills_service create_bill /
    create_bill_v2 / post_bill all delegate here.

    Optional columns (passed by call sites):
    - journal_id: link the ledger row to its GL journal (dual-ledger sync).
    - batch_id: batch tracking linkage (resolved by caller before delegating).
    - movement_type: ledger movement_type label (default 'PURCHASE'; stock
      adjustments pass 'ADJUSTMENT', production FG receipt 'PRODUCTION_OUTPUT').
    - storage_location_id: sub-location linkage (stock adjustments).
    - transaction_unit / transaction_quantity / conversion_factor: unit-of-
      measure conversion metadata (post_bill base-unit conversion).

    `quantity` MUST be the base-unit quantity and `unit_cost` the per-base-unit
    cost; callers that do UoM conversion convert BEFORE calling.

    Returns {"ledger_id": uuid, "new_average_cost": Decimal}.
    """
    # PAGAR GUDANG (11 Sep 2026). `warehouse_id` sudah BERTIPE `UUID` di tanda
    # tangan ini -- bukan `Optional` -- tapi Python tak menegakkannya, dan
    # selama berbulan-bulan pemanggil mengirim None tanpa satu pun keberatan.
    # Akibatnya: trigger `trg_update_warehouse_stock` mencocokkan
    # `warehouse_id = NEW.warehouse_id`, NULL tak pernah cocok, baris masuk ke
    # `inventory_ledger` tapi TIDAK PERNAH ke cache gudang. Layar menjumlahkan
    # ledger dan menjanjikan stok; `/fulfill` membaca cache dan menolak 409.
    # Terukur: 11 baris, 130 unit, 3 item, dan ia akan terulang -- 26 dari 34
    # work order dan 73 dari 74 produk tak punya gudang untuk dijadikan
    # cadangan.
    #
    # Ini SATU tempat yang menutup jalur tulis BERIKUTNYA. Pemanggil yang tak
    # tahu harus mengisi gudang kini MENDENGARNYA, bukan menulis kekosongan
    # dengan tenang.
    await assert_gudang_tidak_terkunci(conn, tenant_id, warehouse_id, source_type, source_id)  # Stok Opname: gudang dihitung = tolak

    if warehouse_id is None:
        raise ValueError(
            f"warehouse_id wajib untuk gerakan persediaan "
            f"({source_type} {source_number}, produk {product_id}). "
            "Menulis NULL akan membuat barang masuk buku tapi tak pernah "
            "masuk stok gudang -- terlihat ada di layar, ditolak saat dikirim."
        )

    # t10-tanggal-bisnis: cadangan = hari ini zona tenant, bukan date.today().
    if movement_date is None:
        movement_date = await tanggal_dokumen(conn, tenant_id)

    quantity_dec = Decimal(str(quantity))
    unit_cost_dec = Decimal(str(unit_cost))
    total_cost = quantity_dec * unit_cost_dec

    # Calculate new WAC
    balance_row = await conn.fetchrow(
        """
        SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) as balance
        FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2
        """,
        tenant_id,
        product_id,
    )
    current_balance = Decimal(str(balance_row["balance"]))
    new_balance = current_balance + quantity_dec

    # WAC numerator and denominator MUST both be NET on-hand (deplete on
    # outbound). Bug fixed (golden-apparel Kain inflation): numerator used the
    # gross SUM(quantity_in*unit_cost) which never depletes, while the
    # denominator was net SUM(quantity_in)-SUM(quantity_out). With partial
    # consumption before a restock, the un-depleted numerator inflated WAC.
    # Both legs must subtract the outbound value/qty so WAC reflects only stock
    # still on hand. milkyhoop-inventory Rule 3; Iron Law 25 (Decimal only).
    avg_cost_row = await conn.fetchrow(
        """
        SELECT
            COALESCE(SUM(quantity_in * unit_cost)
                     - SUM(quantity_out * unit_cost), 0) as net_value,
            COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) as total_qty
        FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2
        """,
        tenant_id,
        product_id,
    )

    old_qty = Decimal(str(avg_cost_row["total_qty"])) if avg_cost_row else Decimal("0")
    old_value = Decimal(str(avg_cost_row["net_value"])) if avg_cost_row else Decimal("0")
    # Division-by-zero guard (milkyhoop-inventory Rule 3 inv #5): when the
    # post-inbound on-hand quantity is zero, fall back to the new unit cost.
    if (old_qty + quantity_dec) == 0:
        new_avg_cost = unit_cost_dec
    elif old_qty > 0:
        new_avg_cost = (old_value + total_cost) / (old_qty + quantity_dec)
    else:
        # No (or negative) prior on-hand: prior value is unreliable as a WAC
        # base, anchor to the incoming unit cost (matches legacy zero-stock).
        new_avg_cost = unit_cost_dec

    # Insert inventory_ledger
    ledger_id = uuid4()
    txn_qty_dec = (
        Decimal(str(transaction_quantity)) if transaction_quantity is not None else None
    )
    conv_factor_dec = (
        Decimal(str(conversion_factor)) if conversion_factor is not None else None
    )
    await tulis_kartu(
        conn,
        id=ledger_id, tenant_id=tenant_id, product_id=product_id, product_code=product_code, product_name=product_name,
        movement_type=movement_type, movement_date=movement_date, source_type=source_type, source_id=source_id, source_number=source_number,
        quantity_in=quantity_dec, quantity_out=0, quantity_balance=new_balance, unit_cost=unit_cost_dec, total_cost=total_cost,
        average_cost=new_avg_cost, warehouse_id=warehouse_id, journal_id=journal_id, created_by=user_id, notes=notes, batch_id=batch_id,
        transaction_unit=transaction_unit, transaction_quantity=txn_qty_dec, conversion_factor=conv_factor_dec,
        storage_location_id=storage_location_id)
    if await fifo_lapisan.fifo_aktif(conn, tenant_id, product_id):   # FIFO unit 2: satu baris masuk = satu lapisan
        await fifo_lapisan.catat_masuk(conn, tenant_id, product_id, ledger_id, movement_date, quantity_dec,
                                       fifo_lapisan.nilai_masuk(quantity_dec, unit_cost_dec), batch_id)

    return {
        "ledger_id": ledger_id,
        "new_average_cost": new_avg_cost,
    }


# Sumber yang barang-masuknya memang PEMBELIAN: pembaliknya = retur pembelian.
_SUMBER_PEMBELIAN = frozenset({"BILL", "PURCHASE_INVOICE"})
# inventory_ledger.movement_type = character varying(30), tanpa CHECK.
_PANJANG_MAKS_MOVEMENT_TYPE = 30


def label_pembalikan_masuk(source_type: str) -> str:
    """movement_type untuk baris yang MEMBALIK gerakan masuk (qty_in) sebuah sumber.

    BILL/PURCHASE_INVOICE -> 'PURCHASE_RETURN' (tetap, perilaku lama).
    CREDIT_NOTE -> 'SALES_RETURN_REVERSAL' (pasangan restock 'SALES_RETURN', #37).
    Sumber lain -> '{source_type}_REVERSAL', sepola dengan
    MATERIAL_ISSUE_REVERSAL / PRODUCTION_OUTPUT_REVERSAL (production.py).
    Nama kepanjangan (>30) -> 'INBOUND_REVERSAL' agar tak ditolak kolom.
    """
    st = (source_type or "").upper()
    if st in _SUMBER_PEMBELIAN:
        return "PURCHASE_RETURN"
    if st == "CREDIT_NOTE":
        # #37: restock nota kredit berlabel SALES_RETURN -> pembaliknya sepasang.
        return "SALES_RETURN_REVERSAL"
    label = f"{st}_REVERSAL" if st else "INBOUND_REVERSAL"
    if len(label) > _PANJANG_MAKS_MOVEMENT_TYPE:
        return "INBOUND_REVERSAL"
    return label


def hitung_wac_sesudah(nilai_sebelum: Decimal, qty_sebelum: Decimal, masuk: Decimal, keluar: Decimal, unit_cost: Decimal, cadangan: Decimal) -> Decimal:
    """MURNI (BUG-268b). WAC SESUDAH sebuah gerakan = nilai bersih ÷ qty bersih on-hand (rumus SAMA dengan record_inventory_inbound, get_weighted_average_cost V480 dan
    guard V181), 2 desimal HALF_UP (Law 9/25, Decimal). nilai_sesudah = nilai_sebelum + masuk×unit_cost − keluar×unit_cost; qty_sesudah = qty_sebelum + masuk − keluar.
    qty_sesudah <= 0 atau nilai_sesudah < 0 -> `cadangan` (snapshot sebelumnya; milkyhoop-inventory Rule 7: tak pernah 0 / harga jual)."""
    nilai = nilai_sebelum + masuk * unit_cost - keluar * unit_cost
    qty = qty_sebelum + masuk - keluar
    if qty > 0 and nilai >= 0:
        return (nilai / qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return cadangan


async def saldo_dan_nilai(conn, tenant_id: str, product_id) -> tuple:
    """(qty bersih, nilai bersih) on-hand barang dari inventory_ledger: Σ in − Σ out dan Σ in×uc − Σ out×uc (rumus SAMA dgn inbound, V480, guard V181). Hanya baca."""
    r = await conn.fetchrow(
        """
        SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) AS balance,
               COALESCE(SUM(quantity_in * unit_cost) - SUM(quantity_out * unit_cost), 0) AS nilai
        FROM inventory_ledger
        WHERE tenant_id = $1 AND product_id = $2
        """,
        tenant_id,
        product_id,
    )
    return (Decimal(str(r["balance"])) if r else Decimal("0"),
            Decimal(str(r["nilai"])) if r and r["nilai"] is not None else Decimal("0"))


async def snapshot_wac_sesudah(conn, tenant_id: str, product_id, masuk, keluar, unit_cost, saldo=None) -> Decimal:
    """BUG-268b/c: nilai kolom average_cost untuk baris yang AKAN ditulis (masuk/keluar pada unit_cost) = WAC SESUDAH efek baris itu. `saldo` = (qty, nilai) bila pemanggil
    sudah membacanya. Cadangan (qty<=0 / nilai<0) = get_weighted_average_cost sebelumnya, lalu unit_cost (tak pernah 0). SATU penentu untuk semua penulis baris pembalik."""
    qty, nilai = saldo if saldo is not None else await saldo_dan_nilai(conn, tenant_id, product_id)
    lama = await conn.fetchval("SELECT get_weighted_average_cost($1, $2)", tenant_id, product_id)
    uc = Decimal(str(unit_cost or 0))
    return hitung_wac_sesudah(nilai, qty, Decimal(str(masuk or 0)), Decimal(str(keluar or 0)), uc, Decimal(str(lama)) if lama else uc)


async def record_inventory_reversal(
    conn,
    tenant_id: str,
    source_type: str,
    source_id: UUID,
    reversal_journal_id: UUID,
    created_by: Optional[UUID] = None,
    reversal_date=None,
    notes_prefix: str = "VOID",
    journal_id: Optional[UUID] = None,
    pindah_batch: bool = False,
) -> list:
    """
    Reverse all inventory_ledger entries for a given source.

    pindah_batch (BE-A 17 Nov; BUG-286 18 Nov): bawaan FALSE = SATU penggerak saldo batch: trigger DB trg_update_batch_stock (V047/V204) menggeser
    batch_warehouse_stock pada INSERT ledger ber-batch_id (baris pembalik di bawah ikut). True (hanya bila sengaja) = helper ikut menggeser manual -> GANDA
    (terukur 18 Nov: void bill 20 -> 0, void faktur jual +6 untuk 3). Penandaan 'depleted' tetap dilakukan helper bagaimanapun.

    journal_id (VOID-SJ-FAKTUR 15 Okt 2026): bila diisi, HANYA baris sumber itu dengan journal_id tsb. Satu faktur bisa punya
    beberapa Surat Jalan yang semuanya ber-source_id = id faktur; pembalik per SJ dibedakan oleh jurnal HPP-nya. None = perilaku lama.

    Finds original entries by source_type + source_id, creates mirror entries
    (swap quantity_in <-> quantity_out), links journal_id to reversal journal.

    Architecture (milkyhoop-inventory Rule 9):
    - Original inbound (qty_in) -> reversal label per SUMBER, lihat
      `label_pembalikan_masuk()`: BILL -> PURCHASE_RETURN; lainnya
      (STOCK_ADJUSTMENT, ...) -> "{source_type}_REVERSAL"; CREDIT_NOTE -> SALES_RETURN_REVERSAL.
    - Original outbound (qty_out) -> reversal VOID_REVERSAL (qty_in)
    - source_type = "{original}_VOID" (e.g. BILL_VOID, SALES_INVOICE_VOID)
    - journal_id = reversal_journal_id (NOT original — D5 fix)
    - WAC: snapshot only, no recalc on outbound (Rule 3)

    MUST be called within an existing transaction (conn.transaction()).

    Returns list of {"product_id", "ledger_id", "quantity_reversed", "direction"}.
    """
    # t10-tanggal-bisnis: cadangan = hari ini zona tenant, bukan date.today().
    # (credit_notes void memanggil tanpa reversal_date -> jalur ini.)
    movement_date = reversal_date or await tanggal_dokumen(conn, tenant_id)

    # Find original inventory_ledger entries for this source
    original_entries = await conn.fetch(
        """
        SELECT id, product_id, product_code, product_name,
               quantity_in, quantity_out, unit_cost, total_cost,
               warehouse_id, source_number, batch_id
        FROM inventory_ledger
        WHERE tenant_id = $1 AND source_type = $2 AND source_id = $3
          AND ($4::uuid IS NULL OR journal_id = $4::uuid)
        ORDER BY created_at
        """,
        tenant_id,
        source_type,
        source_id,
        journal_id,
    )

    if not original_entries:
        logger.info(
            f"No inventory_ledger entries found for {source_type}:{source_id} — "
            "skipping inventory reversal"
        )
        return []

    void_source_type = f"{source_type}_VOID"
    results = []

    for entry in original_entries:
        await assert_gudang_tidak_terkunci(conn, tenant_id, entry["warehouse_id"], void_source_type, source_id)  # Stok Opname: void ke gudang dihitung = tolak
        product_id = entry["product_id"]
        orig_qty_in = Decimal(str(entry["quantity_in"] or 0))
        orig_qty_out = Decimal(str(entry["quantity_out"] or 0))
        unit_cost = Decimal(str(entry["unit_cost"] or 0))

        # Mirror: swap quantity_in and quantity_out
        rev_qty_in = orig_qty_out  # if original was outbound, reversal restores stock
        rev_qty_out = orig_qty_in  # if original was inbound, reversal removes stock

        quantity_reversed = orig_qty_in if orig_qty_in > 0 else orig_qty_out

        # Determine movement_type
        if orig_qty_in > 0:
            # Label per sumber: dulu SELALU 'PURCHASE_RETURN', juga untuk void
            # penyesuaian stok / nota kredit -- kartu stok menyebut "retur
            # pembelian" untuk dokumen yang bukan pembelian.
            movement_type = label_pembalikan_masuk(source_type)
        else:
            movement_type = "VOID_REVERSAL"  # Reversing outbound (restoring stock)

        # Saldo qty + nilai bersih SEBELUM baris pembalik (rumus nilai = Σ in×uc − Σ out×uc, sama dgn inbound, V480, guard V181)
        current_balance, nilai_sebelum = await saldo_dan_nilai(conn, tenant_id, product_id)
        new_balance = current_balance + rev_qty_in - rev_qty_out

        # BUG-268b (17 Nov 2026): snapshot average_cost = WAC SESUDAH efek baris pembalik (dulu get_weighted_average_cost SEBELUM insert -> void
        # penerimaan meninggalkan WAC yang masih memuat penerimaan itu; kolom ini dibaca guard V181, kartu stok, laporan nilai persediaan).
        avg_cost = await snapshot_wac_sesudah(conn, tenant_id, product_id, rev_qty_in, rev_qty_out, unit_cost, (current_balance, nilai_sebelum))

        total_cost = quantity_reversed * unit_cost
        # FIFO unit 2: nilai pembalik = nilai lapisan asal (bukan qty x unit_cost 2dp) -> lapisan dan kartu pulih persis; T5 = 409 bila lapisan terpakai
        rencana_fifo = (await fifo_lapisan.rencana_balik(conn, tenant_id, product_id, entry["id"], orig_qty_in > 0)
                        if await fifo_lapisan.fifo_aktif(conn, tenant_id, product_id) else None)
        if rencana_fifo is not None:
            total_cost = rencana_fifo.nilai

        # Insert reversal entry — DB trigger auto-updates warehouse_stock
        ledger_id = uuid4()
        await tulis_kartu(
            conn,
            id=ledger_id, tenant_id=tenant_id, product_id=product_id, product_code=entry["product_code"], product_name=entry["product_name"],
            movement_type=movement_type, movement_date=movement_date, source_type=void_source_type, source_id=source_id,
            source_number=entry["source_number"], quantity_in=rev_qty_in, quantity_out=rev_qty_out, quantity_balance=new_balance,
            unit_cost=unit_cost, total_cost=total_cost, average_cost=avg_cost, warehouse_id=entry["warehouse_id"],
            journal_id=reversal_journal_id, created_by=created_by, notes=f"{notes_prefix} - inventory reversal", batch_id=entry["batch_id"])
        if rencana_fifo is not None:
            await fifo_lapisan.catat_balik(conn, tenant_id, ledger_id, rencana_fifo)

        # Reverse batch quantities if batch_id present
        batch_id = (
            entry.get("batch_id") or entry["batch_id"] if entry["batch_id"] else None
        )
        if batch_id and pindah_batch:
            if orig_qty_in > 0:
                # Was inbound (purchase) — reverse = reduce stock
                qty_to_reverse = orig_qty_in
                direction_multiplier = Decimal("-1")
            else:
                # Was outbound (sale) — reverse = restore stock
                qty_to_reverse = orig_qty_out
                direction_multiplier = Decimal("1")

            batch_delta = qty_to_reverse * direction_multiplier

            # Update batch_warehouse_stock
            wh_id = entry.get("warehouse_id")
            if wh_id:
                await conn.execute(
                    """
                    UPDATE batch_warehouse_stock
                    SET quantity = quantity + $3,
                        last_movement_date = NOW(), updated_at = NOW()
                    WHERE batch_id = $1 AND warehouse_id = $2
                """,
                    batch_id,
                    wh_id,
                    float(batch_delta),
                )

            # item_batches.current_quantity synced by trg_sync_batch_quantity trigger

            logger.info(
                f"Batch reversal: batch_id={batch_id} delta={batch_delta} "
                f"warehouse={wh_id}"
            )

        if batch_id:   # BUG-286: penandaan habis tetap berjalan walau pergeseran saldo diserahkan ke trigger
            await conn.execute(
                """
                UPDATE item_batches SET status = 'depleted'
                WHERE id = $1 AND current_quantity <= 0 AND status = 'active'
            """,
                batch_id,
            )
            await conn.execute(   # BUG-290: void yang mengembalikan stok ke batch 'depleted' mengaktifkannya lagi
                """
                UPDATE item_batches SET status = 'active'
                WHERE id = $1 AND current_quantity > 0 AND status = 'depleted'
            """,
                batch_id,
            )

        results.append(
            {
                "product_id": product_id,
                "ledger_id": ledger_id,
                "quantity_reversed": quantity_reversed,
                "direction": "outbound" if rev_qty_out > 0 else "inbound",
            }
        )

        logger.info(
            f"Inventory reversal: {void_source_type} product={product_id} "
            f"qty_in={rev_qty_in} qty_out={rev_qty_out} balance={new_balance}"
        )

    return results
