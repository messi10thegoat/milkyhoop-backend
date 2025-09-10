import os

# Daftar environment variables yang harus ada
required_env_vars = [
    "DATABASE_URL",
    "API_KEY",
    "SECRET_KEY",
    "DEBUG",
    "JWT_SECRET"
]

# Memeriksa apakah semua environment variable sudah diatur
missing_vars = [var for var in required_env_vars if var not in os.environ]

if missing_vars:
    print("Peringatan: Environment variable berikut belum diset:")
    for var in missing_vars:
        print(f"- {var}")
    exit(1)
else:
    print("Semua environment variable telah diset dengan benar.")

