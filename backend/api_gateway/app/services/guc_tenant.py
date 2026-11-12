"""Pasang GUC app.tenant_id di transaksi berjalan. BUKAN pagar tenant (BYPASSRLS): hanya agar penulis modul yang membacanya (trigger/fungsi DB) bekerja sama
seperti saat dipanggil rute modulnya. Pagar tenant tetap filter tenant_id eksplisit di tiap SQL."""


async def pasang_tenant(conn, tid: str) -> None:
    await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tid)
