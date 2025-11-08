"""
Response Formatter
Transform technical accounting data into user-friendly Indonesian language

Phase 2 Implementation - Laba Rugi Improvement
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def format_rupiah(amount_in_cents: int) -> str:
    """Format rupiah with Indonesian format (titik as thousand separator)"""
    rupiah = amount_in_cents // 100
    formatted = f"{rupiah:,}".replace(",", ".")
    return f"Rp{formatted}"


def format_laba_rugi_user_friendly(
    periode: str,
    total_pendapatan: int,
    total_beban: int,
    laba_bersih: int,
    jumlah_transaksi: int,
    breakdown_pendapatan: dict = None,
    breakdown_beban: dict = None,
    comparison_last_month: dict = None
) -> str:
    """
    Format Laba Rugi report in user-friendly Indonesian language
    
    Args:
        periode: 'YYYY-MM' format
        total_pendapatan: in cents
        total_beban: in cents
        laba_bersih: in cents
        jumlah_transaksi: count
        breakdown_pendapatan: Optional dict of revenue breakdown
        breakdown_beban: Optional dict of expense breakdown
        comparison_last_month: Optional dict with last month data
    
    Returns:
        Formatted string for Milky response
    """
    # Parse periode for display
    try:
        period_date = datetime.strptime(periode, "%Y-%m")
        period_display = period_date.strftime("%B %Y")  # "November 2025"
        # Translate to Indonesian
        months_id = {
            "January": "Januari", "February": "Februari", "March": "Maret",
            "April": "April", "May": "Mei", "June": "Juni",
            "July": "Juli", "August": "Agustus", "September": "September",
            "October": "Oktober", "November": "November", "December": "Desember"
        }
        for en, id in months_id.items():
            period_display = period_display.replace(en, id)
    except:
        period_display = periode
    
    # Build response
    response = f"💰 Ringkasan Keuangan {period_display}\n\n"
    
    # PEMASUKAN Section
    response += "📈 PEMASUKAN\n"
    if breakdown_pendapatan:
        for kategori, nominal in breakdown_pendapatan.items():
            response += f"├─ {kategori}: {format_rupiah(nominal)}\n"
    else:
        response += f"├─ Total penjualan: {format_rupiah(total_pendapatan)}\n"
    response += f"└─ ({jumlah_transaksi} kali transaksi)\n\n"
    
    # PENGELUARAN Section
    response += "📉 PENGELUARAN\n"
    if breakdown_beban:
        items = list(breakdown_beban.items())
        for i, (kategori, nominal) in enumerate(items):
            prefix = "├─" if i < len(items) - 1 else "└─"
            response += f"{prefix} {kategori}: {format_rupiah(nominal)}\n"
    else:
        if total_beban > 0:
            response += f"└─ Total pengeluaran: {format_rupiah(total_beban)}\n"
        else:
            response += f"└─ Belum ada pengeluaran bulan ini\n"
    
    if total_beban > 0:
        response += f"Total: {format_rupiah(total_beban)}\n"
    response += "\n"
    
    # KESIMPULAN Section
    response += "✨ KESIMPULAN:\n"
    if laba_bersih >= 0:
        response += f"✅ Untung bulan ini: {format_rupiah(laba_bersih)}\n"
        # Add tip for savings
        savings_20pct = laba_bersih * 20 // 100
        response += f"💡 Tips: Sisihkan 20% ({format_rupiah(savings_20pct)}) untuk modal bulan depan!\n"
    else:
        rugi_amount = abs(laba_bersih)
        response += f"⚠️ Rugi bulan ini: {format_rupiah(rugi_amount)}\n"
        response += f"💡 Tips: Review pengeluaran dan cari cara tingkatkan penjualan!\n"
    
    # COMPARISON with last month
    if comparison_last_month:
        response += "\n📊 Perbandingan:\n"
        last_laba = comparison_last_month.get('laba_bersih', 0)
        if last_laba != 0:
            change_pct = ((laba_bersih - last_laba) / abs(last_laba)) * 100
            if change_pct > 0:
                response += f"└─ Bulan lalu untung {format_rupiah(last_laba)} (+{change_pct:.1f}% 📈)\n"
            elif change_pct < 0:
                response += f"└─ Bulan lalu untung {format_rupiah(last_laba)} ({change_pct:.1f}% 📉)\n"
            else:
                response += f"└─ Sama seperti bulan lalu: {format_rupiah(last_laba)}\n"
    
    return response