import asyncio
from backend.api_gateway.app.services.chatbot_client import ChatbotClient

async def test_chatbot_client():
    client = ChatbotClient(host="chatbot_service", port=5002)  # ganti port jadi 5002 sesuai server
    reply = await client.send_message(user_id="test_user", session_id="sess_1", message="Halo, chatbot!")
    print(f"Response from chatbot: {reply}")
    await client.close()

if __name__ == "__main__":
    asyncio.run(test_chatbot_client())
