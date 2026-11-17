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
from annotations_service import (
    get_highlights,
    get_notes,
    list_bookmarks,
    MAX_EPUB_CFI_LEN,
    MAX_CHAPTER_LABEL_LEN,
    MAX_BOOKMARK_LABEL_LEN,
)


class ImportError(Exception):
    """Exceção levantada quando um arquivo de importação é inválido ou incompatível."""
    pass


# =========================================================================
# EXPORTAÇÃO
# =========================================================================

def export_book_annotations_markdown(book_id: str, user_id: int = 1) -> str:
    """
    Gera um relatório completo de estudo em Markdown (.md) para o livro especificado e usuário.
    Exporta exclusivamente as anotações próprias do usuário (`scope='mine'`).
    Organizado por páginas/posições, com destaques, notas vinculadas e notas avulsas.
    """
    book = get_book(book_id, user_id=user_id)
    highlights = get_highlights(book_id, user_id=user_id, scope="mine")
    notes = get_notes(book_id, user_id=user_id, scope="mine")

    # Coleta páginas com desenhos do usuário
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT page, updated_at FROM page_drawings WHERE book_id = ? AND user_id = ? ORDER BY page ASC",
            (book_id, user_id),
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


def export_book_annotations_json(book_id: str, user_id: int = 1) -> Dict[str, Any]:
    """
    Gera uma estrutura JSON completa preservando coordenadas, EPUB CFI, IDs, notas,
    traços vetoriais e marcadores pessoais exclusivamente do próprio usuário (`scope='mine'`).
    Permite backup pontual das anotações e migração segura entre instâncias do Lectoria.
    """
    book = get_book(book_id, user_id=user_id)
    highlights = get_highlights(book_id, user_id=user_id, scope="mine")
    notes = get_notes(book_id, user_id=user_id, scope="mine")
    bookmarks = list_bookmarks(book_id, user_id=user_id)

    # Coleta desenhos com seus traços completos exclusivamente do usuário
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, page, strokes_json, visibility, updated_at FROM page_drawings WHERE book_id = ? AND user_id = ? ORDER BY page ASC",
            (book_id, user_id),
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
        "bookmarks": bookmarks,
    }


# =========================================================================
# IMPORTAÇÃO (VALIDAÇÃO, PREVIEW E APLICAÇÃO TRANSACTICIONAL)
# =========================================================================

MAX_IMPORT_HIGHLIGHTS = 5000
MAX_IMPORT_NOTES = 5000
MAX_IMPORT_DRAWINGS = 1000
MAX_IMPORT_BOOKMARKS = 2000
MAX_STROKES_PER_DRAWING = 500
MAX_POINTS_PER_STROKE = 5000
MAX_TEXT_LENGTH = 10000
MAX_NOTE_LENGTH = 20000
MAX_RECTS_PER_HIGHLIGHT = 100
MAX_JSON_DEPTH = 10


def _validate_imported_cfi_and_chapter(item: Dict[str, Any], item_label: str) -> None:
    """Valida campos opcionais `epub_cfi` e `chapter_label` em itens importados."""
    cfi = item.get("epub_cfi")
    if cfi is not None:
        if not isinstance(cfi, str):
            raise ImportError(f"EPUB CFI inválido em {item_label}.")
        clean_cfi = cfi.strip()
        if clean_cfi:
            if len(clean_cfi) > MAX_EPUB_CFI_LEN:
                raise ImportError(f"EPUB CFI excede o limite de {MAX_EPUB_CFI_LEN} caracteres em {item_label}.")
            if not (clean_cfi.startswith("epubcfi(") and clean_cfi.endswith(")")) or "<" in clean_cfi or ">" in clean_cfi:
                raise ImportError(f"Formato de EPUB CFI inválido em {item_label}.")

    chapter = item.get("chapter_label")
    if chapter is not None:
        if not isinstance(chapter, str):
            raise ImportError(f"Rótulo de capítulo inválido em {item_label}.")
        if len(chapter.strip()) > MAX_CHAPTER_LABEL_LEN:
            raise ImportError(f"Rótulo de capítulo excede o limite de {MAX_CHAPTER_LABEL_LEN} caracteres em {item_label}.")


