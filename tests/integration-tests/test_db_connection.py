import psycopg2

DATABASE_URL = "postgresql://postgres:Proyek771977@db.ltrqrejrkbusvmknpnwb.supabase.co:5432/postgres?sslmode=require"

def test_db_connection():
    """Test koneksi ke PostgreSQL di Supabase."""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        print("✅ Koneksi ke PostgreSQL berhasil!")
        conn.close()
    except Exception as e:
        print("❌ Gagal terhubung ke database:", e)

if __name__ == "__main__":
    test_db_connection()
