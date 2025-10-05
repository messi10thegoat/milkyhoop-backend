import sys
import json
import os
from google.protobuf import json_format
from internal.proto.flow import flow_pb2

def convert_json_to_pb(input_json_path, output_pb_path):
    if not os.path.exists(input_json_path):
        print(f"❌ File tidak ditemukan: {input_json_path}")
        sys.exit(1)

    with open(input_json_path, "r", encoding="utf-8") as f:
        json_data = json.load(f)

    try:
        flow_definition = flow_pb2.Flow()
        json_format.ParseDict(json_data, flow_definition)
    except Exception as e:
        print(f"❌ Gagal mengkonversi JSON ke Protobuf: {e}")
        sys.exit(1)

    try:
        with open(output_pb_path, "wb") as f:
            f.write(flow_definition.SerializeToString())
        print(f"✅ Berhasil dikompilasi ke: {output_pb_path}")
    except Exception as e:
        print(f"❌ Gagal menyimpan file protobuf: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("⚠️  Usage: python json_to_pb.py <input_json> <output_pb>")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    convert_json_to_pb(input_path, output_path)
