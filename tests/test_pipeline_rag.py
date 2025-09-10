from backend.services.chatbot_service.app.pipeline_rag import get_answer

if __name__ == "__main__":
    question = "Siapakah yang menjadi donatur buku reuni Van Lith angkatan 7?"
    answer = get_answer(question)
    print("✅ Jawaban:", answer)
