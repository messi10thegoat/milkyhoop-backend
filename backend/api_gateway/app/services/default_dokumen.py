"""Default yang bisa ditimpa, tahap 1 (30 Sep 2026, putusan pemilik; riset BC/Odoo/Xero/NetSuite/QBO/Zoho/SAP).

SATU tempat untuk (1) aturan uang muka pesanan (persen <-> nominal) dan (2) penentu default dokumen.

Uang muka di SO = SYARAT, bukan transaksi: nol jurnal di sini. Saat diterima ia tetap KEWAJIBAN (Uang Muka Pelanggan),
bukan pendapatan -- itu jalur customer_deposits, tak disentuh.

Aturan nominal (sama dengan quotes.resolve_dp): nominal diketik = TIMPA (sumber 'manual', terkunci); hanya persen =
nominal DIHITUNG SERVER = ROUND_HALF_UP(total x persen / 100) ke rupiah (sumber 'percent', ikut total bila total
berubah). Keduanya kosong = tanpa uang muka.

Penentu default (tingkat PERUSAHAAN; tahap 2 = per pelanggan): persen = accounting_settings.default_dp_percent;
rekening penerimaan = bank_accounts.is_default (bendera EKSPLISIT "utama", satu per tenant, V350), BUKAN urutan
daftar. Server TIDAK mengisi dokumen sendiri: FE menampilkan default (penanda) dan mengirim nilainya; dokumen
menyalin (snapshot) -- setelan diubah tak menyentuh dokumen lama.
"""
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

_SERATUS = Decimal("100")


def hitung_dp(total, dp_percent, dp_amount) -> dict:
    """-> {dp_percent: Decimal|None, dp_amount: Decimal|None, dp_amount_source: 'manual'|'percent'|None}."""
    if dp_amount is not None:
        return {"dp_percent": Decimal(str(dp_percent)) if dp_percent is not None else None,
                "dp_amount": Decimal(str(dp_amount)).quantize(Decimal("1"), rounding=ROUND_HALF_UP),
                "dp_amount_source": "manual"}
    if dp_percent is not None:
        pct = Decimal(str(dp_percent))
        nominal = (Decimal(str(total or 0)) * pct / _SERATUS).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return {"dp_percent": pct, "dp_amount": nominal, "dp_amount_source": "percent"}
    return {"dp_percent": None, "dp_amount": None, "dp_amount_source": None}


async def default_pesanan(conn, tenant_id: str, customer_id: Optional[str] = None) -> dict:
    """Default form Pesanan Penjualan (tahap 1: tingkat perusahaan; customer_id diterima untuk tahap 2)."""
    pct = await conn.fetchval("SELECT default_dp_percent FROM accounting_settings WHERE tenant_id = $1", tenant_id)
    rek = await conn.fetchrow(
        """SELECT id, account_name, bank_name, account_number, account_holder_name
           FROM bank_accounts WHERE tenant_id = $1 AND is_default AND is_active""",
        tenant_id,
    )
    return {
        # 0 = sama dengan tanpa default (permintaan WORKSPACE 30 Sep)
        "dp_percent": ({"value": float(pct), "source": "company"} if pct is not None and pct > 0 else None),
        "receiving_account": ({
            "id": str(rek["id"]), "account_name": rek["account_name"], "bank_name": rek["bank_name"],
            "account_number": rek["account_number"], "account_holder": rek["account_holder_name"],
            "source": "company_main",
        } if rek else None),
    }


# Masa berlaku penawaran bawaan SISTEM (MASTER 3 Okt: 14 hari = yang dipakai sekarang). Tahap berikut: ditimpa per
# tenant/pelanggan (pola "default bisa ditimpa"); source berubah dari 'system' ke 'company'/'customer'.
MASA_BERLAKU_PENAWARAN_HARI = 14


async def default_penawaran(conn, tenant_id: str, hari_ini, customer_id: Optional[str] = None) -> dict:
    """Default form Penawaran = default Pesanan (persen DP + rekening utama) + teks pembuka/penutup (Pengaturan
    > Default Penawaran) + masa berlaku. hari_ini = tanggal usaha
    zona tenant (pemanggil). expiry_date = usulan, FE yang mengirimnya (server tak mengisi dokumen sendiri)."""
    from datetime import timedelta
    d = await default_pesanan(conn, tenant_id, customer_id)
    teks = await conn.fetchrow(
        """SELECT default_quote_opening_text, default_quote_closing_text FROM accounting_settings
           WHERE tenant_id = $1""", tenant_id)
    for k, kol in (("opening_text", "default_quote_opening_text"), ("closing_text", "default_quote_closing_text")):
        v = (teks[kol] if teks else None) or ""
        d[k] = {"value": v, "source": "company"} if v.strip() else None  # kosong = tanpa default (tak dikarang)
    d["validity_days"] = {"value": MASA_BERLAKU_PENAWARAN_HARI, "source": "system",
                          "expiry_date": (hari_ini + timedelta(days=MASA_BERLAKU_PENAWARAN_HARI)).isoformat()}
    return d
