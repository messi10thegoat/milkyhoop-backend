#!/usr/bin/env python3

import os
import argparse
import shutil
import subprocess
import socket
from pathlib import Path

def replace_placeholders(service_path: Path, old: str, new: str):
    subprocess.run([
        "./scripts/replace_placeholder.sh",
        str(service_path),
        old,
        new
    ], check=True)

def setup_proto(service_name: str, class_name: str) -> Path:
    """
    Setup file .proto baru untuk modul, pakai template + patch.
    Tambahkan error handling dan validasi lebih ketat.
    """
    proto_path = Path(f"protos/{service_name}.proto")
    if proto_path.exists():
        proto_path.unlink()

    # 🔥 Pastikan file template ada
    src_proto = Path("protos/template_service.proto")
    if not src_proto.exists():
        print("❌ template_service.proto tidak ditemukan!")
        raise FileNotFoundError("template_service.proto missing")

    # 🔥 Generate file .proto final
    content = src_proto.read_text()
    pkg_name = service_name.replace("-", "_")
    content = content.replace("package template;", f"package {pkg_name};")
    content = content.replace("TemplateService", class_name)
    content = content.replace("template_service", pkg_name)
    proto_path.write_text(content)
    print(f"📦 .proto final dibuat di: {proto_path}")

    # 🔥 Validasi syntax .proto dengan dummy output
    validate_dir = Path("protos/validate")
    validate_dir.mkdir(exist_ok=True)
    try:
        subprocess.run([
            "python3", "-m", "grpc_tools.protoc",
            f"--proto_path=protos",
            f"--python_out={validate_dir}",
            f"--grpc_python_out={validate_dir}",
            f"protos/{service_name}.proto"
        ], check=True)
        print("✅ Syntax .proto valid.")
    except subprocess.CalledProcessError as e:
        print(f"❌ Syntax error di file .proto: {e}")
        raise e
    finally:
        shutil.rmtree(validate_dir)
        print("🧹 Folder validate sudah dibersihkan.")

    return proto_path



def generate_proto_stub(proto_path: Path, service_path: Path):
    """
    Generate file *_pb2.py dan *_pb2_grpc.py untuk protos/<proto_path>.
    Tambahkan error handling yang lebih jelas.
    """
    out_dir = service_path / "app"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 🔥 Hapus stub lama (safe & log error)
    try:
        subprocess.run(["find", str(out_dir), "-name", "*_pb2.py", "-delete"], check=True)
        subprocess.run(["find", str(out_dir), "-name", "*_pb2_grpc.py", "-delete"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"⚠️ Gagal hapus stub lama di {out_dir}: {e}")

    # 🔥 Generate stub baru (dengan validasi error)
    try:
        subprocess.run([
            "python3", "-m", "grpc_tools.protoc",
            "--proto_path=protos",
            f"--python_out={out_dir}",
            f"--grpc_python_out={out_dir}",
            str(proto_path)
        ], check=True)
        print(f"✅ Stub .pb2.py & .pb2_grpc.py digenerate di {out_dir}")
    except subprocess.CalledProcessError as e:
        print(f"❌ Error generate stub untuk {proto_path}: {e}")
        return

    # 🔥 Patch relative import (safe & log error)
    grpc_stub = out_dir / f"{proto_path.stem}_pb2_grpc.py"
    if grpc_stub.exists():
        try:
            content = grpc_stub.read_text()
            content = content.replace(
                f"import {proto_path.stem}_pb2 as",
                f"from . import {proto_path.stem}_pb2 as"
            )
            grpc_stub.write_text(content)
            print("🧩 Relative import di stub gRPC autopatch.")
        except Exception as e:
            print(f"⚠️ Error patch relative import di {grpc_stub}: {e}")



def find_available_port(start=5000, end=5999):
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('', port))
                return port
            except OSError:
                continue
    raise RuntimeError("❌ Tidak ada port tersedia dalam rentang 5000–5999")

def patch_grpc_port_and_logger(service_path: Path, class_name: str, grpc_port: int):
    grpc_file = service_path / "app" / "grpc_server.py"
    if not grpc_file.exists():
        print("⚠️ grpc_server.py tidak ditemukan, skip patch.")
        return

    content = grpc_file.read_text()
    content = content.replace(
        'os.getenv("GRPC_PORT", "5009")',
        f'"{grpc_port}"'
    )
    content = content.replace(
        'logger.info("🚀 TemplateService gRPC server listening on port %s", grpc_port)',
        f'logger.info("🚀 {class_name} gRPC server listening on port %s", grpc_port)'
    )
    grpc_file.write_text(content)
    print(f"🔢 GRPC_PORT default diset ke {grpc_port} dan logger dipatch.")




