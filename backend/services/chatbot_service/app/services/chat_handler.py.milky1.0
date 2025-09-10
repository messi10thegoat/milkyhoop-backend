import json
import logging
import grpc

from milkyhoop_protos import intent_parser_pb2, intent_parser_pb2_grpc
from .faq_client import fetch_faqs_for_tenant

logger = logging.getLogger(__name__)


async def call_intent_parser(user_id: str, input_text: str) -> dict:
    """
    Panggil intent-parser gRPC dan parse JSON hasilnya.
    """
    async with grpc.aio.insecure_channel("intent_parser:5009") as channel:
        stub = intent_parser_pb2_grpc.IntentParserServiceStub(channel)
        request = intent_parser_pb2.IntentParserRequest(user_id=user_id, input=input_text)
        response = await stub.DoSomething(request)
        logger.info(f"[INTENT RAW] {response.result}")
        return json.loads(response.result)


async def handle_chat_message(user_id: str, tenant_id: str, user_message: str) -> str:
    """
    Handler utama chat: deteksi intent → FAQ → fallback ke intent parser.
    """

    logger.info(f"📥 New message from {user_id} ({tenant_id}): {user_message}")

    # 1️⃣ Cek apakah pertanyaan cocok dengan FAQ
    try:
        faq_list = await fetch_faqs_for_tenant(tenant_id)
        for faq in faq_list:
            if faq["question"].strip().lower() in user_message.strip().lower():
                logger.info(f"💡 FAQ match found: {faq['question']}")
                return faq["answer"]
    except Exception as e:
        logger.warning(f"⚠️ Gagal ambil FAQ: {e}")

    # 2️⃣ Jika tidak cocok, teruskan ke intent parser
    try:
        intent_data = await call_intent_parser(user_id, user_message)
        logger.info(f"[INTENT PARSED] {intent_data}")
        return f"🤖 Intent terdeteksi: {intent_data.get('intent')}"
    except Exception as e:
        logger.exception("🔥 Intent parser error")
        return "Maaf, terjadi kesalahan saat memproses pesan kamu."
