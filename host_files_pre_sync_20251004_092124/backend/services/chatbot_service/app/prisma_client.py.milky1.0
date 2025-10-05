from milkyhoop_prisma import Prisma


# ✅ Global Prisma client
prisma = Prisma()

# ✅ Safe connect helper
async def connect_prisma():
    if not prisma.is_connected():
        print("🔌 Connecting to Prisma...")
        await prisma.connect()
        print("✅ Prisma connected.")

# ✅ Safe disconnect helper
async def disconnect_prisma():
    if prisma.is_connected():
        await prisma.disconnect()
        print("⛔ Prisma disconnected.")
