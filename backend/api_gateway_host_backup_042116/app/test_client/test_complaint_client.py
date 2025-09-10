import asyncio
from backend.api_gateway.app.services.complaint_client import ComplaintServiceClient

async def test_create_complaint():
    client = ComplaintServiceClient()

    response = await client.create_complaint(
        user_id="user_001",
        message="Saya kecewa dengan produk roti cokelat. Rasanya asam dan basi.",
        product="roti cokelat",
        source="chat",
        emotion="disappointed"
    )

    print("✅ TEST COMPLAINT RESULT")
    print("Status:", response.status)
    print("Complaint ID:", response.complaint_id)
    print("Message:", response.message)

if __name__ == "__main__":
    asyncio.run(test_create_complaint())
