"""Normalisasi dump skema untuk banding V494 apply->rollback: buang baris token \\restrict/\\unrestrict (acak per pg_dump) dan bandingkan
CHECK chk_da_entity sebagai HIMPUNAN nilai (bentuk ekspresi dinormalkan beda sesudah constraint dibuat ulang; nilainya yang penting)."""
import re
import sys

keluar = []
for baris in sys.stdin.read().splitlines():
    if re.match(r"^\\(un)?restrict ", baris):
        continue
    if "CONSTRAINT chk_da_entity CHECK" in baris:
        nilai = sorted(set(re.findall(r"'([a-z_]+)'::character varying", baris)))
        baris = "    CONSTRAINT chk_da_entity NILAI " + ",".join(nilai) + ("," if baris.rstrip().endswith(",") else "")
    keluar.append(baris)
print("\n".join(keluar))
