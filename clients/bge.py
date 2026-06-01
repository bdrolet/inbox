from sentence_transformers import SentenceTransformer

_MODEL_NAME = "BAAI/bge-small-en-v1.5"


def load_model() -> SentenceTransformer:
    return SentenceTransformer(_MODEL_NAME)


def encode(model: SentenceTransformer, text: str) -> list[float]:
    return model.encode(text, normalize_embeddings=True).tolist()
