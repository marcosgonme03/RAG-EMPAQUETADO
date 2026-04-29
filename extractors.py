import os
import re
from pathlib import Path
from typing import List, Dict


# ── Extracción con metadata de página ─────────────────────────────────────────

def extraer_paginas(ruta: str) -> List[Dict]:
    """
    Extrae texto página a página.
    Devuelve: [{"pagina": int, "texto": str}, ...]
    """
    ext = Path(ruta).suffix.lower()
    if ext == ".pdf":
        return _paginas_pdf(ruta)
    elif ext in (".docx", ".doc"):
        return _paginas_docx(ruta)
    elif ext in (".txt", ".md", ".markdown"):
        return _paginas_txt(ruta)
    elif ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"):
        return _paginas_imagen(ruta)
    else:
        raise ValueError(f"Formato no soportado: {ext}")


def _paginas_pdf(ruta: str) -> List[Dict]:
    import pdfplumber
    paginas = []
    with pdfplumber.open(ruta) as pdf:
        for i, pagina in enumerate(pdf.pages, start=1):
            t = pagina.extract_text()
            if t and t.strip():
                paginas.append({"pagina": i, "texto": t.strip()})
    return paginas


def _paginas_docx(ruta: str) -> List[Dict]:
    from docx import Document
    doc = Document(ruta)
    parrafos = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    # DOCX no tiene páginas reales; agrupamos en bloques de ~50 párrafos como "página virtual"
    bloque_size = 50
    paginas = []
    for i in range(0, len(parrafos), bloque_size):
        bloque = "\n\n".join(parrafos[i:i + bloque_size])
        paginas.append({"pagina": (i // bloque_size) + 1, "texto": bloque})
    return paginas


def _paginas_txt(ruta: str) -> List[Dict]:
    with open(ruta, "r", encoding="utf-8", errors="ignore") as f:
        texto = f.read()
    # Dividimos en bloques de ~3000 chars para simular páginas
    bloque_size = 3000
    paginas = []
    for i in range(0, len(texto), bloque_size):
        bloque = texto[i:i + bloque_size].strip()
        if bloque:
            paginas.append({"pagina": (i // bloque_size) + 1, "texto": bloque})
    return paginas


def _paginas_imagen(ruta: str) -> List[Dict]:
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(ruta)
        texto = pytesseract.image_to_string(img, lang="spa+eng")
        return [{"pagina": 1, "texto": texto.strip()}] if texto.strip() else []
    except Exception as e:
        raise RuntimeError(f"Error OCR: {e}. ¿Está tesseract instalado?")


# ── Chunking semántico para documentos legales ────────────────────────────────

def crear_chunks_con_paginas(
    paginas: List[Dict],
    tamano: int = 1000,
    solape: int = 200,
) -> List[Dict]:
    """
    Genera chunks conservando la referencia de página.
    Respeta límites de párrafo para no cortar cláusulas legales a la mitad.
    Devuelve: [{"texto": str, "pagina": int}, ...]
    """
    chunks = []
    for pagina_data in paginas:
        pagina_num = pagina_data["pagina"]
        texto = pagina_data["texto"]

        # Dividir por párrafos (doble salto de línea o salto + mayúscula = nuevo artículo)
        parrafos = re.split(r"\n{2,}|\n(?=[A-ZÁÉÍÓÚÑ])", texto)
        parrafos = [p.strip() for p in parrafos if p.strip()]

        buffer = ""
        for parrafo in parrafos:
            if len(buffer) + len(parrafo) + 2 <= tamano:
                buffer = (buffer + "\n\n" + parrafo).strip()
            else:
                if buffer:
                    chunks.append({"texto": buffer, "pagina": pagina_num})
                # Si el párrafo solo ya supera el tamaño, lo partimos con solapamiento
                if len(parrafo) > tamano:
                    inicio = 0
                    while inicio < len(parrafo):
                        fin = inicio + tamano
                        chunks.append({"texto": parrafo[inicio:fin], "pagina": pagina_num})
                        inicio += tamano - solape
                    buffer = ""
                else:
                    buffer = parrafo

        if buffer:
            chunks.append({"texto": buffer, "pagina": pagina_num})

    return chunks


# ── Compat: función legacy para código que aún use extraer_texto ──────────────

def extraer_texto(ruta: str) -> str:
    """Compatibilidad con código antiguo. Usa extraer_paginas internamente."""
    paginas = extraer_paginas(ruta)
    return "\n\n".join(p["texto"] for p in paginas)


def crear_chunks(texto: str, tamano: int = 1000, solape: int = 200) -> List[str]:
    """Compatibilidad con código antiguo."""
    paginas = [{"pagina": 1, "texto": texto}]
    return [c["texto"] for c in crear_chunks_con_paginas(paginas, tamano, solape)]
