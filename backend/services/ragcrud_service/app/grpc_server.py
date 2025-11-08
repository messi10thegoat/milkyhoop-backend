from backend.api_gateway.libs.milkyhoop_prisma import Prisma
import asyncio
import signal
import logging
import os
import json

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
from app import ragcrud_service_pb2_grpc as pb_grpc
from app import ragcrud_service_pb2 as pb
from app.prisma_client import prisma, connect_prisma, disconnect_prisma
from app.services import rag_crud

# ✅ Logging config with enhanced debugging
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

# ✅ gRPC handler implementasi with optimized semantic search
class RagCrudServiceServicer(pb_grpc.RagCrudServiceServicer):
    
    async def DoSomething(self, request, context):
        logger.info("📥 DoSomething request received: %s", request.input)
        return pb.Ragcrud_serviceResponse(
            status="ok",
            result=f"Processed input: {request.input}"
        )

    async def CreateRagDocument(self, request, context):
        """Create RAG document with tenant isolation and vector indexing"""
        logger.info(f"📝 CreateRagDocument: tenant={request.tenant_id}, title='{request.title[:50]}...'")
        
        try:
            doc = await rag_crud.create_rag_document(
                tenant_id=request.tenant_id,
                title=request.title,
                content=request.content
            )
            
            return pb.RagDocumentResponse(
                id=doc.id,
                title=doc.title,
                content=doc.content
            )


            
        except Exception as e:
            logger.error(f"❌ CreateRagDocument failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Document creation failed: {str(e)}")
            raise

    async def GetRagDocument(self, request, context):
        """Get single RAG document by ID"""
        logger.info(f"🔍 GetRagDocument: ID={request.id}")
        
        try:
            doc = await rag_crud.get_rag_document(request.id)
            if not doc:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Document with ID {request.id} not found")
                return pb.RagDocumentResponse()
                
            logger.info(f"✅ Document retrieved: ID={doc.id}, title='{doc.title[:30]}...'")
            return pb.RagDocumentResponse(
                id=doc.id,
                title=doc.title,
                content=doc.content
            )
    
            
        except Exception as e:
            logger.error(f"❌ GetRagDocument failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Document retrieval failed: {str(e)}")
            raise

    async def ListRagDocuments(self, request, context):
        """List all RAG documents for a tenant"""
        logger.info(f"📋 ListRagDocuments: tenant={request.tenant_id}")
        
        try:
            docs = await rag_crud.list_rag_documents(request.tenant_id)
            
            doc_responses = [
                pb.RagDocumentResponse(id=d.id, title=d.title, content=d.content)
                for d in docs
            ]
            
            logger.info(f"✅ Listed {len(doc_responses)} documents for tenant {request.tenant_id}")
            return pb.ListRagDocumentsResponse(documents=doc_responses)
            
        except Exception as e:
            logger.error(f"❌ ListRagDocuments failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Document listing failed: {str(e)}")
            return pb.ListRagDocumentsResponse(documents=[])

    async def UpdateRagDocument(self, request, context):
        """Update RAG document by ID with enhanced error handling"""
        logger.info(f"✏️ UpdateRagDocument: ID={request.id}, title='{request.title[:30]}...'")
        
        try:
            doc = await rag_crud.update_rag_document(
                id=request.id,
                title=request.title,
                content=request.content
            )
            
            if not doc:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Document with ID {request.id} not found")
                return pb.RagDocumentResponse()
                
            logger.info(f"✅ Document updated: ID={doc.id}")
            return pb.RagDocumentResponse(
                id=doc.id,
                title=doc.title,
                content=doc.content
            )

            
        except Exception as e:
            logger.error(f"❌ UpdateRagDocument failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Document update failed: {str(e)}")
            raise

    async def UpdateRagDocumentBySearch(self, request, context):
        """Update RAG document by search criteria"""
        logger.info(f"🔍✏️ UpdateRagDocumentBySearch: tenant={request.tenant_id}, search='{request.search_content[:30]}...'")
        
        try:
            doc = await rag_crud.update_rag_document_by_search(
                tenant_id=request.tenant_id,
                search_content=request.search_content,
                new_content=request.new_content
            )
            
            if not doc:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details("No document found matching search criteria")
                return pb.RagDocumentResponse()
                
            logger.info(f"✅ Document updated by search: ID={doc.id}")
            return pb.RagDocumentResponse(
                id=doc.id,
                title=doc.title,
                content=doc.content
            )


            
        except Exception as e:
            logger.error(f"❌ UpdateRagDocumentBySearch failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Document search update failed: {str(e)}")
            raise

    async def DeleteRagDocument(self, request, context):
        """Delete RAG document by ID"""
        logger.info(f"🗑️ DeleteRagDocument: ID={request.id}")
        
        try:
            doc = await rag_crud.delete_rag_document(request.id)
            
            if not doc:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Document with ID {request.id} not found")
                return pb.RagDocumentResponse()
                
            logger.info(f"✅ Document deleted: ID={doc.id}")
            return pb.RagDocumentResponse(
                id=doc.id,
                title=doc.title,
                content=doc.content
            )
            
        except Exception as e:
            logger.error(f"❌ DeleteRagDocument failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Document deletion failed: {str(e)}")
            raise

    async def FuzzySearchDocuments(self, request, context):
        """
        🚀 OPTIMIZED SEMANTIC SEARCH with cached embeddings
        
        Implements 92+ API calls → 0 API calls optimization
        Uses cached embeddings for instant semantic search
        """
        logger.info(f"🧠 FuzzySearchDocuments: tenant='{request.tenant_id}', query='{request.search_content}', threshold={request.similarity_threshold}")
        
        try:
            # Call optimized semantic search function with cached embeddings
            docs = await rag_crud.fuzzy_search_rag_documents(
                tenant_id=request.tenant_id,
                search_content=request.search_content,
                similarity_threshold=request.similarity_threshold or 0.7
            )
            
            # Convert to gRPC response format
            doc_responses = []
            for doc_tuple in docs:
                doc_responses.append(pb.RagDocumentResponse(
                    id=doc_tuple[0].id,
                    title=doc_tuple[0].title,
                    content=doc_tuple[0].content,
                    similarity_score=doc_tuple[1]
                ))
            
            logger.info(f"✅ FuzzySearch completed: {len(doc_responses)} documents found (0 API calls used)")
            return pb.FuzzySearchResponse(documents=doc_responses)
            
        except Exception as e:
            logger.error(f"❌ FuzzySearchDocuments failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Semantic search failed: {str(e)}")
            return pb.FuzzySearchResponse(documents=[])

