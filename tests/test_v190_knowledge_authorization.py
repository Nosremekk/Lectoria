"""
Suíte de Testes da Fase 1 da Lectoria v1.9.0 — Autorização em Tempo de Leitura,
Isolamento Multiusuário e Segurança (`knowledge_service.py`).

Cobre todos os cenários da Seção 14.5:
- Conceito de usuário A invisível para usuário B (e para admin).
- Reflexão de usuário A invisível para usuário B.
- Conexão de usuário A invisível para usuário B.
- Usuário A conecta sua nota ao destaque público do usuário B.
- Usuário B não vê conceitos, reflexões, conexões nem backlinks privados de A sobre o destaque público de B.
- Usuário B altera seu destaque público para privado (`public -> private`).
- Usuário A consulta conexões, backlinks e referências após a revogação: o destaque de B aparece apenas como
  indisponível (`available = False`), sem vazar texto, capítulo, página, CFI, autor ou metadados, mas a
  justificativa autoral (`rationale`) de A permanece preservada.
- Usuário B republica o destaque (`private -> public`) e a resolução volta a funcionar normalmente.
- Livro ou fonte é desativado/removido (`is_missing = 1` ou `is_active = 0`) e anotações de terceiros naquele
  livro deixam de ser resolvidas.
- Anotação referenciada de terceiro é excluída e o sistema não falha nem vaza dados nem apaga o link/rationale de A.
- Tentativa de vincular anotação privada de outro usuário falha sem revelar detalhes.
- Tentativa de vincular livro sem permissão (fonte inativa / ausente) falha.
- Tentativa de injeção SQL em nomes, descrições, títulos, corpos, justificativas, filtros e buscas.
- Teste com índice FTS5 deliberadamente dessincronizado provando que a validação relacional impede vazamento.
"""

from pathlib import Path
import shutil
import tempfile
import pytest

import annotations_service as ann
import config
import database
import knowledge_service as ks


