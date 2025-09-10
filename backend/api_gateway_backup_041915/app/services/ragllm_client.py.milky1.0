import os
import sys
import asyncio
import logging
from typing import Optional
import grpc
from google.protobuf import symbol_database

# ✅ Tambahan path agar import stub tidak error
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../libs/milkyhoop_protos")))
import ragllm_service_pb2 as ragllm__service__pb2
import ragllm_service_pb2_grpc

logger = logging.getLogger(__name__)
sym_db = symbol_database.Default()

# Ambil class message dari symbol database
GenerateAnswerRequest = ragllm__service__pb2.GenerateAnswerRequest
GenerateAnswerResponse = ragllm__service__pb2.GenerateAnswerResponse

class RagLLMClient:
    def __init__(self, host: str = "ragllm_service", port: int = 5000, timeout: float = 60.0):
        self.target = f"{host}:{port}"
        self.timeout = timeout
        self.channel: Optional[grpc.aio.Channel] = None
        self.stub: Optional[ragllm_service_pb2_grpc.RagLlmServiceStub] = None

    async def connect(self):
        if self.channel is None:
            self.channel = grpc.aio.insecure_channel(self.target)
            self.stub = ragllm_service_pb2_grpc.RagLlmServiceStub(self.channel)
            logger.info(f"Connected to RagLLM gRPC service at {self.target}")

    async def close(self):
        if self.channel:
            await self.channel.close()
            self.channel = None
            self.stub = None
            logger.info("RagLLM gRPC channel closed")

    

    async def generate_answer(self, user_id: str, session_id: str, tenant_id: str, message: str) -> str:
        await self.connect()
        
        # Set mode based on user_id
        mode = "customer_service" if user_id == "customer" else "conversation"
        
        request = GenerateAnswerRequest(
            question=message,
            tenant_id=tenant_id,
            mode=mode  # ✅ Use mode field instead of user_id
        )
        try:
            response = await asyncio.wait_for(
                self.stub.GenerateAnswer(request),
                timeout=60.0
            )
            return response.answer
        except grpc.RpcError as e:
            logger.error(f"gRPC error: {e.code()} - {e.details()}")
            raise
        except asyncio.TimeoutError:
            logger.error("Timeout while calling RagLLM service")
            raise