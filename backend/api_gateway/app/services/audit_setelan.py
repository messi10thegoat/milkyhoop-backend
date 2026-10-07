"""Audit setelan tenant (7 Okt 2026): siapa mengubah setelan apa, kapan.

Latar: default_quote_signer_user_id grapgrap jadi NULL lewat PATCH /api/settings/accounting (nginx mencatat tiga PATCH dalam 2 menit);
isi body tak terekam dan audit_logs kosong -> "pemilik mengosongkan" vs "cacat FE" TAK bisa dibedakan. Rute setelan harus meninggalkan jejak.

Satu baris audit_logs per PATCH yang BENAR-BENAR mengubah sesuatu: metadata = {changed: [nama medan], diff: {medan: {lama, baru}}}.
`diff` HANYA untuk id/angka/flag/enum. Medan teks bebas (kata pembuka/penutup, catatan, S&K, jabatan, HP) dicatat NAMANYA saja:
isinya bisa panjang dan sebagian data pribadi. Di transaksi pemanggil (atomik dengan UPDATE). Append-only (Law 12).
"""
import json
from decimal import Decimal
from typing import Iterable, Optional

ABAIKAN = frozenset({"id", "tenant_id", "created_at", "updated_at"})


def _nilai(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, Decimal):
        return str(v)
    return str(v)


def ringkas_perubahan(lama, baru, teks_bebas: Iterable[str] = ()) -> Optional[dict]:
    """Murni. lama/baru = baris (Record/dict). -> None bila tak ada medan berubah; selain itu {changed, diff}."""
    teks = frozenset(teks_bebas)
    berubah, diff = [], {}
    for k in baru.keys():
        if k in ABAIKAN:
            continue
        a, b = _nilai(lama.get(k) if lama is not None else None), _nilai(baru[k])
        if a == b:
            continue
        berubah.append(k)
        if k not in teks:
            diff[k] = {"lama": a, "baru": b}
    return {"changed": sorted(berubah), "diff": diff} if berubah else None


async def catat_audit_setelan(conn, tenant_id: str, user_id, event_type: str, entity_type: str, entity_id, ringkas: dict) -> None:
    await conn.execute(
        """INSERT INTO audit_logs (id, "eventType", entity_type, entity_id, tenant_id, source, metadata, success, "createdAt", "userId")
           VALUES (gen_random_uuid()::text, $1, $2, $3::uuid, $4, 'api:settings', $5::jsonb, true, now(), $6)""",
        event_type, entity_type, str(entity_id) if entity_id else None, tenant_id, json.dumps(ringkas), str(user_id or "") or None)


def ringkas_konfigurasi_gaji(lama: dict, configs) -> Optional[dict]:
    """Murni. lama = {(component_id:str, effective_date:iso): baris|None dgn amount/percentage}; configs = daftar item PUT.
    Catat NAMA medan saja (amount/percentage) + komponen + tanggal efektif; ANGKA GAJI TIDAK PERNAH masuk metadata (sensitif).
    -> None bila tak ada yang berubah/baru."""
    out = []
    for c in configs:
        kunci = (str(c.component_id), c.effective_date.isoformat())
        r = lama.get(kunci)
        if r is None:
            out.append({"component_id": kunci[0], "effective_date": kunci[1], "aksi": "tambah", "medan": ["amount", "percentage"]})
            continue
        medan = [m for m, baru in (("amount", c.amount), ("percentage", c.percentage))
                 if (None if r[m] is None else Decimal(str(r[m]))) != (None if baru is None else Decimal(str(baru)))]
        if medan:
            out.append({"component_id": kunci[0], "effective_date": kunci[1], "aksi": "ubah", "medan": medan})
    return {"configs": out, "jumlah": len(out)} if out else None
