"""
Módulo de serviço para Exportação e Importação de anotações do Lectoria.
Suporta exportação em Markdown (.md) estruturado e JSON (.json) completo.
Gerencia importação transacional com validação de schema, prevenção de duplicatas
e pré-visualização (preview) antes da aplicação no banco SQLite.
"""

from datetime import datetime
import json
import uuid
from typing import Any, Dict, List, Optional, Tuple

from database import get_db
from library import get_book, BookNotFoundError
from annotations_service import get_highlights, get_notes


class ImportError(Exception):
    """Exceção levantada quando um arquivo de importação é inválido ou incompatível."""
    pass


# =========================================================================
# EXPORTAÇÃO
# =========================================================================

def export_book_annotations_markdown(book_id: str) -> str:
    """
    Gera um relatório completo de estudo em Markdown (.md) para o livro especificado.
    Organizado por páginas, com destaques, notas vinculadas e notas avulsas.
    """
    book = get_book(book_id)
    highlights = get_highlights(book_id)
    notes = get_notes(book_id)

    # Coleta páginas com desenhos
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT page, updated_at FROM page_drawings WHERE book_id = ? ORDER BY page ASC",
            (book_id,),
        )
        drawings = [dict(r) for r in cursor.fetchall()]

    lines: List[str] = []
    lines.append(f"# Caderno de Leituras e Estudos — {book.get('title', 'Documento')}")
    if book.get("author"):
        lines.append(f"**Autor:** {book['author']}")
    lines.append(f"**Exportado em:** {datetime.now().strftime('%d/%m/%Y às %H:%M')}")
    lines.append(f"**Arquivo original:** `{book.get('filename', '')}`")
    lines.append("")

    # Resumo estatístico
    lines.append("## Sumário de Anotações")
    lines.append(f"- **Destaques de texto:** {len(highlights)}")
    lines.append(f"- **Notas e reflexões:** {len(notes)}")
    lines.append(f"- **Páginas com desenhos/esboços:** {len(drawings)}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Coleta todas as páginas com atividade
    pages_set = set()
    for h in highlights:
        pages_set.add(h["page"])
    for n in notes:
        pages_set.add(n["page"])
    for d in drawings:
        pages_set.add(d["page"])

    sorted_pages = sorted(list(pages_set))

    if not sorted_pages:
        lines.append("*Nenhuma anotação, destaque ou desenho registrado para este documento.*")
        return "\n".join(lines)

    for page_num in sorted_pages:
        lines.append(f"## Página {page_num}")
        lines.append("")

        # Destaques nesta página
        page_highlights = [h for h in highlights if h["page"] == page_num]
        # Notas associadas a destaques
        linked_note_ids = set()

        if page_highlights:
            for idx, h in enumerate(page_highlights, 1):
                created_str = h.get("created_at") or ""
                if created_str:
                    try:
                        dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                        date_display = dt.strftime("%d/%m/%Y %H:%M")
                    except Exception:
                        date_display = created_str
                else:
                    date_display = ""

                meta_parts = []
                if date_display:
                    meta_parts.append(date_display)
                meta_suffix = f" *({', '.join(meta_parts)})*" if meta_parts else ""

                lines.append(f"> \"{h['selected_text']}\"{meta_suffix}")
                lines.append("")

                # Verifica se há notas vinculadas a este destaque
                linked_notes = [n for n in notes if n.get("highlight_id") == h["id"]]
                for ln in linked_notes:
                    linked_note_ids.add(ln["id"])
                    ln_date = ln.get("created_at") or ""
                    ln_suffix = f" *({ln_date})*" if ln_date else ""
                    lines.append(f"  - 📝 **Nota:** {ln['content']}{ln_suffix}")
                if linked_notes:
                    lines.append("")

        # Notas avulsas da página (não vinculadas a destaques)
        page_notes_standalone = [
            n for n in notes if n["page"] == page_num and n["id"] not in linked_note_ids
        ]
        if page_notes_standalone:
            lines.append("### Notas da Página")
            for sn in page_notes_standalone:
                sn_date = sn.get("created_at") or ""
                sn_suffix = f" *({sn_date})*" if sn_date else ""
                lines.append(f"- 💡 {sn['content']}{sn_suffix}")
            lines.append("")

        # Indicação de desenho/anotação visual
        page_drawing = next((d for d in drawings if d["page"] == page_num), None)
        if page_drawing:
            lines.append("✍️ *Esta página contém ilustrações e anotações vetoriais desenhadas à mão.*")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def export_book_annotations_json(book_id: str) -> Dict[str, Any]:
    """
    Gera uma estrutura JSON completa preservando coordenadas, IDs, notas e traços vetoriais.
    Permite backup pontual das anotações e migração segura entre instâncias do Lectoria.
    """
    book = get_book(book_id)
    highlights = get_highlights(book_id)
    notes = get_notes(book_id)

    # Coleta desenhos com seus traços completos
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, page, strokes_json, updated_at FROM page_drawings WHERE book_id = ? ORDER BY page ASC",
            (book_id,),
        )
        drawings = []
        for r in cursor.fetchall():
            row = dict(r)
            try:
                row["strokes"] = json.loads(row["strokes_json"])
            except Exception:
                row["strokes"] = []
            row.pop("strokes_json", None)
            drawings.append(row)

    return {
        "version": "1.0",
        "format": "lectoria_study_export",
        "exported_at": datetime.now().isoformat(),
        "book": {
            "id": book["id"],
            "title": book.get("title"),
            "author": book.get("author"),
            "filename": book.get("filename"),
            "page_count": book.get("page_count"),
        },
        "highlights": highlights,
        "notes": notes,
        "drawings": drawings,
    }


