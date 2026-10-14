from __future__ import annotations
import io
import re
import uuid
from pathlib import Path
from typing import Any
import pandas as pd
from pypdf import PdfReader
from docx import Document as DocxDocument
from ..config import UPLOAD_DIR, MAX_UPLOAD_MB
from ..db import connection, json_dump, now_iso

ALLOWED = {".csv", ".xlsx", ".xls", ".pdf", ".docx", ".txt", ".md"}
TABULAR = {".csv", ".xlsx", ".xls"}


def safe_filename(name: str) -> str:
    base = Path(name or "upload").name
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .")
    return (base or "upload")[:180]


def _read_frame(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    raise ValueError("Only CSV and Excel files can be used for numeric analysis.")


def frame_for_dataset(dataset_id: str) -> pd.DataFrame:
    with connection() as conn:
        row = conn.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
    if not row:
        raise KeyError("Dataset not found.")
    path = Path(row["stored_path"])
    if not path.exists():
        raise FileNotFoundError("The stored dataset file is missing from disk.")
    return _read_frame(path)


def upload_file(filename: str, content: bytes) -> dict[str, Any]:
    if not content:
        raise ValueError("The uploaded file is empty.")
    if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
        raise ValueError(f"File exceeds the configured {MAX_UPLOAD_MB} MB upload limit.")
    safe = safe_filename(filename)
    suffix = Path(safe).suffix.lower()
    if suffix not in ALLOWED:
        raise ValueError(f"Unsupported file type {suffix or '(none)'}. Allowed: CSV, XLSX, XLS, PDF, DOCX, TXT, MD.")
    file_id = str(uuid.uuid4())
    stored = UPLOAD_DIR / f"{file_id}{suffix}"
    stored.write_bytes(content)
    now = now_iso()
    try:
        if suffix in TABULAR:
            frame = _read_frame(stored)
            if frame.shape[1] == 0:
                raise ValueError("The tabular file has no columns.")
            # Bound previews to avoid huge API responses. Full data remains local on disk.
            cols = [str(c) for c in frame.columns]
            preview = frame.head(8).replace({float("nan"): None}).to_dict(orient="records")
            # Make non-JSON-native values safe.
            preview = [{str(k): (None if pd.isna(v) else v.item() if hasattr(v, "item") else str(v) if not isinstance(v, (str, int, float, bool)) else v) for k, v in r.items()} for r in preview]
            with connection() as conn:
                conn.execute("INSERT INTO datasets(id,filename,stored_path,file_type,row_count,columns_json,preview_json,created_at) VALUES(?,?,?,?,?,?,?,?)", (file_id, safe, str(stored), suffix[1:], int(len(frame)), json_dump(cols), json_dump(preview), now))
            return {"kind": "dataset", "id": file_id, "filename": safe, "file_type": suffix[1:], "row_count": int(len(frame)), "columns": cols, "preview": preview}
        text = ""
        if suffix == ".txt" or suffix == ".md":
            text = content.decode("utf-8", errors="replace")
        elif suffix == ".pdf":
            reader = PdfReader(io.BytesIO(content))
            text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        elif suffix == ".docx":
            doc = DocxDocument(io.BytesIO(content))
            text = "\n".join(p.text for p in doc.paragraphs)
        with connection() as conn:
            conn.execute("INSERT INTO documents(id,filename,stored_path,file_type,size_bytes,extracted_text,created_at) VALUES(?,?,?,?,?,?,?)", (file_id, safe, str(stored), suffix[1:], len(content), text[:2_000_000], now))
        return {"kind": "document", "id": file_id, "filename": safe, "file_type": suffix[1:], "size_bytes": len(content), "characters_extracted": len(text), "preview": text[:700]}
    except Exception:
        stored.unlink(missing_ok=True)
        raise


def list_datasets() -> list[dict]:
    import json
    with connection() as conn:
        rows = conn.execute("SELECT id,filename,file_type,row_count,columns_json,preview_json,created_at FROM datasets ORDER BY created_at DESC").fetchall()
    return [{"id": r["id"], "filename": r["filename"], "file_type": r["file_type"], "row_count": r["row_count"], "columns": json.loads(r["columns_json"]), "preview": json.loads(r["preview_json"]), "created_at": r["created_at"]} for r in rows]


def list_documents(query: str = "") -> list[dict]:
    import json
    with connection() as conn:
        if query.strip():
            like = f"%{query.strip()}%"
            rows = conn.execute("SELECT id,filename,file_type,size_bytes,created_at,substr(extracted_text,1,500) AS preview FROM documents WHERE filename LIKE ? OR extracted_text LIKE ? ORDER BY created_at DESC LIMIT 100", (like, like)).fetchall()
        else:
            rows = conn.execute("SELECT id,filename,file_type,size_bytes,created_at,substr(extracted_text,1,500) AS preview FROM documents ORDER BY created_at DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]
