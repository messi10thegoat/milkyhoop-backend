"""
Financial Formatter
Transform analytics data into user-friendly Indonesian language

Phase 2 Implementation - Analytics Formatting
"""

import logging

logger = logging.getLogger(__name__)


def format_rupiah(amount: int) -> str:
    """
    Format rupiah to Indonesian Rupiah (PUEBI + SAK EMKM compliant)
    
    Args:
        amount: Amount in full Rupiah (NOT cents)
        
    Returns:
        Formatted string: "Rp70.000.000" (titik sebagai pemisah ribuan)
    """
    formatted = f"{amount:,}".replace(",", ".")
    return f"Rp{formatted}"


def format_top_products_response(products: list, time_range: str) -> str:
    """
    Format top products analytics in user-friendly Indonesian
    
    Args:
        products: List of ProductSales proto objects
        time_range: 'daily', 'weekly', 'monthly'
        
    Returns:
        Formatted Indonesian response string
    """
    if not products:
        time_desc = {
            "daily": "hari ini",
            "weekly": "minggu ini",
            "monthly": "bulan ini"
        }
        return f"Belum ada penjualan {time_desc.get(time_range, 'periode ini')}. Yuk mulai catat transaksi!"
    
    # Time range display
    time_desc_title = {
        "daily": "Hari Ini",
        "weekly": "Minggu Ini",
        "monthly": "Bulan Ini"
    }
    
    response = f"🏆 Produk Terlaris {time_desc_title.get(time_range, 'Periode Ini')}:\n\n"
    
    # Product emojis
    emojis = ["👕", "👖", "🧢", "👟", "🎒", "📦", "🛍️", "📱", "💼", "🎁"]
    
    # Show top 5 products
    top_products = products[:5]
    for i, product in enumerate(top_products, 1):
        emoji = emojis[i-1] if i <= len(emojis) else "📦"
        response += f"{i}. {emoji} {product.product_name}\n"
        response += f"   Terjual: {int(product.quantity_sold)} {product.unit}\n"
        response += f"   Omzet: {format_rupiah(product.total_revenue)}\n"
        if i < len(top_products):
            response += "\n"
    
    # Summary
    total_qty = sum(p.quantity_sold for p in top_products)
    total_revenue = sum(p.total_revenue for p in top_products)
    
    response += f"\n💰 Total dari {len(top_products)} produk teratas:\n"
    response += f"└─ {int(total_qty)} pcs terjual, {format_rupiah(total_revenue)} omzet\n"
    
    # Insight
    if top_products:
        top_product = top_products[0]
        response += f"\n💡 Insight: {top_product.product_name} paling laku! "
        response += f"Pastikan stok selalu tersedia ya!"
    
    return response


def format_low_sell_products_response(products: list, time_range: str) -> str:
    """
    Format low-sell products analytics in user-friendly Indonesian
    
    Args:
        products: List of ProductLowSell proto objects
        time_range: 'daily', 'weekly', 'monthly'
        
    Returns:
        Formatted Indonesian response string
    """
    if not products:
        time_desc = {
            "daily": "hari ini",
            "weekly": "minggu ini",
            "monthly": "bulan ini"
        }
        return f"Bagus! Semua produk laku lancar {time_desc.get(time_range, 'periode ini')} 🎉\n\nTidak ada produk yang perlu perhatian khusus."
    
    # Time range display
    time_desc_title = {
        "daily": "Hari Ini",
        "weekly": "Minggu Ini",
        "monthly": "Bulan Ini"
    }
    
    response = f"⚠️ Produk Kurang Laku {time_desc_title.get(time_range, 'Periode Ini')}:\n\n"
    
    # Show top 5 low-sell products
    low_products = products[:5]
    for i, product in enumerate(low_products, 1):
        response += f"{i}. 📦 {product.product_name}\n"
        response += f"   Terjual: {int(product.quantity_sold)} {product.unit} "
        response += f"(dari stok {int(product.current_stock)} {product.unit})\n"
        response += f"   Omzet: {format_rupiah(product.total_revenue)}\n"
        response += f"   💡 {product.suggestion}\n"
        if i < len(low_products):
            response += "\n"
    
    # Summary
    total_qty_sold = sum(p.quantity_sold for p in low_products)
    total_stock = sum(p.current_stock for p in low_products)
    avg_turnover = (total_qty_sold / total_stock * 100) if total_stock > 0 else 0
    
    response += f"\n⚠️ Total: {int(total_qty_sold)} pcs terjual dari {int(total_stock)} pcs stok "
    response += f"({avg_turnover:.1f}% turnover)\n"
    
    # Action plan
    response += f"\n💡 Action Plan:\n"
    response += f"├─ Flash sale weekend: Diskon 30%\n"
    response += f"├─ Bundle deal: Beli 2 gratis 1\n"
    response += f"└─ Promosi Instagram: Foto produk lifestyle\n"
    
    return response