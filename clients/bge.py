_MODEL_NAME = "BAAI/bge-small-en-v1.5"


def load_model():
    # Deferred import — keeps module-level import of clients.bge fast so the
    # Cloud Run health check passes before PyTorch/sentence-transformers load.
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(_MODEL_NAME)


def encode(model, text: str) -> list[float]:
    return model.encode(text, normalize_embeddings=True).tolist()
