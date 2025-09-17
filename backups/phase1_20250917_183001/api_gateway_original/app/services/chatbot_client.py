import os
import sys
import asyncio
import logging
from typing import Optional

import grpc
from google.protobuf import symbol_database

# Tambahan path agar import stub tidak error
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../libs/milkyhoop_protos")))

import chatbot_service_pb2_grpc
import chatbot_service_pb2  # import tetap untuk DESCRIPTOR loading

logger = logging.getLogger(__name__)

# Dapatkan symbol database default protobuf
_sym_db = symbol_database.Default()

# Ambil class message dari symbol database, bukan dari chatbot_service_pb2 langsung
ChatbotServiceRequest = _sym_db.GetSymbol("chatbot_service.ChatbotServiceRequest")
ChatbotServiceResponse = _sym_db.GetSymbol("chatbot_service.ChatbotServiceResponse")

class ChatbotClient:
    def __init__(self, host: str = "chatbot_service", port: int = 5002, timeout: float = 5.0):
        self.target = f"{host}:{port}"
        self.timeout = timeout
        self.channel: Optional[grpc.aio.Channel] = None
        self.stub: Optional[chatbot_service_pb2_grpc.ChatbotServiceStub] = None

    async def connect(self):
        if self.channel is None:
            self.channel = grpc.aio.insecure_channel(self.target)
            self.stub = chatbot_service_pb2_grpc.ChatbotServiceStub(self.channel)
            logger.info(f"Connected to Chatbot gRPC service at {self.target}")

    async def close(self):
        if self.channel:
            await self.channel.close()
            self.channel = None
            self.stub = None
            logger.info("Chatbot gRPC channel closed")

    async def send_message(self, user_id: str, session_id: str, message: str, tenant_id: str) -> str:

        await self.connect()
        # Buat instance message via symbol database
        request = ChatbotServiceRequest(
            user_id=user_id,
            input=message,
            tenant_id=tenant_id,
        )
        try:
            response = await asyncio.wait_for(
                self.stub.DoSomething(request),
                timeout=self.timeout
            )
            # response sudah instance ChatbotServiceResponse
            return response.result
        except grpc.RpcError as e:
            import traceback
            print("=== GRPC ERROR in chatbot_client ===")
            traceback.print_exc()
            logger.error(f"gRPC error: {e.code()} - {e.details()}")
            raise
        except asyncio.TimeoutError:
            logger.error("Timeout while calling Chatbot service")
            raise
