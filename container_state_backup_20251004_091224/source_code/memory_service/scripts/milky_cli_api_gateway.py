#!/usr/bin/env python3

import os
import shutil
import socket
from pathlib import Path

def find_available_port(start=5000, end=5999):
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('', port))
                return port
            except OSError:
                continue
    raise RuntimeError("❌ Tidak ada port tersedia dalam rentang 5000–5999")

def create_api_gateway_template():
    """
    Clone template REST API + gRPC Client + Prisma ke backend/api_gateway.
    """
    template_dir = Path("backend/services/template-service-python-prisma")
    dest_dir = Path("backend/api_gateway")

    if dest_dir.exists():
        print("❌ Folder backend/api_gateway sudah ada! Rename dulu.")
        return

    # Clone dari template
    shutil.copytree(template_dir, dest_dir)
    print("✅ Template backend/api_gateway berhasil dicloning dari template-service-python-prisma.")

    # Tambahkan __init__.py di setiap subfolder
    for subdir, _, _ in os.walk(dest_dir):
        init_file = Path(subdir) / "__init__.py"
        if not init_file.exists():
            init_file.touch()
            print(f"📝 __init__.py dibuat di {subdir}")

    # Sesuaikan nama service
    grpc_port = find_available_port()
    class_name = "ApiGateway"
    service_name = "api_gateway"

    # Replace placeholders di grpc_server.py
    grpc_file = dest_dir / "app" / "grpc_server.py"
    if grpc_file.exists():
        content = grpc_file.read_text()
        content = content.replace('TemplateService', class_name)
        content = content.replace('template_service', service_name)
        content = content.replace('os.getenv("GRPC_PORT", "5009")', f'"{grpc_port}"')
        grpc_file.write_text(content)
        print(f"🔧 grpc_server.py disesuaikan (class {class_name}, port {grpc_port}).")

    # Tambahkan file .env
    env_file = dest_dir / ".env"
    env_file.write_text(
        f"GRPC_PORT={grpc_port}\n"
        f"DATABASE_URL=postgresql://postgres:Proyek771977@db.ltrqrejrkbusvmknpnwb.supabase.co:5432/postgres?sslmode=require\n"
    )
    print(f"📝 File .env dibuat dengan GRPC_PORT={grpc_port}.")

    # Generate Dockerfile baru
    dockerfile_template = Path("backend/services/template-service-python-prisma/Dockerfile")
    dockerfile_dest = dest_dir / "Dockerfile"
    if dockerfile_template.exists():
        content = dockerfile_template.read_text().replace("template-service-python-prisma", "api_gateway")
        dockerfile_dest.write_text(content)
        print("✅ Dockerfile disesuaikan & disalin.")

    # Patch import prisma ke milkyhoop_prisma
    grpc_file_content = grpc_file.read_text()
    if "from backend.api_gateway.libs.milkyhoop_prisma import Prisma" not in grpc_file_content:
        grpc_file_content = "from backend.api_gateway.libs.milkyhoop_prisma import Prisma\n" + grpc_file_content
        grpc_file.write_text(grpc_file_content)
        print("🧩 Prisma import patched di grpc_server.py.")

    # Patch logging di grpc_server.py
    grpc_file_content = grpc_file.read_text()
    grpc_file_content = grpc_file_content.replace(
        'logger.info("🚀 TemplateService gRPC server listening on port %s", grpc_port)',
        f'logger.info("🚀 {class_name} gRPC server (entrypoint REST API) listening on port %s", grpc_port)'
    )
    grpc_file.write_text(grpc_file_content)

    # Bersihkan __pycache__ & .pyc
    os.system(f"find {dest_dir} -type d -name '__pycache__' -exec rm -rf {{}} +")
    os.system(f"find {dest_dir} -name '*.pyc' -delete")
    print("🧹 Bersihkan __pycache__ & file .pyc selesai.")

    print("🎉 Modul api_gateway siap digunakan!")
    print("⚠️ Langkah manual: generate ulang stub gRPC jika perlu & implementasikan REST API logic di FastAPI.")

if __name__ == "__main__":
    create_api_gateway_template()
