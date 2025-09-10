import faiss
import numpy as np
import threading
import os

class FaissIndexSingleton:
    """
    Singleton untuk FAISS index dan doc_id_map (persistent, thread-safe).
    """
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        if not hasattr(self, "initialized"):
            # 1536 = dimensi embedding (ubah sesuai model)
            self.index = faiss.IndexFlatIP(1536)
            self.doc_id_map = {}
            self.initialized = True

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = FaissIndexSingleton()
        return cls._instance

def save_faiss_index(index_path="faiss.index", map_path="doc_id_map.npy"):
    """
    Save FAISS index dan doc_id_map ke file.
    """
    instance = FaissIndexSingleton.get_instance()
    faiss.write_index(instance.index, index_path)
    np.save(map_path, instance.doc_id_map)
    print(f"✅ FAISS index saved to {index_path} & doc_id_map to {map_path}")

def load_faiss_index(index_path="faiss.index", map_path="doc_id_map.npy"):
    """
    Load FAISS index dan doc_id_map dari file (jika ada).
    """
    instance = FaissIndexSingleton.get_instance()
    if os.path.exists(index_path):
        instance.index = faiss.read_index(index_path)
        print(f"✅ FAISS index loaded from {index_path}")
    if os.path.exists(map_path):
        instance.doc_id_map = np.load(map_path, allow_pickle=True).item()
        print(f"✅ doc_id_map loaded from {map_path}")

# 🚀 Auto-load saat modul di-import/startup
load_faiss_index()

# =====================
#   PUBLIC API
# =====================

def add_document(doc_id: int, embedding: np.ndarray):
    """
    Tambahkan dokumen baru ke FAISS + doc_id_map.
    Auto-save ke file setiap kali update.
    """
    instance = FaissIndexSingleton.get_instance()
    instance.index.add(np.expand_dims(embedding, axis=0))
    instance.doc_id_map[instance.index.ntotal - 1] = doc_id
    save_faiss_index()

def search_documents(query_embedding: np.ndarray, top_k: int = 5):
    """
    Cari dokumen terdekat dari FAISS.
    Return: list dict {doc_id, score}
    """
    instance = FaissIndexSingleton.get_instance()
    D, I = instance.index.search(np.expand_dims(query_embedding, axis=0), top_k)
    results = []
    for score, idx in zip(D[0], I[0]):
        if idx == -1:
            continue
        results.append({
            "doc_id": instance.doc_id_map.get(idx),
            "score": float(score)
        })
    return results

def print_index_status():
    """
    Debug: print status FAISS index.
    """
    instance = FaissIndexSingleton.get_instance()
    print("🧪 FAISS debug → index total:", instance.index.ntotal)
    print("🧪 FAISS debug → doc_id_map:", instance.doc_id_map)