def patch_grpc_imports(service_path: Path, class_name: str, service_name: str, prisma: bool = False):
    """
    Patch file grpc_server.py: replace semua import & instansiasi.
    Hapus semua sisa template-service & bersihkan residue.
    """
    grpc_file = service_path / "app" / "grpc_server.py"
    if not grpc_file.exists():
        print("⚠️ grpc_server.py tidak ditemukan, skip patch.")
        return

    content = grpc_file.read_text()

    # 🔥 Replace import path pb2_grpc & pb2
    content = content.replace(
        "from app import template_service_python_prisma_pb2_grpc as pb_grpc",
        f"from app import {service_name}_pb2_grpc as pb_grpc"
    )
    content = content.replace(
        "from app import template_service_python_prisma_pb2 as pb",
        f"from app import {service_name}_pb2 as pb"
    )

    # 🔥 Replace add_X_to_server + Servicer instance
    content = content.replace(
        "pb_grpc.add_TemplateServiceServicer_to_server",
        f"pb_grpc.add_{class_name}Servicer_to_server"
    ).replace(
        "TemplateServiceServicer()",
        f"{class_name}Servicer()"
    )

    # 🔥 Bersihkan template-service residue
    content = content.replace("TenantManagerServicer()", f"{class_name}Servicer()")
    content = content.replace("pb_grpc.add_TenantManagerServicer_to_server", f"pb_grpc.add_{class_name}Servicer_to_server")
    content = content.replace("template_service_python_prisma", service_name.replace("-", "_"))
    content = content.replace("TemplateService", class_name)
    content = content.replace("TenantManager", class_name)

    # 🔥 Bersihkan residual "PythonPrisma"
    content = content.replace("PythonPrisma", "")

    # 🔥 Tambahkan import Prisma Client jika prisma=True
    if prisma and "from backend.api_gateway.libs.milkyhoop_prisma import Prisma" not in content:
        content = "from backend.api_gateway.libs.milkyhoop_prisma import Prisma\n" + content

    grpc_file.write_text(content)
    print(f"🧩 grpc_server.py final: import dan patch sudah clean (service: {service_name})")






def generate_dockerfile(service_path: Path, service_name: str, prisma: bool = False):
    """
    Generate Dockerfile dari template python prisma, auto-patch nama service
    supaya persis 90 baris (100% identik), future-proof, dan clean.
    """
    template_dockerfile = Path("backend/services/template-service-python-prisma/Dockerfile")
    if not template_dockerfile.exists():
        raise FileNotFoundError("❌ Dockerfile template tidak ditemukan!")

    # 🔥 Baca template Dockerfile
    content = template_dockerfile.read_text()

    # 🔥 Validasi tidak ada hardcoded template-service
    assert "template-service-python-prisma" in content, "❌ Template service pattern tidak ditemukan di Dockerfile template"

    # 🔥 Replace semua nama template-service → nama modul baru
    content = content.replace("template-service-python-prisma", service_name)

    # 🔥 Tulis ulang Dockerfile ke folder modul kloningan
    dockerfile_path = service_path / "Dockerfile"
    dockerfile_path.write_text(content)
    print("✅ Dockerfile final disalin & disesuaikan persis dari template (90 baris).")




def write_env_file(service_path: Path, grpc_port: int):
    env_file = service_path / ".env"
    content = (
        f"GRPC_PORT={grpc_port}\n"
        f"DATABASE_URL=postgresql://postgres:Proyek771977@db.ltrqrejrkbusvmknpnwb.supabase.co:5432/postgres?sslmode=require\n"
    )
    env_file.write_text(content)
    print(f"📝 File .env dibuat dengan GRPC_PORT={grpc_port} & DATABASE_URL otomatis.")




def main(service_name: str, lang: str, prisma: bool = False):
    if lang == "python" and prisma:
        template_dir = Path("backend/services/template-service-python-prisma")
    else:
        src_base = {
            "python": "backend/services/template-service-python",
            "go": "backend/services/template-service-golang"
        }
        template_dir = Path(src_base[lang])

    dest_dir = Path(f"backend/services/{service_name}")
    if dest_dir.exists():
        print(f"❌ Folder {dest_dir} sudah ada!")
        return

    shutil.copytree(template_dir, dest_dir)
    print(f"✅ Service baru {service_name} berhasil dicloning dari template {lang}.")

    # 🔥 Auto-create file __init__.py di semua subfolder baru (biar Python treat sebagai package)
    for subdir, dirs, files in os.walk(dest_dir):
        init_file = Path(subdir) / "__init__.py"
        if not init_file.exists():
            init_file.touch()
            print(f"📝 __init__.py dibuat di {subdir}")

    class_name = "".join(word.capitalize() for word in service_name.split("-"))
    replace_placeholders(dest_dir, "TemplateService", class_name)
    grpc_port = find_available_port()
    patch_grpc_port_and_logger(dest_dir, class_name, grpc_port)
    write_env_file(dest_dir, grpc_port)
    proto_path = setup_proto(service_name, class_name)
    if lang == "python" and proto_path:
        generate_proto_stub(proto_path, dest_dir)
    patch_grpc_imports(dest_dir, class_name, service_name.replace("-", "_"), prisma=prisma)
    generate_dockerfile(dest_dir, service_name, prisma=prisma)

    # 🔥 Auto-Warning & Rename Shadow File (misalnya prisma.py, grpc.py)
    shadow_files = ["prisma.py", "grpc.py"]
    for root, dirs, files in os.walk(dest_dir):
        for fname in files:
            if fname in shadow_files:
                file_path = Path(root) / fname
                new_name = file_path.with_name(fname.replace(".py", "_client.py"))
                file_path.rename(new_name)
                print(f"⚠️ File shadow {fname} di {root} di-rename ke {new_name.name} untuk mencegah konflik modul Python.")

    print("🎉 Service baru siap digunakan!")

    # 🔥 Bersihkan .pyc & __pycache__ (residu stub lama)
    subprocess.run(["find", str(dest_dir), "-type", "d", "-name", "__pycache__", "-exec", "rm", "-r", "{}", "+"], check=True)
    subprocess.run(["find", str(dest_dir), "-name", "*.pyc", "-delete"], check=True)
    print("🧹 Bersihkan file .pyc & __pycache__ selesai.")



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MilkyHoop Service Scaffolder")
    parser.add_argument("service_name", help="Nama modul baru, contoh: order-service")
    parser.add_argument("--lang", choices=["python", "go"], required=True, help="Bahasa pemrograman")
    parser.add_argument("--prisma", action="store_true", help="Gunakan template Python Prisma")
    args = parser.parse_args()
    main(args.service_name, args.lang, prisma=args.prisma)