async def serve() -> None:
    """
    Production-grade gRPC server with proper lifecycle management
    """
    # ✅ Database connection with error handling
    if "DATABASE_URL" in os.environ:
        logger.info("🔌 Connecting to Prisma...")
        try:
            await connect_prisma()
            logger.info("✅ Prisma connected")
        except Exception as e:
            logger.error(f"❌ Prisma connection failed: {e}")
            raise

    # ✅ gRPC server setup
    server = aio.server()
    pb_grpc.add_RagCrudServiceServicer_to_server(RagCrudServiceServicer(), server)

    # ✅ Health check service
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set('', health_pb2.HealthCheckResponse.SERVING)

    # ✅ Server binding
    listen_addr = f"[::]:{settings.GRPC_PORT}"
    server.add_insecure_port(listen_addr)
    logger.info(f"🚀 {settings.SERVICE_NAME} gRPC server listening on port {settings.GRPC_PORT}")

    # ✅ Graceful shutdown handling
    stop_event = asyncio.Event()

    def handle_shutdown(*_):
        logger.info("🛑 Shutdown signal received. Cleaning up...")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    try:
        await server.start()
        logger.info("✅ gRPC server started successfully")
        await stop_event.wait()
    finally:
        logger.info("🧹 Shutting down gRPC server...")
        await server.stop(5)
        
        if "DATABASE_URL" in os.environ:
            logger.info("🧹 Disconnecting Prisma...")
            try:
                await disconnect_prisma()
                logger.info("✅ Prisma disconnected")
            except Exception as e:
                logger.error(f"⚠️ Prisma disconnect error: {e}")
                
        logger.info("✅ gRPC server shut down cleanly.")

if __name__ == "__main__":
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        logger.info("👋 Server stopped by user")
    except Exception as e:
        logger.error(f"💥 Server crashed: {e}")
        raise