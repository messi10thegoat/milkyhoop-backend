"""(A) Pensiunkan X-Source: hapus bypass auth_middleware + jalur klien gRPC di unified_chat (LIVE) + compose service.
action_chat DEPRECATED & TAK di-mount (main.py:762) -> referensinya mati; ditangani terpisah (dilaporkan). Jangkar tepat."""
import io
import sys

AM = "backend/api_gateway/app/middleware/auth_middleware.py"
UC = "backend/api_gateway/app/routers/unified_chat.py"
teks = {p: io.open(p, encoding="utf-8").read() for p in (AM, UC)}
galat = []


def ganti(p, lama, baru):
    if teks[p].count(lama) != 1:
        galat.append(f"{p}: jangkar {teks[p].count(lama)}x :: {lama[:60]!r}")
        return
    teks[p] = teks[p].replace(lama, baru)


# 1) auth_middleware: hapus bypass X-Source seluruhnya
ganti(AM, '''
            # Allow internal service requests (action_executor calling kernel endpoints)
            x_source = request.headers.get("X-Source", "")
            x_tenant = request.headers.get("X-Tenant-ID", "")
            x_user = request.headers.get("X-User-ID", "")
            if x_source == "action_executor" and x_tenant:
                request.state.user = {
                    "tenant_id": x_tenant,
                    "user_id": x_user or "",
                    "role": "ADMIN",
                    "source": "action_executor",
                }
                logger.info(
                    f"Internal service auth bypass: {x_source} tenant={x_tenant}"
                )
                return await call_next(request)
''', '''
            # 14 Sep 2026: bypass internal X-Source=action_executor DIHAPUS (pensiun layanan action_executor).
            # Dulu: X-Source + X-Tenant-ID -> role ADMIN tanpa auth -> lubang kepercayaan-header (siapa pun yang bisa
            # menyetel header itu + X-User-ID bertindak sebagai siapa saja). Layanan gRPC-nya mati (host tak resolve,
            # 0 kejadian 30 hari, tak ada container); chat mengeksekusi lewat penerusan JWT pengguna (jalur is_direct).
''')

# 2) unified_chat: hapus import klien
ganti(UC, "from ..services.action_executor_client import get_action_executor_client\n", "")

# 3) confirm_action non-direct: jalur gRPC pensiun -> hasil gagal terbaca (blok 'if success' yang ada dilewati)
ganti(UC, '''        executor = get_action_executor_client()
        result = await executor.execute_action(
            pending_action_id=body.pending_action_id,
            doc_status=body.doc_status,
            tenant_id=ctx["tenant_id"],
            user_id=ctx["user_id"],
        )
''', '''        # 14 Sep 2026: jalur eksekusi gRPC action_executor PENSIUN. Aksi non-direct (is_direct=false) tak lagi
        # dieksekusi lewat sini; semua pending_actions hidup sejak 3 Sep berstatus is_direct=true (jalur REST + JWT
        # di atas). 4 baris non-direct terakhir (2-3 Sep) semuanya kedaluwarsa/dibatalkan. Kembalikan gagal terbaca.
        result = {
            "success": False,
            "error_message": "Aksi ini tidak bisa diproses lewat jalur lama. Muat ulang dan coba lagi dari chat.",
            "error_code": "ACTION_EXECUTOR_RETIRED",
        }
''')

# 4) get_action_status: baca status dari DB (bukan gRPC)
ganti(UC, '''    try:
        executor = get_action_executor_client()
        result = await executor.get_action_status(
            action_id=pending_action_id,
            tenant_id=ctx["tenant_id"],
        )

        return ActionStatusResponse(
            pending_action_id=pending_action_id,
            status=result.get("status", "UNKNOWN"),
            message=result.get("message"),
            data=result.get("data"),
        )

    except Exception as e:
        logger.exception(f"[Status] Failed for {pending_action_id}")
        raise HTTPException(status_code=500, detail=f"Status check failed: {str(e)}")
''', '''    # 14 Sep 2026: status dibaca dari DB (pending_actions) — jalur gRPC action_executor pensiun.
    try:
        pool = await get_session_db_pool()
        row = await pool.fetchrow(
            "SELECT status FROM pending_actions WHERE id = $1 AND tenant_id = $2",
            uuid_mod.UUID(pending_action_id),
            ctx["tenant_id"],
        )
        if not row:
            return ActionStatusResponse(
                pending_action_id=pending_action_id, status="NOT_FOUND", message="Aksi tidak ditemukan", data=None
            )
        return ActionStatusResponse(
            pending_action_id=pending_action_id, status=str(row["status"]).upper(), message=None, data=None
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"[Status] Failed for {pending_action_id}")
        raise HTTPException(status_code=500, detail="Status check failed")
''')

if galat:
    print("GAGAL — NOL ditulis:\n  " + "\n  ".join(galat)); sys.exit(1)
for p, t in teks.items():
    io.open(p, "w", encoding="utf-8").write(t)
print("X-Source bypass + klien gRPC unified_chat dihapus")
