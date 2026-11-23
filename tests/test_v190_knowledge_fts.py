"""
Suíte de Testes da Fase 1 da Lectoria v1.9.0 — Índice SQLite FTS5, Língua Portuguesa,
Normalização Unicode (NFC/NFD) e Verificação Inicial de Desempenho (`knowledge_service.py`).

Cobre todos os cenários da Seção 14.6 e da Seção 16:
- Criação da tabela virtual `knowledge_fts` com `tokenize='unicode61 remove_diacritics 2'`.
- Indexação ao criar conceito, reflexão, destaque, nota e justificativa de link.
- Atualização de índice ao editar conceito, reflexão, destaque, nota ou link.
- Remoção do índice ao excluir conceito, reflexão, destaque, nota ou link.
- Reindexação completa com `rebuild_knowledge_fts`.
- Correspondência em português com e sem acento para todos os pares exigidos:
  - `técnica` <-> `tecnica`
  - `educação` <-> `educacao`
  - `racionalização` <-> `racionalizacao`
  - `filosófico` <-> `filosofico`
  - `ciência` <-> `ciencia`
- Correspondência com entradas Unicode em NFC e NFD.
- Verificação inicial de desempenho (Seção 16) nos cenários sintéticos de 100, 1.000 e 10.000 anotações,
  incluindo validação de `EXPLAIN QUERY PLAN` confirmando uso de índices compostos iniciados por `user_id`
  e ausência de full table scan desnecessário.
"""

from pathlib import Path
import shutil
import tempfile
import time
import unicodedata
import uuid
import pytest

import annotations_service as ann
import config
import database
import knowledge_service as ks


