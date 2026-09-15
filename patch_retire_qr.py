"""Pensiun QR login: hapus qr_auth + qr_token_service, cabut mount, skip-auth middleware, plumbing hub QR.
JANGAN sentuh device WS. Jangkar tepat; NOL bila tak cocok."""
import io, os, sys, subprocess

BE = "/root/mh-law2/backend/api_gateway/app/"


def rd(p): return io.open(p, encoding="utf-8").read()
def wr(p, t): io.open(p, "w", encoding="utf-8").write(t)
def repl(t, old, new, n, tag):
    c = t.count(old)
    if c != n:
        print(f"GAGAL {tag}: cacah {c}!={n}"); sys.exit(1)
    return t.replace(old, new)


# 1) hapus dua file QR-only
for f in ["routers/qr_auth.py", "services/qr_token_service.py"]:
    subprocess.run(["git", "-C", "/root/mh-law2", "rm", "-q", "backend/api_gateway/app/" + f], check=True)
print("OK hapus qr_auth.py + qr_token_service.py")

# 2) main.py: import + mount
m = rd(BE + "main.py")
m = repl(m, "from .routers import qr_auth\n", "", 1, "main import")
m = repl(m, '\n# QR Login System (Phase: QR Auth)\napp.include_router(qr_auth.router, tags=["qr-auth"])\n',
         "\n", 1, "main mount")
wr(BE + "main.py", m); print("OK main.py")

# 3) auth_middleware: method + call-site
a = rd(BE + "middleware/auth_middleware.py")
METHOD = ('    def _is_qr_public_endpoint(self, path: str) -> bool:\n'
          '        """Check if path matches QR login public endpoints"""\n'
          '        # /api/auth/qr/generate - POST\n'
          '        # /api/auth/qr/status/{token} - GET\n'
          '        # /api/auth/qr/ws/{token} - WebSocket\n'
          '        if path == "/api/auth/qr/generate":\n'
          '            return True\n'
          '        if re.match(r"^/api/auth/qr/status/[^/]+/?$", path):\n'
          '            return True\n'
          '        if re.match(r"^/api/auth/qr/ws/[^/]+/?$", path):\n'
          '            return True\n'
          '        return False\n\n')
a = repl(a, METHOD, "", 1, "mw method")
CALL = ('\n            # Allow QR login public endpoints (no auth for desktop)\n'
        '            if self._is_qr_public_endpoint(path):\n'
        '                logger.info(f"Bypassing auth for QR login endpoint: {path}")\n'
        '                return await call_next(request)\n')
a = repl(a, CALL, "", 1, "mw callsite")
wr(BE + "middleware/auth_middleware.py", a); print("OK auth_middleware.py")

# 4) websocket_hub: docstring, init, method-section (boundary), stats, cleanup block
h = rd(BE + "services/websocket_hub.py")
# docstring
h = repl(h, "Manages WebSocket connections for QR Login, device communication, and Remote Scanner",
         "Manages WebSocket connections for device communication and Remote Scanner", 1, "hub doc1")
h = repl(h,
         "Connections are managed in two pools:\n"
         "1. qr_connections: token -> WebSocket (for QR login flow)\n"
         "2. device_connections: device_id -> tab_id -> WebSocket (for force logout + remote scan)\n",
         "Connections are managed in one pool:\n"
         "1. device_connections: device_id -> tab_id -> WebSocket (for force logout + remote scan)\n",
         1, "hub doc2")
# init
h = repl(h, "        # QR token -> WebSocket (desktop waiting for approval)\n"
            "        self.qr_connections: Dict[str, WebSocket] = {}\n", "", 1, "hub init")
# method section (boundary-based)
S = "    # ================================\n    # QR LOGIN WEBSOCKET METHODS"
D = "    # ================================\n    # DEVICE WEBSOCKET METHODS"
si = h.find(S); di = h.find(D)
if si == -1 or di == -1 or not (si < di):
    print(f"GAGAL hub section bound si={si} di={di}"); sys.exit(1)
h = h[:si] + h[di:]
print("OK hub QR method section dihapus")
# stats line
h = repl(h, '            "qr_connections": len(self.qr_connections),\n', "", 1, "hub stats")
# cleanup QR block
CLEAN = ('        # Check QR connections\n'
         '        async with self._lock:\n'
         '            stale_qr = []\n'
         '            for token, ws in self.qr_connections.items():\n'
         '                try:\n'
         '                    # Try to ping - if fails, connection is dead\n'
         '                    await ws.send_json({"event": "ping"})\n'
         '                except Exception:\n'
         '                    stale_qr.append(token)\n\n'
         '            for token in stale_qr:\n'
         '                del self.qr_connections[token]\n'
         '                cleaned += 1\n\n'
         '            # Check device connections (multi-tab structure)\n')
CLEAN_NEW = ('        async with self._lock:\n'
             '            # Check device connections (multi-tab structure)\n')
h = repl(h, CLEAN, CLEAN_NEW, 1, "hub cleanup")
wr(BE + "services/websocket_hub.py", h); print("OK websocket_hub.py")

# 5) sanity: tak ada sisa ref qr_connections/register_qr/qr_auth
leftover = 0
for p in ["main.py", "middleware/auth_middleware.py", "services/websocket_hub.py"]:
    t = rd(BE + p)
    for tok in ["qr_connections", "register_qr", "unregister_qr", "send_to_qr",
                "is_qr_connected", "qr_auth", "_is_qr_public_endpoint"]:
        if tok in t:
            print(f"SISA '{tok}' di {p}"); leftover += 1
print("SEMUA OK" if leftover == 0 else f"ADA SISA: {leftover}")
sys.exit(1 if leftover else 0)