# =========================================================================
# IMPORTAÇÃO (VALIDAÇÃO, PREVIEW E APLICAÇÃO TRANSACTICIONAL)
# =========================================================================

def validate_import_payload(data: Any) -> None:
    """Valida a estrutura básica do arquivo de importação JSON."""
    if not isinstance(data, dict):
        raise ImportError("O conteúdo do arquivo deve ser um objeto JSON válido.")
    if data.get("format") != "lectoria_study_export" and not ("highlights" in data or "notes" in data or "drawings" in data):
        raise ImportError("Formato de exportação incompatível com o Lectoria.")
    if not isinstance(data.get("highlights", []), list):
        raise ImportError("O campo 'highlights' deve ser uma lista.")
    if not isinstance(data.get("notes", []), list):
        raise ImportError("O campo 'notes' deve ser uma lista.")
    if not isinstance(data.get("drawings", []), list):
        raise ImportError("O campo 'drawings' deve ser uma lista.")


def preview_import_annotations(target_book_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analisa os dados de importação sem alterar o banco de dados.
    Detecta novos itens e duplicatas existentes para que o usuário aprove a operação.
    """
    validate_import_payload(data)
    get_book(target_book_id)  # Valida que o livro de destino existe

    existing_highlights = get_highlights(target_book_id)
    existing_notes = get_notes(target_book_id)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT page, strokes_json FROM page_drawings WHERE book_id = ?", (target_book_id,))
        existing_drawings = {r["page"]: r["strokes_json"] for r in cursor.fetchall()}

    # Conjuntos para detecção de duplicatas
    existing_hl_ids = {h["id"] for h in existing_highlights}
    existing_hl_tuples = {
        (h["page"], h["selected_text"].strip()) for h in existing_highlights
    }

    existing_note_ids = {n["id"] for n in existing_notes}
    existing_note_tuples = {
        (n["page"], n["content"].strip()) for n in existing_notes
    }

    incoming_highlights = data.get("highlights", [])
    incoming_notes = data.get("notes", [])
    incoming_drawings = data.get("drawings", [])

    new_highlights = []
    dup_highlights = 0
    for h in incoming_highlights:
        if not isinstance(h, dict) or "page" not in h or "selected_text" not in h:
            continue
        h_id = h.get("id")
        h_tuple = (h["page"], str(h["selected_text"]).strip())
        if (h_id and h_id in existing_hl_ids) or (h_tuple in existing_hl_tuples):
            dup_highlights += 1
        else:
            new_highlights.append(h)

    new_notes = []
    dup_notes = 0
    for n in incoming_notes:
        if not isinstance(n, dict) or "page" not in n or "content" not in n:
            continue
        n_id = n.get("id")
        n_tuple = (n["page"], str(n["content"]).strip())
        if (n_id and n_id in existing_note_ids) or (n_tuple in existing_note_tuples):
            dup_notes += 1
        else:
            new_notes.append(n)

    new_drawings = []
    dup_drawings = 0
    for d in incoming_drawings:
        if not isinstance(d, dict) or "page" not in d:
            continue
        p = d["page"]
        strokes = d.get("strokes", [])
        if not strokes:
            continue
        strokes_str = json.dumps(strokes)
        if p in existing_drawings:
            # Já existe desenho na página
            if existing_drawings[p] == strokes_str:
                dup_drawings += 1
            else:
                new_drawings.append(d)
        else:
            new_drawings.append(d)

    return {
        "valid": True,
        "source_book": data.get("book", {}),
        "target_book_id": target_book_id,
        "counts": {
            "highlights_new": len(new_highlights),
            "highlights_duplicate": dup_highlights,
            "notes_new": len(new_notes),
            "notes_duplicate": dup_notes,
            "drawings_new": len(new_drawings),
            "drawings_duplicate": dup_drawings,
            "total_new_items": len(new_highlights) + len(new_notes) + len(new_drawings),
        },
        "preview_highlights": [
            {"page": h.get("page"), "text": h.get("selected_text", "")[:80]}
            for h in new_highlights[:5]
        ],
        "preview_notes": [
            {"page": n.get("page"), "content": n.get("content", "")[:80]}
            for n in new_notes[:5]
        ],
    }


def apply_import_annotations(target_book_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Aplica a importação de forma estritamente transacional no SQLite.
    Se qualquer inserção falhar, a transação sofre rollback total, mantendo o banco intacto.
    """
    validate_import_payload(data)
    get_book(target_book_id)

    incoming_highlights = data.get("highlights", [])
    incoming_notes = data.get("notes", [])
    incoming_drawings = data.get("drawings", [])

    imported_highlights_count = 0
    imported_notes_count = 0
    imported_drawings_count = 0

    # Mapeamento de IDs antigos para novos IDs de destaque (caso colidam ou precisem de remap)
    highlight_id_map: Dict[str, str] = {}

    with get_db() as conn:
        cursor = conn.cursor()

        # 1. Carrega dados já existentes para checagem rápida de duplicatas
        cursor.execute("SELECT id, page, selected_text FROM highlights WHERE book_id = ?", (target_book_id,))
        existing_hl_rows = cursor.fetchall()
        existing_hl_ids = {r["id"] for r in existing_hl_rows}
        existing_hl_tuples = {(r["page"], r["selected_text"].strip()) for r in existing_hl_rows}

        cursor.execute("SELECT id, page, content FROM notes WHERE book_id = ?", (target_book_id,))
        existing_note_rows = cursor.fetchall()
        existing_note_ids = {r["id"] for r in existing_note_rows}
        existing_note_tuples = {(r["page"], r["content"].strip()) for r in existing_note_rows}

        cursor.execute("SELECT page, strokes_json FROM page_drawings WHERE book_id = ?", (target_book_id,))
        existing_drawings = {r["page"]: r["strokes_json"] for r in cursor.fetchall()}

        # 2. Inserção de Destaques
        for h in incoming_highlights:
            if not isinstance(h, dict) or "page" not in h or "selected_text" not in h:
                continue
            original_id = h.get("id")
            page = int(h["page"])
            text = str(h["selected_text"]).strip()
            if not text:
                continue

            h_tuple = (page, text)
            if (original_id and original_id in existing_hl_ids) or (h_tuple in existing_hl_tuples):
                # Duplicata já existente; preserva o ID para mapeamento de notas
                if original_id:
                    highlight_id_map[original_id] = original_id
                continue

            # Gera um novo ID para garantir unicidade
            new_hl_id = str(uuid.uuid4())
            if original_id:
                highlight_id_map[original_id] = new_hl_id

            color = h.get("color") or "#fef08a"
            rects = h.get("rects") or []
            rects_json = json.dumps(rects)
            context = h.get("text_context")

            cursor.execute(
                """
                INSERT INTO highlights (id, book_id, page, color, rects_json, selected_text, text_context)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (new_hl_id, target_book_id, page, color, rects_json, text, context),
            )
            existing_hl_ids.add(new_hl_id)
            existing_hl_tuples.add(h_tuple)
            imported_highlights_count += 1

        # 3. Inserção de Notas
        for n in incoming_notes:
            if not isinstance(n, dict) or "page" not in n or "content" not in n:
                continue
            page = int(n["page"])
            content = str(n["content"]).strip()
            if not content:
                continue

            original_id = n.get("id")
            n_tuple = (page, content)
            if (original_id and original_id in existing_note_ids) or (n_tuple in existing_note_tuples):
                continue

            new_note_id = str(uuid.uuid4())
            orig_hl_id = n.get("highlight_id")
            # Mapeia para o novo ID do destaque inserido, se houver
            target_hl_id = highlight_id_map.get(orig_hl_id, orig_hl_id) if orig_hl_id else None

            # Valida se o destaque realmente existe na base
            if target_hl_id and target_hl_id not in existing_hl_ids:
                target_hl_id = None

            color = n.get("color") or "#fef08a"

            cursor.execute(
                """
                INSERT INTO notes (id, book_id, page, highlight_id, content, color)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (new_note_id, target_book_id, page, target_hl_id, content, color),
            )
            existing_note_ids.add(new_note_id)
            existing_note_tuples.add(n_tuple)
            imported_notes_count += 1

        # 4. Inserção / Mesclagem de Desenhos
        for d in incoming_drawings:
            if not isinstance(d, dict) or "page" not in d:
                continue
            page = int(d["page"])
            incoming_strokes = d.get("strokes") or []
            if not incoming_strokes:
                continue

            if page in existing_drawings:
                # Se já há desenhos na página, mescla os traços evitando duplicatas exatas
                try:
                    curr_strokes = json.loads(existing_drawings[page])
                except Exception:
                    curr_strokes = []

                # Serializações simples para evitar traços duplicados
                existing_stroke_hashes = {json.dumps(s.get("points", [])) for s in curr_strokes if isinstance(s, dict)}
                added_strokes = [s for s in incoming_strokes if json.dumps(s.get("points", [])) not in existing_stroke_hashes]

                if added_strokes:
                    combined = curr_strokes + added_strokes
                    cursor.execute(
                        """
                        UPDATE page_drawings
                        SET strokes_json = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE book_id = ? AND page = ?
                        """,
                        (json.dumps(combined), target_book_id, page),
                    )
                    imported_drawings_count += 1
            else:
                drawing_id = str(uuid.uuid4())
                cursor.execute(
                    """
                    INSERT INTO page_drawings (id, book_id, page, strokes_json, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (drawing_id, target_book_id, page, json.dumps(incoming_strokes)),
                )
                existing_drawings[page] = json.dumps(incoming_strokes)
                imported_drawings_count += 1

    return {
        "status": "success",
        "imported_counts": {
            "highlights": imported_highlights_count,
            "notes": imported_notes_count,
            "drawings": imported_drawings_count,
            "total": imported_highlights_count + imported_notes_count + imported_drawings_count,
        },
    }