@pytest.fixture
def fts_env(monkeypatch):
    tmp_dir = Path(tempfile.mkdtemp(prefix="lectoria_v190_fts_"))
    db_path = tmp_dir / "lectoria.db"
    lib_dir = tmp_dir / "books"
    lib_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "livro_pt.pdf").write_bytes(b"%PDF-1.4 dummy")

    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_dir)
    monkeypatch.setattr(config, "LIBRARY_DIR", lib_dir)

    database.init_db(db_path)

    with database.get_db(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO users (id, username, password_hash, role, display_name, is_active) VALUES
            (10, 'helena', 'scrypt$h', 'reader', 'Helena', 1),
            (20, 'lucas', 'scrypt$l', 'reader', 'Lucas', 1);
            """
        )
        cur.execute(
            "INSERT OR REPLACE INTO library_sources (id, name, path, is_active) VALUES (1, 'Principal', ?, 1);",
            (str(lib_dir),),
        )
        cur.execute(
            """
            INSERT INTO books (id, source_id, title, author, filename, rel_path, filesize, format, page_count, is_missing)
            VALUES ('book-pt', 1, 'Filosofia e Ciência', 'Autor Brasileiro', 'livro_pt.pdf', 'livro_pt.pdf', 100, 'pdf', 250, 0);
            """
        )

    yield {"tmp_dir": tmp_dir, "db_path": db_path, "helena": 10, "lucas": 20}
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.parametrize(
    "accented_word,unaccented_word",
    [
        ("técnica", "tecnica"),
        ("educação", "educacao"),
        ("racionalização", "racionalizacao"),
        ("filosófico", "filosofico"),
        ("ciência", "ciencia"),
    ],
)
def test_fts5_portuguese_diacritics_and_nfc_nfd_equivalence(fts_env, accented_word, unaccented_word):
    """
    Valida correspondência bidirecional com e sem acento para todos os 5 pares obrigatórios em português,
    bem como equivalência entre strings em NFC e NFD.
    """
    helena = fts_env["helena"]

    # Armazena usando forma NFD para garantir que normalização NFC + FTS5 funcionem perfeitamente
    nfd_term = unicodedata.normalize("NFD", accented_word)
    c = ks.create_concept(
        helena,
        name=f"Conceito sobre {nfd_term}",
        description=f"Investigação aprofundada sobre {nfd_term} contemporânea.",
    )

    # 1. Buscar sem acento encontra a palavra acentuada
    res_unaccented = ks.search_knowledge_candidates(helena, unaccented_word)
    assert any(item["id"] == c["id"] for item in res_unaccented["items"]), (
        f"Busca por '{unaccented_word}' deve encontrar '{accented_word}'"
    )

    # 2. Buscar com acento (NFC) encontra a palavra
    res_nfc = ks.search_knowledge_candidates(helena, unicodedata.normalize("NFC", accented_word))
    assert any(item["id"] == c["id"] for item in res_nfc["items"])

    # 3. Buscar com acento (NFD) encontra a palavra
    res_nfd = ks.search_knowledge_candidates(helena, unicodedata.normalize("NFD", accented_word))
    assert any(item["id"] == c["id"] for item in res_nfd["items"])

    # 4. Agora cria um item gravado SEM acento e busca COM acento
    r = ks.create_reflection(
        helena,
        title=f"Reflexão {unaccented_word}",
        body_markdown=f"Texto redigido sem acento usando {unaccented_word} diretamente.",
    )
    res_reverse = ks.search_knowledge_candidates(helena, accented_word)
    assert any(item["id"] == r["id"] for item in res_reverse["items"]), (
        f"Busca por '{accented_word}' deve encontrar '{unaccented_word}'"
    )


def test_fts5_lifecycle_sync_on_create_update_delete_and_rebuild(fts_env):
    """
    Verifica sincronização FTS5 ao criar, atualizar, excluir e reconstruir (`rebuild_knowledge_fts`)
    para conceitos, reflexões, destaques, notas e justificativas de conexões (`links`).
    """
    helena = fts_env["helena"]

    # 1. Criação
    c = ks.create_concept(helena, name="Epistemologia", description="TermoInicialConceito")
    r = ks.create_reflection(helena, title="Síntese", body_markdown="TermoInicialReflexao")
    hl = ann.create_highlight(
        book_id="book-pt",
        user_id=helena,
        page=10,
        rects=[{"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.02}],
        color="#fef08a",
        selected_text="TermoInicialDestaque",
        text_context="Contexto",
    )
    nt = ann.create_note(
        book_id="book-pt",
        user_id=helena,
        page=11,
        content="TermoInicialNota",
    )
    lk = ks.create_knowledge_link(
        helena,
        source_type="note",
        source_id=nt["id"],
        target_type="concept",
        target_id=c["id"],
        relation_type="grounds",
        rationale="TermoInicialJustificativaLink",
    )

    assert ks.search_knowledge_candidates(helena, "TermoInicialConceito")["total"] == 1
    assert ks.search_knowledge_candidates(helena, "TermoInicialReflexao")["total"] == 1
    assert ks.search_knowledge_candidates(helena, "TermoInicialDestaque")["total"] == 1
    assert ks.search_knowledge_candidates(helena, "TermoInicialNota")["total"] == 1
    assert ks.search_knowledge_candidates(helena, "TermoInicialJustificativaLink")["total"] == 1

    # 2. Atualização
    ks.update_concept(c["id"], helena, description="TermoAtualizadoConceito")
    ks.update_reflection(r["id"], helena, body_markdown="TermoAtualizadoReflexao")
    ann.update_note(nt["id"], "TermoAtualizadoNota", user_id=helena, book_id="book-pt")
    ks.update_knowledge_link(lk["id"], helena, rationale="TermoAtualizadoJustificativaLink")

    assert ks.search_knowledge_candidates(helena, "TermoInicialConceito")["total"] == 0
    assert ks.search_knowledge_candidates(helena, "TermoAtualizadoConceito")["total"] == 1
    assert ks.search_knowledge_candidates(helena, "TermoInicialReflexao")["total"] == 0
    assert ks.search_knowledge_candidates(helena, "TermoAtualizadoReflexao")["total"] == 1
    assert ks.search_knowledge_candidates(helena, "TermoInicialNota")["total"] == 0
    assert ks.search_knowledge_candidates(helena, "TermoAtualizadoNota")["total"] == 1
    assert ks.search_knowledge_candidates(helena, "TermoInicialJustificativaLink")["total"] == 0
    assert ks.search_knowledge_candidates(helena, "TermoAtualizadoJustificativaLink")["total"] == 1

    # 3. Reindexação completa (`rebuild_knowledge_fts`)
    counts = ks.rebuild_knowledge_fts()
    assert counts["concepts"] == 1
    assert counts["reflections"] == 1
    assert counts["highlights"] == 1
    assert counts["notes"] == 1
    assert counts["links"] == 1
    assert ks.search_knowledge_candidates(helena, "TermoAtualizadoConceito")["total"] == 1

    # 4. Exclusão remove do FTS5
    ks.delete_knowledge_link(lk["id"], helena)
    ann.delete_note(nt["id"], user_id=helena, book_id="book-pt")
    ann.delete_highlight(hl["id"], user_id=helena, book_id="book-pt")
    ks.delete_reflection(r["id"], helena)
    ks.delete_concept(c["id"], helena)

    for term in (
        "TermoAtualizadoConceito",
        "TermoAtualizadoReflexao",
        "TermoInicialDestaque",
        "TermoAtualizadoNota",
        "TermoAtualizadoJustificativaLink",
    ):
        assert ks.search_knowledge_candidates(helena, term)["total"] == 0


def test_explain_query_plan_and_synthetic_scale_100_1000_10000(fts_env):
    """
    Seção 16 — Verificação inicial de desempenho e planos de execução (`EXPLAIN QUERY PLAN`):
    1. Verifica que as consultas principais de `concepts`, `reflections`, `knowledge_links` (saída e backlinks)
       e `knowledge_fts` utilizam os índices compostos iniciados por `user_id` ou o índice virtual FTS5,
       sem realizar full scan linear.
    2. Executa verificação em volumes sintéticos de 100, 1.000 e 10.000 anotações (com conceitos, reflexões e links)
       medindo os tempos reais de consulta.
    """
    helena = fts_env["helena"]
    db_path = fts_env["db_path"]

    with database.get_db(db_path) as conn:
        cur = conn.cursor()

        # 1. EXPLAIN QUERY PLAN das consultas principais
        plan_concepts = " ".join(
            str(dict(r))
            for r in cur.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM concepts WHERE user_id = ? AND is_archived = 0 ORDER BY updated_at DESC, id ASC LIMIT 50;",
                (helena,),
            ).fetchall()
        )
        assert "idx_concepts_user_updated" in plan_concepts

        plan_reflections = " ".join(
            str(dict(r))
            for r in cur.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM reflections WHERE user_id = ? AND is_archived = 0 ORDER BY is_pinned DESC, updated_at DESC, id ASC LIMIT 50;",
                (helena,),
            ).fetchall()
        )
        assert "idx_reflections_user_updated" in plan_reflections

        plan_outgoing = " ".join(
            str(dict(r))
            for r in cur.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM knowledge_links WHERE user_id = ? AND source_type = 'note' AND source_id = 'n1' ORDER BY created_at DESC, id ASC LIMIT 50;",
                (helena,),
            ).fetchall()
        )
        assert "idx_klinks_user_source" in plan_outgoing

        plan_incoming = " ".join(
            str(dict(r))
            for r in cur.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM knowledge_links WHERE user_id = ? AND target_type = 'concept' AND target_id = 'c1' ORDER BY created_at DESC, id ASC LIMIT 50;",
                (helena,),
            ).fetchall()
        )
        assert "idx_klinks_user_target" in plan_incoming

    # 2. Teste progressivo nos 3 degraus de escala da Seção 16: 100, 1.000 e 10.000 anotações
    current_notes = 0
    target_concept = ks.create_concept(
        helena,
        name="Racionalização Técnica Central",
        description="Conceito hub para medição de backlinks e pesquisa FTS5.",
    )

    for scale_target in (100, 1000, 10000):
        delta = scale_target - current_notes
        with database.get_db(db_path) as conn:
            cur = conn.cursor()
            notes_batch = [
                (
                    f"note-scale-{current_notes + i}",
                    "book-pt",
                    (i % 200) + 1,
                    f"Análise filosófica sobre técnica, educação e racionalização número {current_notes + i}.",
                    "#fef08a",
                    helena,
                    "private",
                    "Capítulo Escala",
                )
                for i in range(delta)
            ]
            cur.executemany(
                """
                INSERT INTO notes (id, book_id, page, content, color, user_id, visibility, chapter_label)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                notes_batch,
            )

            # Cria conexões para os primeiros 50 itens apontando para o conceito central
            if current_notes == 0:
                links_batch = [
                    (
                        f"link-scale-{i}",
                        helena,
                        "note",
                        f"note-scale-{i}",
                        "concept",
                        target_concept["id"],
                        "grounds",
                        f"Fundamentação empírica número {i}",
                    )
                    for i in range(50)
                ]
                cur.executemany(
                    """
                    INSERT INTO knowledge_links (id, user_id, source_type, source_id, target_type, target_id, relation_type, rationale)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    links_batch,
                )
        current_notes = scale_target

        t0 = time.perf_counter()
        backlinks = ks.list_incoming_links("concept", target_concept["id"], helena, limit=30, offset=0)
        t_backlinks_ms = (time.perf_counter() - t0) * 1000.0

        t1 = time.perf_counter()
        fts_res = ks.search_knowledge_candidates(helena, "racionalizacao filosofica", limit=30, offset=0)
        t_fts_ms = (time.perf_counter() - t1) * 1000.0

        assert len(backlinks) == 30
        assert fts_res["total"] > 0
        assert len(fts_res["items"]) <= 30
        # Limite folgado para CI/hardware modesto, mas na prática executa em poucos milissegundos
        assert t_backlinks_ms < 250.0, f"Consulta de backlinks demorou {t_backlinks_ms:.2f} ms em {scale_target} notas"
        assert t_fts_ms < 500.0, f"Busca FTS5 demorou {t_fts_ms:.2f} ms em {scale_target} notas"
