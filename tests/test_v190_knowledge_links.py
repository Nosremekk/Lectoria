"""
Suíte de Testes da Fase 1 da Lectoria v1.9.0 — Conexões e Backlinks (`knowledge_service.py`).

Cobre todos os cenários da Seção 14.4:
- Criação de vínculo para cada um dos 8 tipos de relação.
- Rejeição de relação inválida.
- Rejeição de auto-referência (`source == target`).
- Rejeição de duplicata exata.
- Permissão de relações distintas ou direção inversa entre os mesmos nós.
- Permissão de ciclos entre nós distintos sem loop infinito.
- Consulta de conexões de saída (`outgoing_links`).
- Consulta de conexões de entrada (`incoming_links`) e backlinks sem duplicar arestas.
- Edição de `relation_type` e `rationale` sem permitir trocar `user_id`, `source` ou `target`.
- Exclusão de conexão própria.
- Bloqueio de edição ou exclusão por outro usuário.
- Concorrência básica na criação de conceitos e conexões sem corrupção nem violação de integridade.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import tempfile
import pytest

import config
import database
import knowledge_service as ks


@pytest.fixture
def links_env(monkeypatch):
    tmp_dir = Path(tempfile.mkdtemp(prefix="lectoria_v190_links_"))
    db_path = tmp_dir / "lectoria.db"
    lib_dir = tmp_dir / "books"
    lib_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "b1.pdf").write_bytes(b"%PDF-1.4 dummy")
    (lib_dir / "b2.epub").write_bytes(b"PK\x03\x04 dummy")

    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_dir)
    monkeypatch.setattr(config, "LIBRARY_DIR", lib_dir)

    database.init_db(db_path)

    with database.get_db(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO users (id, username, password_hash, role, display_name, is_active) VALUES
            (10, 'alice', 'scrypt$a', 'reader', 'Alice', 1),
            (20, 'bob', 'scrypt$b', 'reader', 'Bob', 1);
            """
        )
        cur.execute(
            "INSERT OR REPLACE INTO library_sources (id, name, path, is_active) VALUES (1, 'Principal', ?, 1);",
            (str(lib_dir),),
        )
        cur.execute(
            """
            INSERT INTO books (id, source_id, title, author, filename, rel_path, filesize, format, page_count, is_missing) VALUES
            ('book-1', 1, 'Vigiar e Punir', 'Michel Foucault', 'b1.pdf', 'b1.pdf', 100, 'pdf', 300, 0),
            ('book-2', 1, 'A Sociedade do Espetáculo', 'Guy Debord', 'b2.epub', 'b2.epub', 100, 'epub', 200, 0);
            """
        )
        cur.execute(
            """
            INSERT INTO highlights (id, book_id, page, color, rects_json, selected_text, text_context, user_id, visibility) VALUES
            ('hl-1', 'book-1', 177, '#fef08a', '[]', 'O Panóptico é uma máquina de dissociar o par ver-ser visto.', 'Cap. 3', 10, 'private'),
            ('hl-2', 'book-2', 14, '#fef08a', '[]', 'O espetáculo não é um conjunto de imagens, mas uma relação social.', 'Tese 4', 10, 'private');
            """
        )
        cur.execute(
            """
            INSERT INTO notes (id, book_id, page, highlight_id, content, color, user_id, visibility) VALUES
            ('note-1', 'book-1', 177, 'hl-1', 'Nota sobre vigilância panóptica.', '#fef08a', 10, 'private');
            """
        )

    yield {"tmp_dir": tmp_dir, "db_path": db_path, "alice": 10, "bob": 20}
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_create_all_eight_relation_types_and_reject_invalid(links_env):
    """Cria uma conexão válida para cada um dos 8 tipos fixos de relação (`KD-003`) e rejeita tipos inválidos."""
    alice = links_env["alice"]
    c_main = ks.create_concept(alice, name="Modernidade")

    for idx, rel_type in enumerate(ks.VALID_RELATION_TYPES):
        c_other = ks.create_concept(alice, name=f"Subconceito {idx}")
        link = ks.create_knowledge_link(
            alice,
            source_type="concept",
            source_id=c_main["id"],
            target_type="concept",
            target_id=c_other["id"],
            relation_type=rel_type,
            rationale=f"Justificativa para {rel_type}",
        )
        assert link["relation_type"] == rel_type
        assert link["rationale"] == f"Justificativa para {rel_type}"
        assert link["relation_label"] == ks.RELATION_LABELS_PT[rel_type]["outgoing"]

    with pytest.raises(ks.KnowledgeValidationError):
        ks.create_knowledge_link(
            alice,
            source_type="concept",
            source_id=c_main["id"],
            target_type="book",
            target_id="book-1",
            relation_type="relacao_inexistente",
        )


