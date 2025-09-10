from prisma import Prisma

# Client global
prisma = Prisma()

async def connect_prisma():
    try:
        print("🔌 Connecting to Prisma...")
        await prisma.connect()
        print("✅ Prisma connected.")
    except Exception as e:
        print(f"❌ Prisma connection failed: {e}")
        raise

async def disconnect_prisma():
    try:
        await prisma.disconnect()
        print("⛔ Prisma disconnected.")
    except Exception as e:
        print(f"❌ Error during Prisma disconnect: {e}")
