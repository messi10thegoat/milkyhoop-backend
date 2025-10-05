import asyncio
import signal
import logging
import os
import numpy as np

import grpc
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

# ✅ OpenTelemetry tracing
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.instrumentation.grpc import GrpcInstrumentorServer

from app.config import settings
from app import ragindex_service_pb2_grpc as pb_grpc
from app import ragindex_service_pb2 as pb
from app.services import indexing_service
from app.services.indexing_service import print_index_status  # ✅ Tambahan debug FAISS

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

# ✅ gRPC handler implementasi
class RagIndexServiceServicer(pb_grpc.RagIndexServiceServicer):
    async def DoSomething(self, request, context):
        logger.info("📥 DoSomething request received: %s", request.input)
        return pb.Ragindex_serviceResponse(
            status="ok",
            result=f"Processed input: {request.input}"
        )

    async def IndexDocument(self, request, context):
        embedding = np.array(request.embedding, dtype=np.float32)
        indexing_service.add_document(request.doc_id, embedding)
        logger.info("✅ Document %s indexed.", request.doc_id)
        print_index_status()  # ✅ Tambahan debug
        return pb.IndexDocumentResponse(status="ok")

    async def SearchDocument(self, request, context):
        embedding = np.array(request.embedding, dtype=np.float32)
        results = indexing_service.search_documents(embedding, top_k=request.top_k)
        response = pb.SearchDocumentResponse()
        for res in results:
            response.results.add(doc_id=res["doc_id"], score=res["score"])
        return response

async def serve() -> None:
    server = aio.server()
    pb_grpc.add_RagIndexServiceServicer_to_server(RagIndexServiceServicer(), server)

    # ✅ Health check
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set('', health_pb2.HealthCheckResponse.SERVING)

    listen_addr = f"[::]:{settings.GRPC_PORT}"
    server.add_insecure_port(listen_addr)
    logger.info(f"🚀 {settings.SERVICE_NAME} gRPC server listening on port {settings.GRPC_PORT}")

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
