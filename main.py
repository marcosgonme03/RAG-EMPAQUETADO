import os
import uuid
import asyncio
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, Request, Form, UploadFile, File, Depends, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

from database import conectar, init_db, ahora
from rag import indexar_documento, buscar, chat_con_contexto

# ── Configuración ──────────────────────────────────────────────────────────────
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs("chroma_db", exist_ok=True)
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)

EXTENSIONES_PERMITIDAS = {".pdf", ".txt", ".docx", ".doc", ".md", ".markdown",
                           ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="cambia-esto-en-produccion-xxx")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ── Helpers ────────────────────────────────────────────────────────────────────
def usuario_actual(request: Request):
    uid = request.session.get("usuario_id")
    if not uid:
        return None
    conn = conectar()
    u = conn.execute("SELECT * FROM usuarios WHERE id = ?", (uid,)).fetchone()
    conn.close()
    return u


def require_user(request: Request):
    u = usuario_actual(request)
    if not u:
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    return u


# ── Auth ───────────────────────────────────────────────────────────────────────
@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
async def login_post(request: Request, email: str = Form(...), password: str = Form(...)):
    conn = conectar()
    u = conn.execute("SELECT * FROM usuarios WHERE email = ?", (email,)).fetchone()
    conn.close()
    if not u or not check_password_hash(u["password_hash"], password):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Email o contraseña incorrectos"})
    request.session["usuario_id"] = u["id"]
    return RedirectResponse("/", status_code=303)


@app.get("/register", response_class=HTMLResponse)
async def register_get(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "error": None})


@app.post("/register", response_class=HTMLResponse)
async def register_post(
    request: Request,
    nombre: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
):
    conn = conectar()
    existe = conn.execute("SELECT id FROM usuarios WHERE email = ?", (email,)).fetchone()
    if existe:
        conn.close()
        return templates.TemplateResponse("register.html", {"request": request, "error": "El email ya está registrado"})
    hash_pw = generate_password_hash(password)
    conn.execute(
        "INSERT INTO usuarios (nombre, email, password_hash, creado_en) VALUES (?, ?, ?, ?)",
        (nombre, email, hash_pw, ahora())
    )
    conn.commit()
    conn.close()
    return RedirectResponse("/login", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ── Dashboard ──────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    u = usuario_actual(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    conn = conectar()
    proyectos = conn.execute(
        "SELECT * FROM proyectos WHERE usuario_id = ? ORDER BY creado_en DESC",
        (u["id"],)
    ).fetchall()
    # Contar docs por proyecto
    stats = {}
    for p in proyectos:
        row = conn.execute(
            "SELECT COUNT(*) as total FROM documentos WHERE proyecto_id = ?",
            (p["id"],)
        ).fetchone()
        stats[p["id"]] = row["total"]
    conn.close()
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "usuario": u,
        "proyectos": proyectos,
        "stats": stats,
        "activo": "dashboard",
    })


