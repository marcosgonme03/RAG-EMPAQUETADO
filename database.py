import sqlite3
import os
from datetime import datetime

DB_PATH = "rag.sqlite"


def conectar():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ahora():
    return datetime.utcnow().isoformat()


def init_db():
    conn = conectar()
    cur = conn.cursor()

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        creado_en TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS proyectos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario_id INTEGER NOT NULL,
        nombre TEXT NOT NULL,
        descripcion TEXT,
        creado_en TEXT NOT NULL,
        FOREIGN KEY(usuario_id) REFERENCES usuarios(id)
    );

    CREATE TABLE IF NOT EXISTS documentos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        proyecto_id INTEGER NOT NULL,
        usuario_id INTEGER NOT NULL,
        nombre_original TEXT NOT NULL,
        nombre_archivo TEXT NOT NULL,
        ruta TEXT NOT NULL,
        tipo TEXT NOT NULL,
        estado TEXT NOT NULL DEFAULT 'PENDIENTE',
        chunks INTEGER NOT NULL DEFAULT 0,
        creado_en TEXT NOT NULL,
        FOREIGN KEY(proyecto_id) REFERENCES proyectos(id),
        FOREIGN KEY(usuario_id) REFERENCES usuarios(id)
    );

    CREATE TABLE IF NOT EXISTS mensajes_chat (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario_id INTEGER NOT NULL,
        proyecto_id INTEGER,
        rol TEXT NOT NULL,
        contenido TEXT NOT NULL,
        creado_en TEXT NOT NULL,
        FOREIGN KEY(usuario_id) REFERENCES usuarios(id),
        FOREIGN KEY(proyecto_id) REFERENCES proyectos(id)
    );
    """)

    # Reset documentos atascados en INDEXANDO por reinicio
    conn.execute(
        "UPDATE documentos SET estado = 'ERROR' WHERE estado = 'INDEXANDO'"
    )

    conn.commit()
    conn.close()