@pytest.fixture
def auth_env(monkeypatch):
    tmp_dir = Path(tempfile.mkdtemp(prefix="lectoria_v190_auth_"))
    db_path = tmp_dir / "lectoria.db"
    lib_dir = tmp_dir / "books"
    lib_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "casmurro.pdf").write_bytes(b"%PDF-1.4 dummy")
    (lib_dir / "restrito.pdf").write_bytes(b"%PDF-1.4 dummy")

    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_dir)
    monkeypatch.setattr(config, "LIBRARY_DIR", lib_dir)

    database.init_db(db_path)

    with database.get_db(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO users (id, username, password_hash, role, display_name, is_active) VALUES
            (10, 'alice', 'scrypt$a', 'reader', 'Alice Lemos', 1),
            (20, 'bob', 'scrypt$b', 'reader', 'Bob Ferreira', 1),
            (30, 'admin', 'scrypt$adm', 'admin', 'Admin Geral', 1),
            (40, 'inativo', 'scrypt$i', 'reader', 'Leitor Inativo', 0);
            """
        )
        cur.execute(
            "INSERT OR REPLACE INTO library_sources (id, name, path, is_active) VALUES (1, 'Ativa', ?, 1);",
            (str(lib_dir),),
        )
        cur.execute(
            "INSERT OR REPLACE INTO library_sources (id, name, path, is_active) VALUES (2, 'Inativa', ?, 0);",
            (str(lib_dir / "inativa"),),
        )
        cur.execute(
            """
            INSERT INTO books (id, source_id, title, author, filename, rel_path, filesize, format, page_count, is_missing) VALUES
            ('book-shared', 1, 'Dom Casmurro', 'Machado de Assis', 'casmurro.pdf', 'casmurro.pdf', 100, 'pdf', 200, 0),
            ('book-inactive', 2, 'Obra Fonte Inativa', 'Autor Secreto', 'restrito.pdf', 'restrito.pdf', 100, 'pdf', 100, 0);
            """
        )
        # Bob cria um destaque PÚBLICO e uma nota PRIVADA em book-shared
        cur.execute(
            """
            INSERT INTO highlights (id, book_id, page, color, rects_json, selected_text, text_context, user_id, visibility, chapter_label)
            VALUES ('hl-bob-pub', 'book-shared', 32, '#fef08a', '[]',
                    'TRECHO_CONFIDENCIAL_APOS_REVOGACAO_OLHOS_DE_RESSACA', 'Cap. 32', 20, 'public', 'Capítulo XXXII');
            """
        )
        cur.execute(
            """
            INSERT INTO notes (id, book_id, page, highlight_id, content, color, user_id, visibility, chapter_label)
            VALUES ('note-bob-priv', 'book-shared', 33, NULL,
                    'SEGREDO_PRIVADO_DO_BOB_NUNCA_VAZAR', '#fef08a', 20, 'private', 'Capítulo XXXIII');
            """
        )
        # Alice cria uma nota privada própria em book-shared
        cur.execute(
            """
            INSERT INTO notes (id, book_id, page, highlight_id, content, color, user_id, visibility, chapter_label)
            VALUES ('note-alice-own', 'book-shared', 12, NULL,
                    'Análise privada da Alice sobre o narrador Bento Santiago.', '#fef08a', 10, 'private', 'Capítulo XII');
            """
        )

    yield {"tmp_dir": tmp_dir, "db_path": db_path, "alice": 10, "bob": 20, "admin": 30, "inactive": 40}
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_cross_user_public_annotation_link_revocation_republish_and_isolation(auth_env):
    """
    Testa o ciclo completo de segurança em tempo de leitura:
    1. Alice conecta sua nota privada (`note-alice-own`) e seu conceito privado (`Narrador Não Confiável`)
       ao destaque público de Bob (`hl-bob-pub`), escrevendo uma justificativa autoral (`rationale`).
    2. Bob consulta os backlinks do seu destaque público `hl-bob-pub` e NÃO vê nenhuma conexão, conceito ou nota privada da Alice.
    3. Bob revoga a visibilidade de `hl-bob-pub` de `public` para `private`.
    4. Alice consulta suas conexões: `hl-bob-pub` aparece com `available = False`, `label = 'Referência indisponível'`,
       sem vazar `selected_text`, `page`, `chapter_label`, `book_title` ou autor, mas o `rationale` de Alice continua intacto!
    5. Bob republica `hl-bob-pub` (`private -> public`): a consulta de Alice volta a resolver o destaque normalmente.
    """
    alice = auth_env["alice"]
    bob = auth_env["bob"]

    c_alice = ks.create_concept(alice, name="Narrador Não Confiável", description="Estudo privado da Alice")
    r_alice = ks.create_reflection(alice, title="Reflexão da Alice sobre Bento", body_markdown="Texto autoral da Alice")

    link_note_to_bob = ks.create_knowledge_link(
        alice,
        source_type="note",
        source_id="note-alice-own",
        target_type="highlight",
        target_id="hl-bob-pub",
        relation_type="complements",
        rationale="Justificativa autoral da Alice que nunca deve ser perdida.",
    )
    link_refl_to_bob = ks.create_knowledge_link(
        alice,
        source_type="reflection",
        source_id=r_alice["id"],
        target_type="highlight",
        target_id="hl-bob-pub",
        relation_type="references",
        rationale="Citação na reflexão da Alice.",
    )

    # Enquanto público, Alice vê os dados do destaque público de Bob
    assert link_note_to_bob["target"]["available"] is True
    assert "TRECHO_CONFIDENCIAL_APOS_REVOGACAO" in link_note_to_bob["target"]["excerpt"]

    # Bob consulta conexões e backlinks de hl-bob-pub: ZERO vazamento da rede privada da Alice
    bob_bundle = ks.get_entity_connections_and_backlinks("highlight", "hl-bob-pub", bob)
    assert bob_bundle["total_incoming"] == 0
    assert bob_bundle["incoming_backlinks"] == []

    # Bob revoga a visibilidade de hl-bob-pub para 'private'
    ann.set_highlight_visibility("hl-bob-pub", "private", bob, book_id="book-shared")

    # Alice consulta sua conexão e as referências da sua reflexão após a revogação
    after_revocation = ks.get_knowledge_link(link_note_to_bob["id"], alice)
    target = after_revocation["target"]
    assert target["available"] is False
    assert target["status"] == "unavailable"
    assert target["label"] == "Referência indisponível"
    assert target["excerpt"] is None
    assert target["title"] is None
    assert target["page"] is None
    assert target["chapter_label"] is None
    assert "TRECHO_CONFIDENCIAL_APOS_REVOGACAO" not in str(after_revocation)
    # A justificativa autoral de Alice foi 100% preservada
    assert after_revocation["rationale"] == "Justificativa autoral da Alice que nunca deve ser perdida."

    refl_refs = ks.get_reflection_references(r_alice["id"], alice)
    assert len(refl_refs) == 1
    assert refl_refs[0]["target"]["available"] is False
    assert "TRECHO_CONFIDENCIAL_APOS_REVOGACAO" not in str(refl_refs)

    # Bob republica o destaque ('private' -> 'public'): Alice volta a resolver o alvo
    ann.set_highlight_visibility("hl-bob-pub", "public", bob, book_id="book-shared")
    after_republish = ks.get_knowledge_link(link_note_to_bob["id"], alice)
    assert after_republish["target"]["available"] is True
    assert "TRECHO_CONFIDENCIAL_APOS_REVOGACAO" in after_republish["target"]["excerpt"]
    assert after_republish["target"]["page"] == 32


def test_book_revocation_and_third_party_deletion_preserve_alice_rationale_safely(auth_env):
    """
    - Se a fonte do livro for desativada (`is_active = 0`) ou o livro ficar ausente (`is_missing = 1`),
      o destaque de terceiro deixa de ser resolvido (`available = False`).
    - Se Bob excluir fisicamente o seu destaque `hl-bob-pub`, o trigger NÃO apaga a conexão nem o `rationale`
      pertencente à Alice (`user_id = 10 != 20`), e Alice vê `available = False` com seu `rationale` preservado.
    """
    alice = auth_env["alice"]
    bob = auth_env["bob"]

    link = ks.create_knowledge_link(
        alice,
        source_type="note",
        source_id="note-alice-own",
        target_type="highlight",
        target_id="hl-bob-pub",
        relation_type="grounds",
        rationale="Argumento central da Alice sobre o capítulo 32.",
    )

    # 1. Simula livro marcado como is_missing = 1
    with database.get_db(auth_env["db_path"]) as conn:
        conn.cursor().execute("UPDATE books SET is_missing = 1 WHERE id = 'book-shared';")

    when_missing = ks.get_knowledge_link(link["id"], alice)
    assert when_missing["target"]["available"] is False
    assert "TRECHO_CONFIDENCIAL" not in str(when_missing)
    assert when_missing["rationale"] == "Argumento central da Alice sobre o capítulo 32."

    # Restaura o livro
    with database.get_db(auth_env["db_path"]) as conn:
        conn.cursor().execute("UPDATE books SET is_missing = 0 WHERE id = 'book-shared';")

    assert ks.get_knowledge_link(link["id"], alice)["target"]["available"] is True

    # 2. Bob exclui fisicamente seu destaque público hl-bob-pub
    ann.delete_highlight("hl-bob-pub", user_id=bob, book_id="book-shared")

    after_delete = ks.get_knowledge_link(link["id"], alice)
    assert after_delete["target"]["available"] is False
    assert after_delete["target"]["label"] == "Referência indisponível"
    assert after_delete["rationale"] == "Argumento central da Alice sobre o capítulo 32."


def test_cannot_link_private_third_party_annotation_or_inaccessible_book(auth_env):
    """
    Bloqueia tentativas de criar conexão para nota privada de outro usuário (`note-bob-priv`),
    conceito/reflexão de outro usuário ou livro de fonte inativa (`book-inactive`).
    """
    alice = auth_env["alice"]
    bob = auth_env["bob"]
    c_alice = ks.create_concept(alice, name="Conceito Alice")
    c_bob = ks.create_concept(bob, name="Conceito Secreto Bob")

    # Tentar vincular nota privada do Bob falha com 404 genérico (KnowledgeNotFoundError)
    with pytest.raises(ks.KnowledgeNotFoundError):
        ks.create_knowledge_link(
            alice,
            source_type="concept",
            source_id=c_alice["id"],
            target_type="note",
            target_id="note-bob-priv",
            relation_type="references",
        )

    # Tentar vincular conceito privado do Bob falha com 404 genérico
    with pytest.raises(ks.KnowledgeNotFoundError):
        ks.create_knowledge_link(
            alice,
            source_type="concept",
            source_id=c_alice["id"],
            target_type="concept",
            target_id=c_bob["id"],
            relation_type="relates_to",
        )

    # Tentar vincular livro de fonte inativa falha com 404 genérico
    with pytest.raises(ks.KnowledgeNotFoundError):
        ks.create_knowledge_link(
            alice,
            source_type="concept",
            source_id=c_alice["id"],
            target_type="book",
            target_id="book-inactive",
            relation_type="references",
        )

    # Usuário inativo é bloqueado em todas as operações
    with pytest.raises(ks.KnowledgePermissionError):
        ks.list_concepts(auth_env["inactive"])


def test_deliberately_desynchronized_fts5_index_never_leaks_unauthorized_records(auth_env):
    """
    Injeta deliberadamente linhas espúrias/obsoletas em `knowledge_fts` apontando para:
    - A nota privada do Bob (`note-bob-priv`);
    - Um conceito privado do Bob;
    - Um destaque público do Bob cujo livro foi desativado ou cuja visibilidade foi revogada;
    - Um registro já excluído da tabela relacional.
    Prova que `search_knowledge_candidates` para Alice descarta 100% desses candidatos na validação relacional.
    """
    alice = auth_env["alice"]
    bob = auth_env["bob"]

    c_bob = ks.create_concept(bob, name="Segredo Conceitual Ultrassecreto", description="PALAVRA_CHAVE_VAZAMENTO_FTS")
    c_alice = ks.create_concept(alice, name="Conceito Legítimo da Alice", description="PALAVRA_CHAVE_VAZAMENTO_FTS")

    # Revoga o destaque de Bob para privado
    ann.set_highlight_visibility("hl-bob-pub", "private", bob, book_id="book-shared")

    # Injeta entradas maliciosas/obsoletas diretamente na tabela virtual knowledge_fts (simulando índice sujo)
    with database.get_db(auth_env["db_path"]) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO knowledge_fts (entity_type, entity_id, user_id, book_id, title, body, tags)
            VALUES
            ('note', 'note-bob-priv', '10', 'book-shared', 'Falso', 'PALAVRA_CHAVE_VAZAMENTO_FTS SEGREDO_PRIVADO_DO_BOB_NUNCA_VAZAR', ''),
            ('highlight', 'hl-bob-pub', '10', 'book-shared', 'Falso', 'PALAVRA_CHAVE_VAZAMENTO_FTS TRECHO_CONFIDENCIAL_APOS_REVOGACAO', ''),
            ('concept', ?, '10', '', 'Falso', 'PALAVRA_CHAVE_VAZAMENTO_FTS', ''),
            ('concept', 'id-fantasma-excluido', '10', '', 'Fantasma', 'PALAVRA_CHAVE_VAZAMENTO_FTS', '');
            """,
            (c_bob["id"],),
        )

    res = ks.search_knowledge_candidates(alice, "PALAVRA_CHAVE_VAZAMENTO_FTS")
    assert res["total"] == 1
    assert len(res["items"]) == 1
    assert res["items"][0]["id"] == c_alice["id"]
    assert "SEGREDO_PRIVADO_DO_BOB" not in str(res)
    assert "TRECHO_CONFIDENCIAL_APOS_REVOGACAO" not in str(res)
    assert "Ultrassecreto" not in str(res)


