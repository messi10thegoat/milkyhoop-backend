"""Flag fitur per tenant (W0 Conversational Workspace, V304 tenant_features).

Satu-satunya pembaca flag. Kontrak: daftar flag AKTIF, terurut; flag tak tercantum = mati.
Gagal membaca = [] + WARNING — arah aman: fitur baru mati, izin pengguna tak ikut gagal.
"""

import logging
from typing import List

logger = logging.getLogger(__name__)


async def fitur_aktif(conn, tenant_id: str) -> List[str]:
    try:
        baris = await conn.fetch(
            "SELECT feature FROM tenant_features WHERE tenant_id = $1 AND enabled ORDER BY feature",
            tenant_id,
        )
        return [b["feature"] for b in baris]
    except Exception as e:
        logger.warning("[FITUR] gagal membaca flag tenant %s: %s -> features=[]", tenant_id, e)
        return []
