import os
import requests
import chromadb
from typing import List, Optional, Dict
from embeddings import get_embedding, get_embeddings
from extractors import extraer_paginas, crear_chunks_con_paginas

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

CHROMA_PATH = "chroma_db"

SIMILARITY_THRESHOLD = 0.75  # 🔥 clave

_chroma_client = None


def get_chroma_client():
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
    return _chroma_client


def coleccion_usuario(usuario_id: int, proyecto_id: Optional[int] = None):
    nombre = f"usuario_{usuario_id}"
    if proyecto_id:
        nombre = f"usuario_{usuario_id}_proyecto_{proyecto_id}"
    client = get_chroma_client()
    return client.get_or_create_collection(name=nombre)


def indexar_documento(
    ruta: str,
    documento_id: int,
    usuario_id: int,
    proyecto_id: int,
    nombre_original: str = "",
) -> int:
    paginas = extraer_paginas(ruta)
    chunks = crear_chunks_con_paginas(paginas)
    if not chunks:
        return 0

    col = coleccion_usuario(usuario_id, proyecto_id)

    textos = [c["texto"] for c in chunks]
    embeddings = get_embeddings(textos)

    ids = [f"doc{documento_id}_chunk{i}" for i in range(len(chunks))]

    metadatas = [
        {
            "documento_id": documento_id,
            "chunk_index": i,
            "pagina": c["pagina"],
            "nombre_original": nombre_original or str(documento_id),
        }
        for i, c in enumerate(chunks)
    ]

    col.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=textos,
        metadatas=metadatas,
    )

    return len(chunks)


def buscar(
    consulta: str,
    usuario_id: int,
    proyecto_id: Optional[int] = None,
    n_resultados: int = 5,
) -> List[Dict]:

    col = coleccion_usuario(usuario_id, proyecto_id)

    if col.count() == 0:
        return []

    embedding = get_embedding(consulta)

    resultado = col.query(
        query_embeddings=[embedding],
        n_results=n_resultados,
        include=["documents", "metadatas", "distances"],  # 🔥 importante
    )

    if not resultado["documents"]:
        return []

    resultados = []

    for doc, meta, dist in zip(
        resultado["documents"][0],
        resultado["metadatas"][0],
        resultado["distances"][0],
    ):
        similarity = 1 - dist  # depende del metric

        if similarity < SIMILARITY_THRESHOLD:
            continue

        resultados.append({
            "texto": doc,
            "documento_id": meta.get("documento_id", 0),
            "pagina": meta.get("pagina", 1),
            "nombre_original": meta.get("nombre_original", "Documento"),
            "similarity": similarity
        })

    return resultados


def chat_con_contexto(
    consulta: str,
    historial: List[dict],
    usuario_id: int,
    proyecto_id: Optional[int] = None,
) -> Dict:

    fragmentos = buscar(consulta, usuario_id, proyecto_id, n_resultados=6)

    if not fragmentos:
        return {
            "respuesta": "No tengo información sobre eso.",
            "fuentes": [],
        }

    # 🔥 ordenar por relevancia real
    fragmentos = sorted(fragmentos, key=lambda x: x["similarity"], reverse=True)[:4]

    # 🔥 contexto estructurado
    contexto = []
    for i, f in enumerate(fragmentos, 1):
        contexto.append(
            f"[FUENTE {i} | doc:{f['documento_id']} | pag:{f['pagina']}]\n{f['texto']}"
        )

    bloque_contexto = "\n\n".join(contexto)

    sistema = (
        "Responde EXCLUSIVAMENTE usando la información proporcionada.\n"
        "Si la respuesta no está en el contexto, responde exactamente: No tengo información sobre eso.\n"
        "No inventes, no completes, no supongas.\n"
        "No añadas explicaciones externas.\n"
        "No uses conocimiento previo.\n\n"
        f"{bloque_contexto}"
    )

    mensajes = [{"role": "system", "content": sistema}]

    for msg in historial[-6:]:
        mensajes.append({"role": msg["rol"], "content": msg["contenido"]})

    mensajes.append({"role": "user", "content": consulta})

    resp = requests.post(
        GROQ_URL,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": GROQ_MODEL,
            "messages": mensajes,
            "temperature": 0.0,
            "max_tokens": 700,
        },
        timeout=45,
    )

    resp.raise_for_status()

    respuesta = resp.json()["choices"][0]["message"]["content"].strip()

    return {
        "respuesta": respuesta,
        "fuentes": [
            {
                "documento_id": f["documento_id"],
                "pagina": f["pagina"],
                "nombre_original": f["nombre_original"],
            }
            for f in fragmentos
        ],
    }