import grpc
import asyncio
import logging
import json

from milkyhoop_protos import intent_parser_pb2_grpc, intent_parser_pb2

logger = logging.getLogger(__name__)

class IntentParserClient:
    def __init__(self, host: str = "intent_parser", port: int = 5009):
        self.target = f"{host}:{port}"

    async def parse(self, user_id: str, message: str):
        async with grpc.aio.insecure_channel(self.target) as channel:
            stub = intent_parser_pb2_grpc.IntentParserServiceStub(channel)

            request = intent_parser_pb2.IntentParserRequest(
                user_id=user_id,
                input=message
            )

            try:
                response = await stub.DoSomething(request)

                # 🐞 DEBUG: Cetak isi response mentah
                print("=== INTENT PARSER DEBUG ===")
                print("Status:", response.status)
                print("Result:", response.result)

                # ✅ Coba parse JSON, fallback jika gagal
                try:
                    entities = json.loads(response.result)
                except (json.JSONDecodeError, TypeError):
                    entities = {}

                return {
                    "intent": response.status,
                    "entities": entities
                }

            except grpc.aio.AioRpcError as e:
                logger.error(f"[IntentParser] gRPC error: {e.code()} - {e.details()}")
                return {"intent": "unknown", "entities": {}}
