"""
Módulo de Enriquecimento Bibliográfico em Segundo Plano (Fase 4 e Fase 5).
Gerencia a fila de processamento assíncrono de metadados, controle de concorrência
para hardware modesto (Celeron), limites de taxa de requisições externas,
persistência de estados e atualização segura de livros e capas.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import urllib.request
import urllib.error

import config
from covers_service import validate_cover_image
from database import get_db
from metadata_service import identify_book_metadata
from taxonomy_service import map_raw_subjects_to_taxonomy, sync_book_taxonomy

logger = logging.getLogger("lectoria.enricher")


class BackgroundEnricher:
    """Fila e orquestrador de enriquecimento bibliográfico em segundo plano."""

    def __init__(self):
        self._queue: List[str] = []
        self._status: str = "idle"  # idle, running, paused, cancelled, completed
        self._worker_task: Optional[asyncio.Task] = None
        self._current_book: Optional[str] = None
        self._current_title: Optional[str] = None
        self._stats = {
            "total": 0,
            "processed": 0,
            "identified": 0,
            "needs_review": 0,
            "not_found": 0,
            "failed": 0,
            "started_at": None,
            "finished_at": None,
        }

    def get_status(self) -> Dict[str, Any]:
        """Retorna as métricas e o estado atual da fila de enriquecimento."""
        return {
            "status": self._status,
            "queue_length": len(self._queue),
            "current_book_id": self._current_book,
            "current_book_title": self._current_title,
            "total": self._stats.get("total", 0),
            "processed": self._stats.get("processed", 0),
            "identified": self._stats.get("identified", 0),
            "needs_review": self._stats.get("needs_review", 0),
            "not_found": self._stats.get("not_found", 0),
            "failed": self._stats.get("failed", 0),
            "stats": dict(self._stats),
        }

    def enqueue_books(self, book_ids: Optional[List[str]] = None, only_pending: bool = False) -> int:
        """
        Adiciona livros à fila de enriquecimento.
        Se book_ids for None, enfileira todos os livros ativos ou apenas os pendentes/não-identificados.
        """
        with get_db() as conn:
            cursor = conn.cursor()
            if book_ids:
                placeholders = ",".join(["?"] * len(book_ids))
                cursor.execute(
                    f"SELECT id FROM books WHERE id IN ({placeholders}) AND is_missing = 0;",
                    book_ids,
                )
            elif only_pending:
                cursor.execute(
                    """
                    SELECT id FROM books
                    WHERE is_missing = 0 AND (identification_status IS NULL OR identification_status IN ('pending', 'failed', 'not_found'))
                    ORDER BY created_at DESC;
                    """
                )
            else:
                cursor.execute("SELECT id FROM books WHERE is_missing = 0 ORDER BY created_at DESC;")

            found_ids = [row["id"] for row in cursor.fetchall()]

        # Adiciona à fila sem duplicatas
        added_count = 0
        existing_set = set(self._queue)
        for bid in found_ids:
            if bid not in existing_set:
                self._queue.append(bid)
                existing_set.add(bid)
                added_count += 1

        self._stats["total"] = len(self._queue) + self._stats["processed"]
        return added_count

    def start(self) -> bool:
        """Inicia ou retoma o processamento da fila em segundo plano."""
        if self._status == "running":
            return True

        if not self._queue:
            self._status = "idle"
            return False

        self._status = "running"
        if not self._stats["started_at"]:
            self._stats["started_at"] = datetime.now(timezone.utc).isoformat()
        self._stats["finished_at"] = None

        loop = asyncio.get_event_loop()
        self._worker_task = loop.create_task(self._process_queue_loop())
        return True

    def pause(self) -> None:
        """Pausa o processamento da fila sem descartar os itens pendentes."""
        if self._status == "running":
            self._status = "paused"
            if self._worker_task:
                self._worker_task.cancel()
                self._worker_task = None

    def cancel(self) -> None:
        """Cancela a execução e limpa a fila de processamento."""
        self._status = "cancelled"
        self._queue.clear()
        self._current_book = None
        self._current_title = None
        self._stats["finished_at"] = datetime.now(timezone.utc).isoformat()
        if self._worker_task:
            self._worker_task.cancel()
            self._worker_task = None

    async def _process_queue_loop(self) -> None:
        """Loop sequencial do worker de enriquecimento (concorrência = 1 para proteger CPU)."""
        logger.info("Iniciando fila de enriquecimento bibliográfico (%d itens)", len(self._queue))

        while self._queue and self._status == "running":
            book_id = self._queue.pop(0)
            self._current_book = book_id

            try:
                # Executa o enriquecimento do livro (operações I/O e rede em executor para não bloquear o loop)
                await asyncio.to_thread(self._enrich_single_book_sync, book_id)
                self._stats["processed"] += 1
            except asyncio.CancelledError:
                # Fila pausada ou cancelada
                break
            except Exception as e:
                logger.error("Falha ao enriquecer livro %s: %s", book_id, e)
                self._stats["failed"] += 1
                self._stats["processed"] += 1

            # Pausa defensiva entre livros para respeitar rate-limits de APIs públicas
            await asyncio.sleep(0.5)

        self._current_book = None
        self._current_title = None

        if not self._queue and self._status == "running":
            self._status = "completed"
            self._stats["finished_at"] = datetime.now(timezone.utc).isoformat()
            logger.info("Fila de enriquecimento concluída com sucesso: %s", self._stats)

    def _enrich_single_book_sync(self, book_id: str) -> None:
        """Processamento síncrono de enriquecimento de um único livro."""
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, title, author, filename, isbn10, isbn13, publisher, publish_year,
                       cover_type, cover_path, manual_lock_fields
                FROM books WHERE id = ?;
                """,
                (book_id,),
            )
            row = cursor.fetchone()
            if not row:
                return

            book_dict = dict(row)
            self._current_title = book_dict.get("title") or book_dict.get("filename")

            # Parse de campos bloqueados manualmente
            raw_locked = book_dict.get("manual_lock_fields") or "[]"
            try:
                locked_fields = set(json.loads(raw_locked))
            except Exception:
                locked_fields = set()

        # 1. Consulta candidatos bibliográficos externos
        candidates = identify_book_metadata(book_dict)

        # 2. Persiste os candidatos encontrados para auditoria e revisão
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM metadata_candidates WHERE book_id = ?;", (book_id,))
            for cand in candidates:
                cursor.execute(
                    """
                    INSERT INTO metadata_candidates (
                        book_id, source, external_id, title, author, publisher,
                        publish_year, isbn10, isbn13, cover_url, raw_json, score, confidence_level
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        book_id,
                        cand.get("source", "unknown"),
                        cand.get("external_id", ""),
                        cand.get("title", ""),
                        cand.get("author"),
                        cand.get("publisher"),
                        cand.get("publish_year"),
                        cand.get("isbn10"),
                        cand.get("isbn13"),
                        cand.get("cover_url"),
                        json.dumps(cand.get("raw_data", {})),
                        cand.get("score", 0.0),
                        cand.get("confidence_level", "insufficient"),
                    ),
                )

        if not candidates:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE books
                    SET identification_status = 'not_found', identification_score = 0.0,
                        metadata_updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?;
                    """,
                    (book_id,),
                )
            self._stats["not_found"] += 1
            return

        best = candidates[0]
        score = best.get("score", 0.0)
        confidence = best.get("confidence_level", "insufficient")

        if confidence == "reliable":
            # Correspondência segura: enriquece metadados respeitando campos bloqueados
            updates = []
            params = []

            if "title" not in locked_fields and best.get("title"):
                updates.append("title = ?")
                params.append(best["title"])

            if "author" not in locked_fields and best.get("author"):
                updates.append("author = ?")
                params.append(best["author"])

            if "publisher" not in locked_fields and best.get("publisher"):
                updates.append("publisher = ?")
                params.append(best["publisher"])

            if "publish_year" not in locked_fields and best.get("publish_year"):
                updates.append("publish_year = ?")
                params.append(best["publish_year"])

            if "isbn10" not in locked_fields and best.get("isbn10"):
                updates.append("isbn10 = ?")
                params.append(best["isbn10"])

            if "isbn13" not in locked_fields and best.get("isbn13"):
                updates.append("isbn13 = ?")
                params.append(best["isbn13"])

            if "description" not in locked_fields and best.get("description"):
                updates.append("description = ?")
                params.append(best["description"])

            if "work_id" not in locked_fields and best.get("work_id"):
                updates.append("work_id = ?")
                params.append(best["work_id"])

            updates.append("identification_status = 'identified'")
            updates.append("identification_score = ?")
            params.append(score)

            updates.append("metadata_source = ?")
            params.append(best.get("source"))

            updates.append("metadata_updated_at = CURRENT_TIMESTAMP")
            params.append(book_id)

            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"UPDATE books SET {', '.join(updates)} WHERE id = ?;",
                    params,
                )

            # Classificação taxonômica
            raw_subjects = best.get("subjects", [])
            taxonomy = map_raw_subjects_to_taxonomy(
                best.get("title") or book_dict.get("title"),
                best.get("author") or book_dict.get("author"),
                raw_subjects,
                language=best.get("language"),
            )
            sync_book_taxonomy(book_id, taxonomy, is_manual=False)

            # Capa da edição identificada (apenas se o usuário não definiu capa personalizada 'custom')
            if "cover" not in locked_fields and book_dict.get("cover_type") != "custom":
                cover_url = best.get("cover_url")
                if cover_url:
                    self._download_external_cover_safely(book_id, cover_url)

            # Atualiza FTS5
            self._update_fts_index(book_id)
            self._stats["identified"] += 1

        elif confidence == "needs_review":
            # Correspondência incerta: registra candidatos mas NÃO aplica alterações destrutivas
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE books
                    SET identification_status = 'needs_review', identification_score = ?,
                        metadata_updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?;
                    """,
                    (score, book_id),
                )
            self._stats["needs_review"] += 1

        else:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE books
                    SET identification_status = 'not_found', identification_score = ?,
                        metadata_updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?;
                    """,
                    (score, book_id),
                )
            self._stats["not_found"] += 1

    def _download_external_cover_safely(self, book_id: str, cover_url: str) -> bool:
        """Baixa e valida a imagem de capa de fonte externa sem sobrescrever capas manuais."""
        try:
            req = urllib.request.Request(
                cover_url,
                headers={"User-Agent": "Lectoria-Cover-Agent/1.4.0"},
            )
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                if resp.status == 200:
                    img_bytes = resp.read()
                    ext, mime_type, w, h = validate_cover_image(img_bytes)

                    # Garante pasta de capas
                    config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
                    dest_file = config.COVERS_DIR / f"{book_id}_extracted.{ext}"
                    dest_file.write_bytes(img_bytes)

                    with get_db() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            """
                            UPDATE books
                            SET cover_type = 'extracted', cover_path = ?, cover_updated_at = CURRENT_TIMESTAMP
                            WHERE id = ? AND cover_type != 'custom';
                            """,
                            (str(dest_file), book_id),
                        )
                    return True
        except Exception as e:
            logger.debug("Não foi possível baixar capa para %s de %s: %s", book_id, cover_url, e)
        return False

    def _update_fts_index(self, book_id: str) -> None:
        """Sincroniza o índice de busca textual FTS5 com os novos metadados."""
        try:
            with get_db() as conn:
                cursor = conn.cursor()
                # Recupera metadados e assuntos agregados
                cursor.execute(
                    """
                    SELECT b.id, b.title, b.author, b.description, b.publisher,
                           GROUP_CONCAT(s.name, ' ') as subjects_str
                    FROM books b
                    LEFT JOIN book_subjects bs ON b.id = bs.book_id
                    LEFT JOIN subjects s ON bs.subject_id = s.id
                    WHERE b.id = ?
                    GROUP BY b.id;
                    """,
                    (book_id,),
                )
                row = cursor.fetchone()
                if row:
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO books_fts (book_id, title, author, description, publisher, subjects)
                        VALUES (?, ?, ?, ?, ?, ?);
                        """,
                        (
                            row["id"],
                            row["title"] or "",
                            row["author"] or "",
                            row["description"] or "",
                            row["publisher"] or "",
                            row["subjects_str"] or "",
                        ),
                    )
        except Exception as e:
            logger.debug("Falha ao atualizar FTS5 para %s: %s", book_id, e)


# Instância singleton do enriquecedor em segundo plano
enricher = BackgroundEnricher()
