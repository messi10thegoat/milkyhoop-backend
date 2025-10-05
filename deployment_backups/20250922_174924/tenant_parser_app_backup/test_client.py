import grpc
import tenant_parser_pb2 as pb
import tenant_parser_pb2_grpc as pb_grpc

GRPC_HOST = "localhost"
GRPC_PORT = 5012

def run_test(text):
    with grpc.insecure_channel(f"{GRPC_HOST}:{GRPC_PORT}") as channel:
        stub = pb_grpc.IntentParserServiceStub(channel)
        request = pb.IntentParserRequest(user_id="test-user", input=text)
        response = stub.DoSomething(request)
        print(f"Input: {text}")
        print("Status:", response.status)
        print("Result:", response.result)

if __name__ == "__main__":
    test_texts = [
        "Saya kecewa sekali dengan cake dari Tart Top. Tidak segar, tidak lezat, dan teksturnya kurang memuaskan.",
        "Halo, saya mau pesan roti gandum 2 loyang dan brownies cokelat 1 kotak.",
        "Tolong cek status pengiriman order 123456 untuk Anton di Bandung."
    ]

    for text in test_texts:
        print("\n=== Test ===")
        run_test(text)