def test_sql_injection_payloads_safely_handled_in_all_fields_and_searches(auth_env):
    """Garante que payloads clássicos de SQL Injection e operadores FTS5 não quebram nem corrompem o banco."""
    alice = auth_env["alice"]
    sqli = "'; DROP TABLE concepts; DROP TABLE knowledge_links; -- \"OR 1=1\" NEAR/2 ("

    c = ks.create_concept(alice, name=f"Conceito {sqli[:40]}", description=sqli, tags=["tag' OR '1'='1"])
    r = ks.create_reflection(alice, title=f"Reflexão {sqli[:40]}", body_markdown=sqli)
    l = ks.create_knowledge_link(alice, "reflection", r["id"], "concept", c["id"], "relates_to", rationale=sqli)

    assert ks.get_concept(c["id"], alice)["description"] == sqli
    assert ks.get_reflection(r["id"], alice)["body_markdown"] == sqli
    assert ks.get_knowledge_link(l["id"], alice)["rationale"] == sqli

    # Buscas com payloads maliciosos não levantam erro SQL e mantêm tabelas intactas
    assert ks.list_concepts(alice, search=sqli, tag=sqli)["total"] >= 0
    assert ks.list_reflections(alice, search=sqli, tag=sqli)["total"] >= 0
    assert ks.search_knowledge_candidates(alice, sqli)["total"] >= 0


