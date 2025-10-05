# scripts/fix_prisma_client.py

import os

TARGET_DIR = "/app/libs/milkyhoop_prisma"
OLD_FILE = os.path.join(TARGET_DIR, "types.py")
NEW_FILE = os.path.join(TARGET_DIR, "prisma_types.py")

if os.path.exists(OLD_FILE):
    os.rename(OLD_FILE, NEW_FILE)
    print(f"✅ Renamed {OLD_FILE} → {NEW_FILE}")
else:
    print(f"⚠️ File {OLD_FILE} not found, skip renaming.")