def _check_json_depth(obj: Any, current_depth: int = 1) -> None:
    """Garante que o objeto JSON não possui aninhamento recursivo ou excessivo."""
    if current_depth > MAX_JSON_DEPTH:
        raise ImportError(f"Estrutura JSON com profundidade de aninhamento excessiva (máx: {MAX_JSON_DEPTH}).")
    if isinstance(obj, dict):
        for v in obj.values():
            _check_json_depth(v, current_depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _check_json_depth(item, current_depth + 1)


def validate_import_payload(data: Any) -> None:
    """
    Validação rigorosa de schema, tipos, limites de tamanho e intervalos
    para prevenção de ataques de DoS, travamentos e injeção de dados malformados.
    """
    if not isinstance(data, dict):
        raise ImportError("O conteúdo do arquivo deve ser um objeto JSON válido.")

    _check_json_depth(data)

    if data.get("format") != "lectoria_study_export" and not ("highlights" in data or "notes" in data or "drawings" in data):
        raise ImportError("Formato de exportação incompatível com o Lectoria.")

    highlights = data.get("highlights", [])
    notes = data.get("notes", [])
    drawings = data.get("drawings", [])
    bookmarks = data.get("bookmarks", [])

    if not isinstance(highlights, list):
        raise ImportError("O campo 'highlights' deve ser uma lista.")
    if not isinstance(notes, list):
        raise ImportError("O campo 'notes' deve ser uma lista.")
    if not isinstance(drawings, list):
        raise ImportError("O campo 'drawings' deve ser uma lista.")
    if not isinstance(bookmarks, list):
        raise ImportError("O campo 'bookmarks' deve ser uma lista.")

    if len(highlights) > MAX_IMPORT_HIGHLIGHTS:
        raise ImportError(f"Quantidade de destaques excede o limite máximo permitido ({MAX_IMPORT_HIGHLIGHTS}).")
    if len(notes) > MAX_IMPORT_NOTES:
        raise ImportError(f"Quantidade de notas excede o limite máximo permitido ({MAX_IMPORT_NOTES}).")
    if len(drawings) > MAX_IMPORT_DRAWINGS:
        raise ImportError(f"Quantidade de desenhos excede o limite máximo permitido ({MAX_IMPORT_DRAWINGS}).")
    if len(bookmarks) > MAX_IMPORT_BOOKMARKS:
        raise ImportError(f"Quantidade de marcadores excede o limite máximo permitido ({MAX_IMPORT_BOOKMARKS}).")

    # Validação detalhada de cada destaque
    for idx, h in enumerate(highlights):
        if not isinstance(h, dict):
            raise ImportError(f"Item de destaque inválido na posição {idx}.")
        page = h.get("page")
        if not isinstance(page, int) or isinstance(page, bool) or page < 1 or page > 100000:
            raise ImportError(f"Número de página inválido no destaque {idx}: {page}")
        text = h.get("selected_text")
        if text is None or not isinstance(text, str):
            raise ImportError(f"Texto selecionado ausente ou inválido no destaque {idx}.")
        if len(text) > MAX_TEXT_LENGTH:
            raise ImportError(f"Texto do destaque {idx} excede o tamanho máximo de {MAX_TEXT_LENGTH} caracteres.")

        _validate_imported_cfi_and_chapter(h, f"destaque {idx}")

        rects = h.get("rects", [])
        if not isinstance(rects, list):
            raise ImportError(f"Retângulos de destaque inválidos na posição {idx}.")
        if len(rects) > MAX_RECTS_PER_HIGHLIGHT:
            raise ImportError(f"Número excessivo de retângulos no destaque {idx} (máx: {MAX_RECTS_PER_HIGHLIGHT}).")
        for r in rects:
            if not isinstance(r, dict):
                raise ImportError(f"Retângulo inválido no destaque {idx}.")
            for coord_key in ("x1", "y1", "x2", "y2", "x", "y", "w", "h"):
                if coord_key in r:
                    val = r[coord_key]
                    if not isinstance(val, (int, float)) or isinstance(val, bool) or val < -0.5 or val > 1.5:
                        raise ImportError(f"Coordenada normalizada fora dos limites permitidos ({coord_key}={val}) no destaque {idx}.")

    # Validação detalhada de cada nota
    for idx, n in enumerate(notes):
        if not isinstance(n, dict):
            raise ImportError(f"Item de nota inválido na posição {idx}.")
        page = n.get("page")
        if not isinstance(page, int) or isinstance(page, bool) or page < 1 or page > 100000:
            raise ImportError(f"Número de página inválido na nota {idx}: {page}")
        content = n.get("content")
        if content is None or not isinstance(content, str):
            raise ImportError(f"Conteúdo ausente ou inválido na nota {idx}.")
        if len(content) > MAX_NOTE_LENGTH:
            raise ImportError(f"Conteúdo da nota {idx} excede o limite de {MAX_NOTE_LENGTH} caracteres.")
        _validate_imported_cfi_and_chapter(n, f"nota {idx}")

    # Validação detalhada de cada marcador (opcional)
    for idx, bm in enumerate(bookmarks):
        if not isinstance(bm, dict):
            raise ImportError(f"Item de marcador inválido na posição {idx}.")
        page = bm.get("page", 1)
        if not isinstance(page, int) or isinstance(page, bool) or page < 1 or page > 100000:
            raise ImportError(f"Número de página inválido no marcador {idx}: {page}")
        label = bm.get("label", "")
        if label is not None and (not isinstance(label, str) or len(label) > MAX_BOOKMARK_LABEL_LEN):
            raise ImportError(f"Rótulo de marcador inválido na posição {idx}.")
        _validate_imported_cfi_and_chapter(bm, f"marcador {idx}")

    # Validação detalhada de cada página de desenhos
    for idx, d in enumerate(drawings):
        if not isinstance(d, dict):
            raise ImportError(f"Item de desenho inválido na posição {idx}.")
        page = d.get("page")
        if not isinstance(page, int) or isinstance(page, bool) or page < 1 or page > 100000:
            raise ImportError(f"Número de página inválido no desenho {idx}: {page}")
        strokes = d.get("strokes", [])
        if not isinstance(strokes, list):
            raise ImportError(f"Traços do desenho inválidos na página {page}.")
        if len(strokes) > MAX_STROKES_PER_DRAWING:
            raise ImportError(f"Quantidade de traços excede o limite na página {page} (máx: {MAX_STROKES_PER_DRAWING}).")
        for s_idx, s in enumerate(strokes):
            if not isinstance(s, dict):
                continue
            width = s.get("width")
            if width is not None and (not isinstance(width, (int, float)) or isinstance(width, bool) or width < 0.5 or width > 64):
                raise ImportError(f"Espessura do traço inválida ({width}) no desenho da página {page}.")
            points = s.get("points", [])
            if not isinstance(points, list):
                raise ImportError(f"Lista de pontos inválida no traço {s_idx} da página {page}.")
            if len(points) > MAX_POINTS_PER_STROKE:
                raise ImportError(f"Traço com pontos excessivos na página {page} (máx: {MAX_POINTS_PER_STROKE}).")
            for pt in points:
                if not isinstance(pt, dict):
                    continue
                px = pt.get("x")
                py = pt.get("y")
                if px is not None and (not isinstance(px, (int, float)) or isinstance(px, bool) or px < -0.5 or px > 1.5):
                    raise ImportError(f"Ponto X fora dos limites permitidos ({px}) na página {page}.")
                if py is not None and (not isinstance(py, (int, float)) or isinstance(py, bool) or py < -0.5 or py > 1.5):
                    raise ImportError(f"Ponto Y fora dos limites permitidos ({py}) na página {page}.")


def preview_import_annotations(target_book_id: str, data: Dict[str, Any], user_id: int = 1) -> Dict[str, Any]:
    """
    Analisa os dados de importação sem alterar o banco de dados para o usuário indicado.
    Detecta novos itens e duplicatas existentes para que o usuário aprove a operação.
    """
    validate_import_payload(data)
    get_book(target_book_id, user_id=user_id)  # Valida que o livro de destino existe

    existing_highlights = get_highlights(target_book_id, user_id=user_id, scope="mine")
    existing_notes = get_notes(target_book_id, user_id=user_id, scope="mine")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT page, strokes_json FROM page_drawings WHERE book_id = ? AND user_id = ?", (target_book_id, user_id))
        existing_drawings = {r["page"]: r["strokes_json"] for r in cursor.fetchall()}

    # Conjuntos para detecção de duplicatas (restritos aos próprios itens do usuário)
    existing_hl_ids = {h["id"] for h in existing_highlights}
    existing_hl_tuples = {
        (h["page"], (h.get("epub_cfi") or "").strip(), h["selected_text"].strip()) for h in existing_highlights
    }

    existing_note_ids = {n["id"] for n in existing_notes}
    existing_note_tuples = {
        (n["page"], (n.get("epub_cfi") or "").strip(), n["content"].strip()) for n in existing_notes
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
        h_cfi = str(h.get("epub_cfi") or "").strip()
        h_tuple = (h["page"], h_cfi, str(h["selected_text"]).strip())
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
        n_cfi = str(n.get("epub_cfi") or "").strip()
        n_tuple = (n["page"], n_cfi, str(n["content"]).strip())
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


def apply_import_annotations(target_book_id: str, data: Dict[str, Any], user_id: int = 1) -> Dict[str, Any]:
    """
    Aplica a importação de forma estritamente transacional no SQLite para o usuário indicado.
    Se qualquer inserção falhar, a transação sofre rollback total, mantendo o banco intacto.
    """
    validate_import_payload(data)
    get_book(target_book_id, user_id=user_id)

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

        # 1. Carrega dados já existentes exclusivamente do usuário para checagem rápida de duplicatas
        cursor.execute(
            "SELECT id, page, epub_cfi, selected_text FROM highlights WHERE book_id = ? AND user_id = ?",
            (target_book_id, user_id),
        )
        existing_hl_rows = cursor.fetchall()
        existing_hl_ids = {r["id"] for r in existing_hl_rows}
        existing_hl_tuples = {(r["page"], (r["epub_cfi"] or "").strip(), r["selected_text"].strip()) for r in existing_hl_rows}

        cursor.execute(
            "SELECT id, page, epub_cfi, content FROM notes WHERE book_id = ? AND user_id = ?",
            (target_book_id, user_id),
        )
        existing_note_rows = cursor.fetchall()
        existing_note_ids = {r["id"] for r in existing_note_rows}
        existing_note_tuples = {(r["page"], (r["epub_cfi"] or "").strip(), r["content"].strip()) for r in existing_note_rows}

        cursor.execute("SELECT page, strokes_json FROM page_drawings WHERE book_id = ? AND user_id = ?", (target_book_id, user_id))
        existing_drawings = {r["page"]: r["strokes_json"] for r in cursor.fetchall()}

        # 2. Inserção de Destaques (sempre forçando user_id autenticado e visibility = 'private')
        for h in incoming_highlights:
            if not isinstance(h, dict) or "page" not in h or "selected_text" not in h:
                continue
            original_id = h.get("id")
            page = int(h["page"])
            text = str(h["selected_text"]).strip()
            if not text:
                continue
            epub_cfi = str(h.get("epub_cfi") or "").strip() or None
            chapter_label = str(h.get("chapter_label") or "").strip() or None

            h_tuple = (page, epub_cfi or "", text)
            if (original_id and original_id in existing_hl_ids) or (h_tuple in existing_hl_tuples):
                # Duplicata já existente do próprio usuário; preserva o ID para mapeamento de notas
                if original_id and original_id in existing_hl_ids:
                    highlight_id_map[original_id] = original_id
                continue

            # Gera sempre um novo ID para garantir unicidade e impedir colisão com registros de terceiros
            new_hl_id = str(uuid.uuid4())
            if original_id:
                highlight_id_map[original_id] = new_hl_id

            color = h.get("color") or "#fef08a"
            rects = h.get("rects") or []
            rects_json = json.dumps(rects)
            context = h.get("text_context")

            cursor.execute(
                """
                INSERT INTO highlights (
                    id, book_id, page, color, rects_json, selected_text, text_context,
                    user_id, visibility, epub_cfi, chapter_label, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'private', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (new_hl_id, target_book_id, page, color, rects_json, text, context, user_id, epub_cfi, chapter_label),
            )
            existing_hl_ids.add(new_hl_id)
            existing_hl_tuples.add(h_tuple)
            imported_highlights_count += 1

        # 3. Inserção de Notas (sempre forçando user_id autenticado e visibility = 'private')
        for n in incoming_notes:
            if not isinstance(n, dict) or "page" not in n or "content" not in n:
                continue
            page = int(n["page"])
            content = str(n["content"]).strip()
            if not content:
                continue
            epub_cfi = str(n.get("epub_cfi") or "").strip() or None
            chapter_label = str(n.get("chapter_label") or "").strip() or None

            original_id = n.get("id")
            n_tuple = (page, epub_cfi or "", content)
            if (original_id and original_id in existing_note_ids) or (n_tuple in existing_note_tuples):
                continue

            new_note_id = str(uuid.uuid4())
            orig_hl_id = n.get("highlight_id")
            # Mapeia para o novo ID do destaque inserido, se houver
            target_hl_id = highlight_id_map.get(orig_hl_id, orig_hl_id) if orig_hl_id else None

            # Valida se o destaque realmente existe na base DO PRÓPRIO USUÁRIO (anti-IDOR)
            if target_hl_id and target_hl_id not in existing_hl_ids:
                target_hl_id = None

            color = n.get("color") or "#fef08a"

            cursor.execute(
                """
                INSERT INTO notes (
                    id, book_id, page, highlight_id, content, color,
                    user_id, visibility, epub_cfi, chapter_label, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'private', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (new_note_id, target_book_id, page, target_hl_id, content, color, user_id, epub_cfi, chapter_label),
            )
            existing_note_ids.add(new_note_id)
            existing_note_tuples.add(n_tuple)
            imported_notes_count += 1

        # 4. Inserção / Mesclagem de Desenhos (restrita ao próprio usuário, sempre 'private' ao criar)
        for d in incoming_drawings:
            if not isinstance(d, dict) or "page" not in d:
                continue
            page = int(d["page"])
            incoming_strokes = d.get("strokes") or []
            if not incoming_strokes:
                continue

            if page in existing_drawings:
                # Se já há desenhos do próprio usuário na página, mescla os traços evitando duplicatas exatas
                try:
                    curr_strokes = json.loads(existing_drawings[page])
                except Exception:
                    curr_strokes = []

                existing_stroke_hashes = {json.dumps(s.get("points", [])) for s in curr_strokes if isinstance(s, dict)}
                added_strokes = [s for s in incoming_strokes if json.dumps(s.get("points", [])) not in existing_stroke_hashes]

                if added_strokes:
                    combined = curr_strokes + added_strokes
                    cursor.execute(
                        """
                        UPDATE page_drawings
                        SET strokes_json = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE book_id = ? AND page = ? AND user_id = ?
                        """,
                        (json.dumps(combined), target_book_id, page, user_id),
                    )
                    imported_drawings_count += 1
            else:
                drawing_id = str(uuid.uuid4())
                cursor.execute(
                    """
                    INSERT INTO page_drawings (
                        id, book_id, page, strokes_json, user_id, visibility, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, 'private', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (drawing_id, target_book_id, page, json.dumps(incoming_strokes), user_id),
                )
                existing_drawings[page] = json.dumps(incoming_strokes)
                imported_drawings_count += 1

        # 5. Inserção de Marcadores Pessoais (se presentes no arquivo importado)
        incoming_bookmarks = data.get("bookmarks", [])
        for bm in incoming_bookmarks:
            if not isinstance(bm, dict):
                continue
            bm_page = int(bm.get("page", 1))
            bm_cfi = str(bm.get("epub_cfi") or "").strip() or None
            bm_chapter = str(bm.get("chapter_label") or "").strip() or None
            bm_label = str(bm.get("label") or "").strip()[:MAX_BOOKMARK_LABEL_LEN]
            cursor.execute(
                """
                INSERT OR IGNORE INTO bookmarks (
                    id, book_id, user_id, page, epub_cfi, chapter_label, label, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (str(uuid.uuid4()), target_book_id, user_id, bm_page, bm_cfi, bm_chapter, bm_label),
            )

    return {
        "status": "success",
        "imported_counts": {
            "highlights": imported_highlights_count,
            "notes": imported_notes_count,
            "drawings": imported_drawings_count,
            "total": imported_highlights_count + imported_notes_count + imported_drawings_count,
        },
    }

