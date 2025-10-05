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
    proto_path = Path(f"protos/{service_name}.proto")
    if proto_path.exists():
        proto_path.unlink()
    src_proto = Path("protos/template_service.proto")
    if not src_proto.exists():
        print("❌ template_service.proto tidak ditemukan!")
        raise FileNotFoundError("template_service.proto missing")
    content = src_proto.read_text()
    pkg_name = service_name.replace("-", "_")
    content = content.replace("package template;", f"package {pkg_name};")
    content = content.replace("TemplateService", class_name)
    content = content.replace("template_service", pkg_name)
    proto_path.write_text(content)
    print(f"📦 .proto final dibuat di: {proto_path}")
    return proto_path

def generate_proto_stub(proto_path: Path, service_path: Path):
    out_dir = service_path / "app"
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["python3", "-m", "grpc_tools.protoc",
        "--proto_path=protos",
        f"--python_out={out_dir}",
        f"--grpc_python_out={out_dir}",
        str(proto_path)
    ], check=True)
    grpc_stub = out_dir / f"{proto_path.stem}_pb2_grpc.py"
    if grpc_stub.exists():
        content = grpc_stub.read_text()
        content = content.replace(
            f"import {proto_path.stem}_pb2 as",
            f"from . import {proto_path.stem}_pb2 as"
        )
        grpc_stub.write_text(content)
        print("🧩 Relative import di stub gRPC autopatch.")

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

def main():
    parser = argparse.ArgumentParser(description="MilkyHoop API Gateway Scaffolder")
    parser.add_argument("service_name", help="Nama modul, misalnya: api_gateway")
    parser.add_argument("--lang", choices=["python"], required=True, help="Bahasa pemrograman")
    parser.add_argument("--prisma", action="store_true", help="Gunakan Prisma integration")
    args = parser.parse_args()

    service_name = args.service_name
    dest_dir = Path("backend/api_gateway")
    if dest_dir.exists():
        print(f"❌ Folder {dest_dir} sudah ada!")
        return

    template_dir = Path("backend/services/template-service-python-prisma") if args.prisma else Path("backend/services/template-service-python")
    shutil.copytree(template_dir, dest_dir)
    print(f"✅ API Gateway {service_name} berhasil dicloning dari template.")

    for subdir, dirs, files in os.walk(dest_dir):
        init_file = Path(subdir) / "__init__.py"
        if not init_file.exists():
            init_file.touch()
            print(f"📝 __init__.py dibuat di {subdir}")

    class_name = "".join(word.capitalize() for word in service_name.split("-"))
    replace_placeholders(dest_dir, "TemplateService", class_name)
    grpc_port = find_available_port()
    patch_grpc_port_and_logger(dest_dir, class_name, grpc_port)
    proto_path = setup_proto(service_name, class_name)
    generate_proto_stub(proto_path, dest_dir)

    print("🎉 API Gateway siap digunakan!")
    subprocess.run(["find", str(dest_dir), "-type", "d", "-name", "__pycache__", "-exec", "rm", "-r", "{}", "+"], check=True)
    subprocess.run(["find", str(dest_dir), "-name", "*.pyc", "-delete"], check=True)
    print("🧹 Bersihkan file .pyc & __pycache__ selesai.")

if __name__ == "__main__":
    main()