def test_own_annotation_subject_to_book_authorization_on_source_deactivation(auth_env):
    """
    Seção 4.1 (Auditoria Fase 2.0):
    Verifica que até mesmo as anotações PRÓPRIAS do usuário (`note-alice-own`, `user_id = 10`)
    continuam sujeitas à autorização canônica sobre o livro associado (`_verify_book_access`).
    Se a fonte do livro (`source_id = 1`) for desativada (`is_active = 0`) ou o livro ficar ausente
    (`is_missing = 1`), `resolve_knowledge_entity` bloqueia nova vinculação (`404`) e mascara a
    anotação própria existente como indisponível (`available = False`) em links existentes,
    preservando o conceito e a justificativa autoral.
    """
    alice = auth_env["alice"]
    c_alice = ks.create_concept(alice, name="Conceito Próprio")

    # Enquanto a fonte 1 está ativa, Alice vincula sua própria nota ao seu conceito
    link = ks.create_knowledge_link(
        alice,
        source_type="concept",
        source_id=c_alice["id"],
        target_type="note",
        target_id="note-alice-own",
        relation_type="grounds",
        rationale="Minha justificativa sobre minha própria nota.",
    )
    assert link["target"]["available"] is True
    assert link["target"]["is_own"] is True

    # Desativa a fonte 1 (onde está book-shared)
    with database.get_db(auth_env["db_path"]) as conn:
        conn.cursor().execute("UPDATE library_sources SET is_active = 0 WHERE id = 1;")

    # 1. Na leitura da conexão existente, a nota própria no livro de fonte desativada fica indisponível
    fetched = ks.get_knowledge_link(link["id"], alice)
    assert fetched["target"]["available"] is False
    assert fetched["target"]["status"] == "unavailable"
    assert fetched["target"]["excerpt"] is None
    assert fetched["rationale"] == "Minha justificativa sobre minha própria nota."

    # 2. Tentativa de criar nova conexão com a nota própria enquanto o livro está em fonte inativa falha
    c_alice_2 = ks.create_concept(alice, name="Outro Conceito")
    with pytest.raises(ks.KnowledgeNotFoundError):
        ks.create_knowledge_link(
            alice,
            source_type="concept",
            source_id=c_alice_2["id"],
            target_type="note",
            target_id="note-alice-own",
            relation_type="relates_to",
        )

    # Reativa a fonte 1 e confirma restauração imediata
    with database.get_db(auth_env["db_path"]) as conn:
        conn.cursor().execute("UPDATE library_sources SET is_active = 1 WHERE id = 1;")
    assert ks.get_knowledge_link(link["id"], alice)["target"]["available"] is True


def test_fts5_private_concepts_of_other_users_do_not_exhaust_candidate_pagination(auth_env):
    """
    Seção 4.2 (Auditoria Fase 2.0):
    Garante que mesmo se outro usuário (Bob) possuir centenas de conceitos privados contendo
    um mesmo termo raro, eles são filtrados na consulta FTS5 (`user_id = ?`) e jamais
    consomem ou esgotam os resultados nem alteram o `total` autorizado de Alice, além de
    nunca expor scores brutos de `bm25()`.
    """
    alice = auth_env["alice"]
    bob = auth_env["bob"]

    for i in range(60):
        ks.create_concept(bob, name=f"TermoRaroPaginacao {i}", description="TermoRaroPaginacao privado do Bob")

    c_alice = ks.create_concept(alice, name="TermoRaroPaginacao Alice", description="TermoRaroPaginacao exclusivo")

    res = ks.search_knowledge_candidates(alice, "TermoRaroPaginacao", limit=10, offset=0)
    assert res["total"] == 1
    assert res["has_more"] is False
    assert len(res["items"]) == 1
    assert res["items"][0]["id"] == c_alice["id"]
    assert "rank" not in res["items"][0]
    assert "bm25" not in str(res["items"][0])
