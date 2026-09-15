"""Sentralisasi 401/403: user tak ada -> 401 USER_NOT_FOUND (auth_mw, sudah); keanggotaan nonaktif/
tak-provisi -> 403 MEMBERSHIP_INACTIVE (samakan; ROLE_INACTIVE + ROLE_NOT_PROVISIONED 409->403);
tak punya izin -> 403 PERMISSION_DENIED (sudah). 3 berkas."""
import io, sys
BE = "/root/mh-law2/backend/api_gateway/app/"

def edit(path, fn):
    t = io.open(path, encoding="utf-8").read()
    t2 = fn(t)
    io.open(path, "w", encoding="utf-8").write(t2)

def repl1(t, old, new, tag):
    if t.count(old) != 1:
        print(f"GAGAL {tag}: {t.count(old)}"); sys.exit(1)
    return t.replace(old, new)

# ---- role_resolution.py ----
def f_rr(t):
    # MSG_INACTIVE -> pesan MASTER
    t = repl1(t,
        'MSG_INACTIVE = (\n    "Akses Anda ke bisnis ini sedang dinonaktifkan. Hubungi pemilik bisnis."\n)',
        'MSG_INACTIVE = (\n    "Akses Anda ke bisnis ini telah dinonaktifkan."\n)', "rr MSG")
    # no-row 409 ROLE_NOT_PROVISIONED -> 403 MEMBERSHIP_INACTIVE (pesan tetap kontekstual)
    t = repl1(t,
        '            status_code=409,\n            detail={"error_code": "ROLE_NOT_PROVISIONED", "message": MSG_NOT_PROVISIONED},',
        '            status_code=403,\n            detail={"error_code": "MEMBERSHIP_INACTIVE", "message": MSG_NOT_PROVISIONED},', "rr norow")
    # inactive
    t = t.replace('"error_code": "ROLE_INACTIVE"', '"error_code": "MEMBERSHIP_INACTIVE"')
    # docstring
    t = t.replace('        409 ROLE_NOT_PROVISIONED — tak ada baris peran\n        403 ROLE_INACTIVE        — ada baris tapi tidak aktif',
                  '        403 MEMBERSHIP_INACTIVE — tak ada baris peran ATAU baris tidak aktif')
    return t

# ---- permission_middleware.py ----
def f_pm(t):
    # 723 block: 409 ROLE_NOT_PROVISIONED -> 403 MEMBERSHIP_INACTIVE
    t = repl1(t,
        '                        status_code=409,\n                        content={\n                            "detail": {\n                                "error_code": "ROLE_NOT_PROVISIONED",',
        '                        status_code=403,\n                        content={\n                            "detail": {\n                                "error_code": "MEMBERSHIP_INACTIVE",', "pm norow")
    # semua ROLE_INACTIVE
    t = t.replace('"error_code": "ROLE_INACTIVE"', '"error_code": "MEMBERSHIP_INACTIVE"')
    return t

# ---- policy_engine_client.py ----
def f_pe(t):
    t = repl1(t,
        '                    status_code=409,\n                    detail={\n                        "error_code": "ROLE_NOT_PROVISIONED",',
        '                    status_code=403,\n                    detail={\n                        "error_code": "MEMBERSHIP_INACTIVE",', "pe norow")
    t = t.replace('"error_code": "ROLE_INACTIVE"', '"error_code": "MEMBERSHIP_INACTIVE"')
    return t

edit(BE + "services/role_resolution.py", f_rr)
edit(BE + "middleware/permission_middleware.py", f_pm)
edit(BE + "services/policy_engine_client.py", f_pe)

# sanity: nol ROLE_INACTIVE / ROLE_NOT_PROVISIONED error_code tersisa; nol 409 utk keanggotaan
for p in ("services/role_resolution.py", "middleware/permission_middleware.py", "services/policy_engine_client.py"):
    s = io.open(BE + p, encoding="utf-8").read()
    for bad in ('"error_code": "ROLE_INACTIVE"', '"error_code": "ROLE_NOT_PROVISIONED"'):
        if bad in s:
            print(f"SISA {bad} di {p}"); sys.exit(1)
print("OK sentralisasi: ROLE_INACTIVE+ROLE_NOT_PROVISIONED -> MEMBERSHIP_INACTIVE (403)")
