"""Sabotase VENDOR-IDEM pada SALINAN api_gateway (tiap ubahan tepat sekali, dicek). argv: <api_gateway> <mode>
S_idem/M_ulang = idem dicabut dari status+hapus; S_kunci = kunci nama dicabut dari buat; S_parkir = parkir gabung dicabut; S_sumber = VENDOR_CREATE
keluar dari SUMBER_BUAT."""
import sys
r = sys.argv[1]; m = sys.argv[2]
V = r + "/app/routers/vendors.py"; I = r + "/app/utils/idempotency.py"
UB = {
    "idem": [(V, '''kp, sidik, lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "VENDOR_STATUS", vendor_id,
                                                             {"status": status}, response)''', 'kp, sidik, lama = None, None, None'),
             (V, 'kp, sidik, lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "VENDOR_DELETE", vendor_id, {}, response)',
              'kp, sidik, lama = None, None, None')],
    "kunci": [(V, '''                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"VENDOR_NAMA:{ctx['tenant_id']}:{body.name.strip().lower()}")\n''', '')],
    "parkir": [(V, ''', dependencies=[Depends(fitur_belum_tersedia_dengan(
    "Gabung vendor sedang diperbaiki dan belum bisa dipakai. Sementara ini, nonaktifkan vendor yang dobel."))])''', ')')],
    "sumber": [(I, '\n    "VENDOR_CREATE": ("vendors", None, ()),  # VENDOR-IDEM 25 Okt: vendor terhapus keras -> kunci basi, proses baru', '')],
}
j = {"S_idem": "idem", "M_ulang": "idem", "S_kunci": "kunci", "S_parkir": "parkir", "S_sumber": "sumber"}[m]
for p, a, b in UB[j]:
    s = open(p).read(); assert s.count(a) == 1, f"titik {m} tak tepat sekali: {s.count(a)}"
    open(p, "w").write(s.replace(a, b))
print("terpasang", m)