def test_reject_self_loop_and_duplicate_allow_distinct_relations_and_cycles(links_env):
    """
    - Rejeita auto-referência (`source == target`).
    - Rejeita duplicata exata.
    - Permite outro `relation_type` ou direção inversa entre os mesmos dois nós.
    - Permite ciclos entre nós distintos ($A \\to B \\to C \\to A$) sem loop infinito nas consultas.
    """
    alice = links_env["alice"]
    c_a = ks.create_concept(alice, name="Conceito A")
    c_b = ks.create_concept(alice, name="Conceito B")
    c_c = ks.create_concept(alice, name="Conceito C")

    # Auto-referência bloqueada
    with pytest.raises(ks.KnowledgeValidationError):
        ks.create_knowledge_link(alice, "concept", c_a["id"], "concept", c_a["id"], "relates_to")

    # Criação inicial A -> B (complements)
    ks.create_knowledge_link(alice, "concept", c_a["id"], "concept", c_b["id"], "complements")

    # Duplicata exata A -> B (complements) bloqueada
    with pytest.raises(ks.KnowledgeConflictError):
        ks.create_knowledge_link(alice, "concept", c_a["id"], "concept", c_b["id"], "complements")

    # Relação distinta A -> B (questions) permitida
    ks.create_knowledge_link(alice, "concept", c_a["id"], "concept", c_b["id"], "questions")

    # Direção inversa B -> A (grounds) e ciclo A -> B -> C -> A permitidos
    ks.create_knowledge_link(alice, "concept", c_b["id"], "concept", c_c["id"], "develops")
    ks.create_knowledge_link(alice, "concept", c_c["id"], "concept", c_a["id"], "grounds")

    bundle_a = ks.get_entity_connections_and_backlinks("concept", c_a["id"], alice)
    assert bundle_a["total_outgoing"] == 2
    assert bundle_a["total_incoming"] == 1
    assert bundle_a["incoming_backlinks"][0]["source_id"] == c_c["id"]
    assert bundle_a["incoming_backlinks"][0]["direction"] == "incoming"


def test_bidirectional_query_without_edge_duplication_and_update_constraints(links_env):
    """
    - Verifica que uma única aresta persistida (`note-1` -> `hl-2`) aparece como saída em `note-1`
      e como backlink de entrada em `hl-2`.
    - Verifica que `update_knowledge_link` atualiza apenas `relation_type` e `rationale`, rejeitando
      alteração de `user_id`, `source_id` ou `target_id`.
    """
    alice = links_env["alice"]
    bob = links_env["bob"]

    link = ks.create_knowledge_link(
        alice,
        source_type="note",
        source_id="note-1",
        target_type="highlight",
        target_id="hl-2",
        relation_type="complements",
        rationale="Vigilância panóptica e espetáculo",
    )
    lid = link["id"]

    out_from_note = ks.list_outgoing_links("note", "note-1", alice)
    assert len(out_from_note) == 1
    assert out_from_note[0]["id"] == lid
    assert out_from_note[0]["direction"] == "outgoing"
    assert out_from_note[0]["relation_label"] == "Complementa"
    assert out_from_note[0]["counterpart"]["id"] == "hl-2"

    in_to_hl2 = ks.list_incoming_links("highlight", "hl-2", alice)
    assert len(in_to_hl2) == 1
    assert in_to_hl2[0]["id"] == lid
    assert in_to_hl2[0]["direction"] == "incoming"
    assert in_to_hl2[0]["relation_label"] == "Complementado por"
    assert in_to_hl2[0]["counterpart"]["id"] == "note-1"

    # Atualização válida de relation_type e rationale
    updated = ks.update_knowledge_link(
        lid,
        alice,
        relation_type="contradicts",
        rationale="Tensão entre disciplina invisível e visibilidade total",
    )
    assert updated["relation_type"] == "contradicts"
    assert updated["rationale"] == "Tensão entre disciplina invisível e visibilidade total"

    # Tentativa de alterar extremos ou proprietário deve ser rejeitada
    for forbidden_kw in (
        {"owner_user_id": bob},
        {"source_id": "hl-1"},
        {"target_id": "book-1"},
        {"source_type": "highlight"},
        {"target_type": "book"},
    ):
        with pytest.raises(ks.KnowledgeValidationError):
            ks.update_knowledge_link(lid, alice, **forbidden_kw)

    # Bob não pode ler, editar ou excluir a conexão da Alice
    with pytest.raises(ks.KnowledgeNotFoundError):
        ks.get_knowledge_link(lid, bob)
    with pytest.raises(ks.KnowledgeNotFoundError):
        ks.update_knowledge_link(lid, bob, rationale="Invasão")
    with pytest.raises(ks.KnowledgeNotFoundError):
        ks.delete_knowledge_link(lid, bob)

    # Alice exclui sua conexão
    assert ks.delete_knowledge_link(lid, alice) == {"deleted": True, "id": lid}
    assert ks.list_outgoing_links("note", "note-1", alice) == []


def test_concurrent_creation_of_concepts_and_links_integrity(links_env):
    """Testa concorrência básica na criação de conceitos e conexões em modo SQLite WAL."""
    alice = links_env["alice"]

    def worker(idx: int):
        c = ks.create_concept(alice, name=f"Conceito Concorrente {idx}")
        l = ks.create_knowledge_link(
            alice,
            source_type="concept",
            source_id=c["id"],
            target_type="book",
            target_id="book-1",
            relation_type="references",
            rationale=f"Vínculo concorrente {idx}",
        )
        return c["id"], l["id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(worker, range(12)))

    assert len(results) == 12
    with database.get_db(links_env["db_path"]) as conn:
        cur = conn.cursor()
        assert cur.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
        assert cur.execute("SELECT COUNT(*) FROM concepts WHERE user_id = ?;", (alice,)).fetchone()[0] == 12
        assert cur.execute("SELECT COUNT(*) FROM knowledge_links WHERE user_id = ?;", (alice,)).fetchone()[0] == 12
