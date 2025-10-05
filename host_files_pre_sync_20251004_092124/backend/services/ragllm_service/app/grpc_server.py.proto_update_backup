import asyncio
import signal
import logging
import os
import grpc
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.instrumentation.grpc import GrpcInstrumentorServer
from app.config import settings
from app import ragllm_service_pb2_grpc as pb_grpc
from app import ragllm_service_pb2 as pb
from app.services.llm_pipeline import generate_embedding, generate_answer

# ✅ Logging config
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(settings.SERVICE_NAME)

# ✅ Setup OpenTelemetry tracing
trace.set_tracer_provider(TracerProvider())
tracer_provider = trace.get_tracer_provider()
otlp_exporter = OTLPSpanExporter(endpoint="http://otel-collector:4317", insecure=True)
span_processor = BatchSpanProcessor(otlp_exporter)
tracer_provider.add_span_processor(span_processor)
GrpcInstrumentorServer().instrument()

# ✅ IMPLEMENTASI SERVICER SESUAI STUB
class RagLlmServiceServicer(pb_grpc.RagLlmServiceServicer):
    async def GenerateEmbedding(self, request, context):
        logger.info("📥 GenerateEmbedding request received")
        embedding = await generate_embedding(request.text)
        return pb.EmbeddingResponse(embedding=embedding)

    async def GenerateAnswer(self, request, context):
        logger.info("📥 GenerateAnswer request received")
        try:
            from app.services.llm_pipeline import generate_conversational_response, detect_action_trigger
            
            # Customer service mode detection - CHECK USER_ID FIRST
            if hasattr(request, 'user_id') and request.user_id == "customer":
                mode = "customer_service"
                logger.info(f"🎯 Customer service mode detected for user_id: {request.user_id}")
            elif not request.mode:
                # Detect mode if not provided (existing logic)
                is_action = await detect_action_trigger(request.question)
                mode = "execution" if is_action else "conversation"
            else:
                mode = request.mode
            
            # Route based on mode
            if mode == "conversation":
                answer = await generate_conversational_response(
                    query=request.question,
                    context=f"tenant: {request.tenant_id}",
                    mode="conversation"
                )
            elif mode == "customer_service":
                answer = await generate_conversational_response(
                    query=request.question,
                    context=f"tenant: {request.tenant_id}",
                    mode="customer_service"
                )
            elif mode == "execution":
                answer = await generate_conversational_response(
                    query=request.question,
                    context=f"tenant: {request.tenant_id}",
                    mode="execution"
                )
            else:
                # Default existing behavior
                answer = await generate_answer(request.question, request.tenant_id)
            
            return pb.GenerateAnswerResponse(
                answer=answer,
                mode=mode
            )
        except Exception as e:
            logger.error(f"Error in GenerateAnswer: {e}")
            return pb.GenerateAnswerResponse(
                answer="Maaf, terjadi kesalahan.",
                mode="error"
            )

# ✅ SETUP SERVER
async def serve() -> None:
    server = aio.server()
    pb_grpc.add_RagLlmServiceServicer_to_server(RagLlmServiceServicer(), server)
    
    # ✅ Health check
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set('', health_pb2.HealthCheckResponse.SERVING)
    
    listen_addr = f"[::]:{settings.GRPC_PORT}"
    server.add_insecure_port(listen_addr)
    logger.info(f"🚀 ragllm_service gRPC server listening on port {settings.GRPC_PORT}")
    
    stop_event = asyncio.Event()
    
    def handle_shutdown(*_):
        logger.info("🛑 Shutdown signal received. Cleaning up...")
        stop_event.set()
    
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    
    try:
        await server.start()
        await stop_event.wait()
    finally:
        logger.info("🧹 Shutting down gRPC server...")
        await server.stop(5)
        logger.info("✅ gRPC server shut down cleanly.")

if __name__ == "__main__":
    asyncio.run(serve())