"""
Customer Service Orchestrator - Main Entry Point
Handles customer query orchestration through tenant parser, RAG CRUD, and RAG LLM services
"""
import asyncio
import logging
from fastapi import FastAPI
from grpc_server import serve

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def main():
    """Start the customer orchestrator service"""
    logger.info("Starting Customer Service Orchestrator...")
    asyncio.run(serve())

if __name__ == "__main__":
    main()
