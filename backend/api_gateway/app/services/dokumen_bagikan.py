"""Tautan publik dokumen SO + lacak terkirim/dibuka (P5 SO-dokumen, 02-DATA-DAN-API §Kirim & lacak, 1 Okt 2026).

Keamanan (desain 26 Sep, docs proposals/2026-09-26-share-link-proforma.md, diperluas ke semua jenis):
- token = secrets.token_urlsafe(32) (256 bit), dikembalikan SEKALI; DB hanya menyimpan sha256(token).
- pencarian lewat hash (tanpa perbandingan string token -> tanpa kebocoran waktu per karakter).
- tenant DIAMBIL dari baris tautan (tak ada input tenant dari luar -- Law 24); filter tenant eksplisit di tiap kueri.
- kedaluwarsa 30 hari (spek), bisa dicabut.
- "dibuka" dicatat lewat suar (POST dari JS halaman publik), BUKAN saat GET halaman: bot pratinjau tautan (WhatsApp
  dll.) mengambil URL tanpa menjalankan JS -> tak boleh terhitung "dibuka". Suar membawa token aplikasi bila peramban
  punya sesi; token SAH milik tenant yang sama = kunjungan INTERNAL -> tak dihitung (spek 05).
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

BERLAKU_HARI = 30
KANAL = ("wa", "email", "link")
URL_DASAR = "https://milkyhoop.com/d/"
POLA_TOKEN = r"[A-Za-z0-9_-]{43}"  # token_urlsafe(32) = 43 karakter


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


async def buat_tautan(conn, tenant_id: str, kind: str, doc_id, channel: str, user_id) -> dict:
    """Satu baris = satu kiriman (kanal tercatat). -> {id, url, token, expires_at, channel, sent_at}."""
    if channel not in KANAL:
        raise ValueError(f"kanal harus salah satu dari {KANAL}")
    token = secrets.token_urlsafe(32)
    kedaluwarsa = datetime.now(timezone.utc) + timedelta(days=BERLAKU_HARI)
    row = await conn.fetchrow(
        """INSERT INTO document_shares (tenant_id, kind, doc_id, token_hash, channel, created_by, expires_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7)
           RETURNING id, sent_at, expires_at""",
        tenant_id, kind, doc_id, hash_token(token), channel, user_id, kedaluwarsa,
    )
    return {"id": str(row["id"]), "url": URL_DASAR + token, "token": token, "channel": channel,
            "sent_at": row["sent_at"].isoformat(), "expires_at": row["expires_at"].isoformat()}


async def cari_tautan(conn, token: str) -> Optional[dict]:
    """Baris tautan untuk token (apa pun keadaannya) atau None. Pemanggil memutuskan berlaku/tidak."""
    row = await conn.fetchrow(
        """SELECT id, tenant_id, kind, doc_id, expires_at, revoked_at FROM document_shares WHERE token_hash = $1""",
        hash_token(token),
    )
    return dict(row) if row else None


def berlaku(t: dict, sekarang: Optional[datetime] = None) -> bool:
    sekarang = sekarang or datetime.now(timezone.utc)
    return t["revoked_at"] is None and t["expires_at"] > sekarang


async def catat_dibuka(conn, t: dict) -> None:
    await conn.execute(
        """UPDATE document_shares SET first_viewed_at = COALESCE(first_viewed_at, now()), last_viewed_at = now(),
                  view_count = view_count + 1
           WHERE id = $1 AND tenant_id = $2""",
        t["id"], t["tenant_id"],
    )


async def cabut(conn, tenant_id: str, kind: str, doc_id, share_id, user_id) -> bool:
    r = await conn.execute(
        """UPDATE document_shares SET revoked_at = now(), revoked_by = $5
           WHERE id = $1 AND tenant_id = $2 AND kind = $3 AND doc_id = $4 AND revoked_at IS NULL""",
        share_id, tenant_id, kind, doc_id, user_id,
    )
    return r.endswith(" 1")


async def daftar_tautan(conn, tenant_id: str, kind: str, doc_id) -> list:
    """Tanpa token (tak bisa dipulihkan dari hash)."""
    return [{"id": str(r["id"]), "channel": r["channel"], "sent_at": r["sent_at"].isoformat(),
             "expires_at": r["expires_at"].isoformat(),
             "first_viewed_at": r["first_viewed_at"].isoformat() if r["first_viewed_at"] else None,
             "last_viewed_at": r["last_viewed_at"].isoformat() if r["last_viewed_at"] else None,
             "view_count": r["view_count"], "revoked_at": r["revoked_at"].isoformat() if r["revoked_at"] else None}
            for r in await conn.fetch(
                """SELECT id, channel, sent_at, expires_at, first_viewed_at, last_viewed_at, view_count, revoked_at
                   FROM document_shares WHERE tenant_id = $1 AND kind = $2 AND doc_id = $3 ORDER BY sent_at DESC""",
                tenant_id, kind, doc_id)]


async def keadaan_kirim(conn, tenant_id: str, pasangan: list) -> dict:
    """{(kind, doc_id_str): {state, sent_at, viewed_at}} untuk panel. viewed bila ADA tautan yang pernah dibuka
    (kunjungan pertama paling awal); sent bila ada tautan; tautan dicabut tetap terhitung sebagai pernah terkirim."""
    if not pasangan:
        return {}
    rows = await conn.fetch(
        """SELECT kind, doc_id::text AS doc_id, MAX(sent_at) AS sent_at, MIN(first_viewed_at) AS viewed_at
           FROM document_shares WHERE tenant_id = $1 AND (kind, doc_id::text) IN (SELECT * FROM unnest($2::text[], $3::text[]))
           GROUP BY 1, 2""",
        tenant_id, [k for k, _ in pasangan], [str(d) for _, d in pasangan],
    )
    return {(r["kind"], r["doc_id"]): {"state": "viewed" if r["viewed_at"] else "sent",
                                         "sent_at": r["sent_at"].isoformat() if r["sent_at"] else None,
                                         "viewed_at": r["viewed_at"].isoformat() if r["viewed_at"] else None}
            for r in rows}


def tenant_dari_jwt(authorization: Optional[str]) -> Optional[str]:
    """tenant_id dari token aplikasi SAH (tanda tangan diverifikasi) atau None. Dipakai HANYA untuk memutuskan
    kunjungan internal (tak menghitung "dibuka"); token tak sah = diperlakukan pengunjung luar."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        import jwt
        from ..utils.rahasia_jwt import jwt_secret_wajib
        muatan = jwt.decode(authorization[7:], jwt_secret_wajib(), algorithms=["HS256"])
        return muatan.get("tenant_id")
    except Exception:
        return None