@app.post("/proyectos/nuevo")
async def crear_proyecto(
    request: Request,
    nombre: str = Form(...),
    descripcion: str = Form(""),
):
    u = usuario_actual(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    conn = conectar()
    conn.execute(
        "INSERT INTO proyectos (usuario_id, nombre, descripcion, creado_en) VALUES (?, ?, ?, ?)",
        (u["id"], nombre, descripcion, ahora())
    )
    conn.commit()
    conn.close()
    return RedirectResponse("/", status_code=303)


@app.post("/proyectos/{proyecto_id}/eliminar")
async def eliminar_proyecto(proyecto_id: int, request: Request):
    u = usuario_actual(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    conn = conectar()
    conn.execute("DELETE FROM proyectos WHERE id = ? AND usuario_id = ?", (proyecto_id, u["id"]))
    conn.commit()
    conn.close()
    return RedirectResponse("/", status_code=303)


# ── Proyecto (docs + subida) ───────────────────────────────────────────────────
@app.get("/proyectos/{proyecto_id}", response_class=HTMLResponse)
async def ver_proyecto(proyecto_id: int, request: Request):
    u = usuario_actual(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    conn = conectar()
    proyecto = conn.execute(
        "SELECT * FROM proyectos WHERE id = ? AND usuario_id = ?",
        (proyecto_id, u["id"])
    ).fetchone()
    if not proyecto:
        conn.close()
        return RedirectResponse("/", status_code=303)
    docs = conn.execute(
        "SELECT * FROM documentos WHERE proyecto_id = ? ORDER BY creado_en DESC",
        (proyecto_id,)
    ).fetchall()
    conn.close()
    return templates.TemplateResponse("proyecto.html", {
        "request": request,
        "usuario": u,
        "proyecto": proyecto,
        "documentos": docs,
        "activo": "proyecto",
        "mensaje": request.session.pop("mensaje", None),
        "error": request.session.pop("error", None),
    })


@app.post("/proyectos/{proyecto_id}/subir")
async def subir_documento(
    proyecto_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    archivo: UploadFile = File(...),
):
    u = usuario_actual(request)
    if not u:
        return RedirectResponse("/login", status_code=303)

    ext = Path(archivo.filename).suffix.lower()
    if ext not in EXTENSIONES_PERMITIDAS:
        request.session["error"] = f"Formato no soportado: {ext}"
        return RedirectResponse(f"/proyectos/{proyecto_id}", status_code=303)

    nombre_seguro = secure_filename(archivo.filename) or "archivo"
    nombre_final = f"{uuid.uuid4()}_{nombre_seguro}"
    ruta = os.path.join(UPLOAD_FOLDER, nombre_final)

    contenido = await archivo.read()
    with open(ruta, "wb") as f:
        f.write(contenido)

    conn = conectar()
    cur = conn.execute(
        """INSERT INTO documentos
           (proyecto_id, usuario_id, nombre_original, nombre_archivo, ruta, tipo, estado, chunks, creado_en)
           VALUES (?, ?, ?, ?, ?, ?, 'INDEXANDO', 0, ?)""",
        (proyecto_id, u["id"], archivo.filename, nombre_final, ruta, ext.lstrip("."), ahora())
    )
    doc_id = cur.lastrowid
    conn.commit()
    conn.close()

    # Indexar en background (BackgroundTasks de FastAPI es más fiable que asyncio.create_task)
    background_tasks.add_task(_indexar_bg, ruta, doc_id, u["id"], proyecto_id, archivo.filename)

    request.session["mensaje"] = f"'{archivo.filename}' subido. Indexando en segundo plano..."
    return RedirectResponse(f"/proyectos/{proyecto_id}", status_code=303)


async def _indexar_bg(ruta: str, doc_id: int, usuario_id: int, proyecto_id: int, nombre_original: str = ""):
    """Indexa un documento en background."""
    try:
        n_chunks = await asyncio.to_thread(
            indexar_documento, ruta, doc_id, usuario_id, proyecto_id, nombre_original
        )
        estado = "INDEXADO" if n_chunks > 0 else "VACIO"
        conn = conectar()
        conn.execute(
            "UPDATE documentos SET estado = ?, chunks = ? WHERE id = ?",
            (estado, n_chunks, doc_id)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        import traceback
        traceback.print_exc()
        conn = conectar()
        conn.execute("UPDATE documentos SET estado = 'ERROR' WHERE id = ?", (doc_id,))
        conn.commit()
        conn.close()


@app.get("/documentos/{doc_id}/ver")
async def ver_documento(doc_id: int, request: Request):
    u = usuario_actual(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    conn = conectar()
    doc = conn.execute(
        "SELECT * FROM documentos WHERE id = ? AND usuario_id = ?",
        (doc_id, u["id"])
    ).fetchone()
    conn.close()
    if not doc:
        raise HTTPException(status_code=404)
    return FileResponse(doc["ruta"], filename=doc["nombre_original"])


@app.post("/documentos/{doc_id}/eliminar")
async def eliminar_documento(doc_id: int, request: Request):
    u = usuario_actual(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    conn = conectar()
    doc = conn.execute(
        "SELECT * FROM documentos WHERE id = ? AND usuario_id = ?",
        (doc_id, u["id"])
    ).fetchone()
    if doc:
        proyecto_id = doc["proyecto_id"]
        try:
            os.remove(doc["ruta"])
        except FileNotFoundError:
            pass
        conn.execute("DELETE FROM documentos WHERE id = ?", (doc_id,))
        conn.commit()
        conn.close()
        return RedirectResponse(f"/proyectos/{proyecto_id}", status_code=303)
    conn.close()
    return RedirectResponse("/", status_code=303)


# ── Chat ───────────────────────────────────────────────────────────────────────
@app.get("/chat", response_class=HTMLResponse)
async def chat_global(request: Request):
    u = usuario_actual(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    conn = conectar()
    historial = conn.execute(
        "SELECT * FROM mensajes_chat WHERE usuario_id = ? AND proyecto_id IS NULL ORDER BY creado_en ASC LIMIT 100",
        (u["id"],)
    ).fetchall()
    proyectos = conn.execute(
        "SELECT * FROM proyectos WHERE usuario_id = ?", (u["id"],)
    ).fetchall()
    conn.close()
    return templates.TemplateResponse("chat.html", {
        "request": request,
        "usuario": u,
        "historial": historial,
        "proyectos": proyectos,
        "proyecto_actual": None,
        "activo": "chat",
    })


@app.get("/chat/{proyecto_id}", response_class=HTMLResponse)
async def chat_proyecto(proyecto_id: int, request: Request):
    u = usuario_actual(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    conn = conectar()
    proyecto = conn.execute(
        "SELECT * FROM proyectos WHERE id = ? AND usuario_id = ?",
        (proyecto_id, u["id"])
    ).fetchone()
    if not proyecto:
        conn.close()
        return RedirectResponse("/", status_code=303)
    historial = conn.execute(
        "SELECT * FROM mensajes_chat WHERE usuario_id = ? AND proyecto_id = ? ORDER BY creado_en ASC LIMIT 100",
        (u["id"], proyecto_id)
    ).fetchall()
    proyectos = conn.execute(
        "SELECT * FROM proyectos WHERE usuario_id = ?", (u["id"],)
    ).fetchall()
    conn.close()
    return templates.TemplateResponse("chat.html", {
        "request": request,
        "usuario": u,
        "historial": historial,
        "proyectos": proyectos,
        "proyecto_actual": proyecto,
        "activo": "chat",
    })


@app.post("/api/chat")
async def api_chat(request: Request):
    u = usuario_actual(request)
    if not u:
        raise HTTPException(status_code=401)

    data = await request.json()
    consulta = data.get("consulta", "").strip()
    proyecto_id = data.get("proyecto_id")  # None = todos los documentos

    if not consulta:
        raise HTTPException(status_code=400, detail="Consulta vacía")

    conn = conectar()
    historial = conn.execute(
        "SELECT * FROM mensajes_chat WHERE usuario_id = ? AND proyecto_id IS ? ORDER BY creado_en ASC LIMIT 20",
        (u["id"], proyecto_id)
    ).fetchall()

    # Guardar mensaje usuario
    conn.execute(
        "INSERT INTO mensajes_chat (usuario_id, proyecto_id, rol, contenido, creado_en) VALUES (?, ?, 'user', ?, ?)",
        (u["id"], proyecto_id, consulta, ahora())
    )
    conn.commit()

    try:
        historial_lista = [{"rol": h["rol"], "contenido": h["contenido"]} for h in historial]
        resultado = await asyncio.to_thread(
            chat_con_contexto, consulta, historial_lista, u["id"], proyecto_id
        )
        respuesta = resultado["respuesta"]
        fuentes = resultado["fuentes"]
    except Exception as e:
        import traceback
        traceback.print_exc()
        respuesta = f"Error al generar respuesta: {e}"
        fuentes = []

    # Guardar respuesta
    conn.execute(
        "INSERT INTO mensajes_chat (usuario_id, proyecto_id, rol, contenido, creado_en) VALUES (?, ?, 'assistant', ?, ?)",
        (u["id"], proyecto_id, respuesta, ahora())
    )
    conn.commit()
    conn.close()

    return {"respuesta": respuesta, "fuentes": fuentes}


@app.post("/api/buscar")
async def api_buscar(request: Request):
    u = usuario_actual(request)
    if not u:
        raise HTTPException(status_code=401)

    data = await request.json()
    consulta = data.get("consulta", "").strip()
    proyecto_id = data.get("proyecto_id")

    if not consulta:
        raise HTTPException(status_code=400)

    try:
        resultados = await asyncio.to_thread(buscar, consulta, u["id"], proyecto_id)
        return {"resultados": resultados}
    except Exception as e:
        return {"resultados": [], "error": str(e)}


@app.post("/chat/limpiar")
async def limpiar_historial(request: Request):
    u = usuario_actual(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    data = await request.form()
    proyecto_id = data.get("proyecto_id")
    conn = conectar()
    if proyecto_id:
        conn.execute(
            "DELETE FROM mensajes_chat WHERE usuario_id = ? AND proyecto_id = ?",
            (u["id"], int(proyecto_id))
        )
        conn.commit()
        conn.close()
        return RedirectResponse(f"/chat/{proyecto_id}", status_code=303)
    else:
        conn.execute(
            "DELETE FROM mensajes_chat WHERE usuario_id = ? AND proyecto_id IS NULL",
            (u["id"],)
        )
        conn.commit()
        conn.close()
        return RedirectResponse("/chat", status_code=303)


# ── Startup ────────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    init_db()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
