import requests
from typing import List

OLLAMA_URL = "http://127.0.0.1:11434/api/embed"
OLLAMA_MODEL = "nomic-embed-text:latest"


def get_embeddings(textos: List[str]) -> List[List[float]]:
    """Genera embeddings para una lista de textos vía Ollama."""
    embeddings = []
    for texto in textos:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "input": texto},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        # Ollama devuelve {"embeddings": [[...]]} o {"embedding": [...]}
        if "embeddings" in data:
            embeddings.append(data["embeddings"][0])
        else:
            embeddings.append(data["embedding"])
    return embeddings


def get_embedding(texto: str) -> List[float]:
    return get_embeddings([texto])[0]
