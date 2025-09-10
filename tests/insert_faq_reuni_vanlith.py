import asyncio
import grpc
from backend.services.ragcrud_service.app import ragcrud_service_pb2 as crud_pb
from backend.services.ragcrud_service.app import ragcrud_service_pb2_grpc as crud_pb_grpc

async def insert_faq():
    faq_content = """�� FAQ Buku Reuni 25 Tahun Van Lith Angkatan 7
Q: Apa itu buku reuni ini?
A: Buku ini adalah bunga rampai tulisan dari para alumni Van Lith Angkatan 7, plus satu tulisan istimewa dari pendamping. Tujuannya untuk menjadi ruang refleksi dan berbagi di momen reuni perak ini.

Q: Berapa halaman dan apa isinya?
A: Total 231 halaman. Isinya beragam: ada yang menyentuh, ada yang lucu, ada yang penuh nostalgia. Semua menghadirkan suara hati dan perjalanan hidup masing-masing alumni.

Q: Siapa saja yang menulis?
A: Sebagian ditunjuk, sebagian lain menulis dengan sukarela. Semuanya alumni Angkatan 7, plus satu pendamping. Kami percaya setiap orang membawa cerita yang layak dibaca.

Q: Untuk apa buku ini dibuat?
A: Untuk mengenang perjalanan 25 tahun, merayakan kebersamaan, dan menyambut masa depan dengan semangat baru. Ini juga jadi rumah bersama: tempat setiap orang merasa diingat dan diterima.

Q: Apakah ini hanya sekadar nostalgia?
A: Tidak hanya nostalgia! Buku ini menegaskan bahwa meskipun jalan hidup kita berbeda, ada benang merah yang menyatukan kita sebagai satu angkatan.

Q: Siapa yang membiayai pembuatan buku ini?
A: Buku ini hadir berkat kebaikan beberapa donatur, terutama keluarga Arif Ocha di Bogor.

Q: Apakah buku ini dijual?
A: Tidak. Buku ini dibagikan gratis untuk teman-teman alumni yang hadir di reuni 10-11 Mei di Depok.

Q: Apakah jumlahnya banyak?
A: Tidak. Buku ini dicetak secara terbatas, sebagai kenangan istimewa untuk yang hadir.

Q: Bagaimana saya bisa mendapatkannya kalau tidak hadir?
A: Karena buku ini gratis dan terbatas, kami utamakan untuk alumni yang hadir. Tapi semoga semangat dan isi buku ini tetap sampai ke semua hati teman-teman.
"""

    async with grpc.aio.insecure_channel("localhost:5001") as channel:
        stub = crud_pb_grpc.RagCrudServiceStub(channel)
        request = crud_pb.CreateRagDocumentRequest(
            tenant_id="tenant_001",
            title="FAQ Buku Reuni 25 Tahun Van Lith Angkatan 7",
            content=faq_content
        )
        response = await stub.CreateRagDocument(request)
        print("✅ FAQ Created:", response)

if __name__ == "__main__":
    asyncio.run(insert_faq())
