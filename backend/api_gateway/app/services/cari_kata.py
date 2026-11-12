"""Pencarian per KATA yang SATU untuk daftar barang (`?search=`), ringkasan, dan autocomplete barang (BUG-181, putusan MASTER 12 Nov 2026).

Aturan: q dipecah per spasi (spasi ganda/tepi dirapikan); SEMUA kata harus ada, urutan bebas; satu kata cocok bila ada di SALAH SATU kolom
(`kolom`). `%`, `_`, dan `\\` di masukan di-ESCAPE (literal, bukan wildcard). Kolom berasal dari kode pemanggil (tak pernah dari pengguna)."""
from typing import Sequence, Tuple


def escape_like(kata: str) -> str:
    return kata.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def syarat_kata(q, kolom: Sequence[str], idx: int) -> Tuple[str, list]:
    """-> ("AND ((k1 ILIKE $i OR k2 ILIKE $i) AND ...)" | "", params). Placeholder mulai $idx, satu per kata (dipakai ulang antarkolom)."""
    kata = q.split() if isinstance(q, str) else []
    if not kata:
        return "", []
    per_kata = [" OR ".join(f"{k} ILIKE ${idx + i}" for k in kolom) for i in range(len(kata))]
    return "AND (" + " AND ".join(f"({p})" for p in per_kata) + ")", [f"%{escape_like(w)}%" for w in kata]
