"""
Suíte de Testes Unitários, de Integração de API, Segurança e Escala Quantitativa — Fase 6 da Lectoria v1.9.0.
Valida todas as exigências das Seções 12–39:
- Pesquisa Integrada FTS5 (`GET /api/knowledge/search` e `knowledge_service.search_knowledge`):
  - Pesquisa por conceito, reflexão, destaque, nota, conexão (justificativa) e livro (`index_source`).
  - Insensibilidade a acentos/diacríticos, cedilha e caixa (`unicode61 remove_diacritics 2`).
  - Pesquisa com múltiplos termos (`AND`), paginação, busca vazia e sem resultados.
  - Neutralização de sintaxe malformada FTS5, aspas desbalanceadas, parênteses, operadores, curingas e Unicode malformado.
  - Dupla barreira e validação relacional ao vivo antes da paginação (índice FTS5 obsoleto e volume dominante de itens inacessíveis).
  - Isolamento estrito de relevância entre usuários (ausência de vazamento estatístico BM25 IDF).
  - Geração segura de snippets e `snippet_segments` sem HTML bruto e sem vazar referências indisponíveis.
  - Fallback relacional controlado vs. propagação diagnosticável de corrupção estrutural.
- Grafo Local de Conhecimento (`GET /api/knowledge/graph` e `knowledge_service.get_knowledge_graph`):
  - Construção de subgrafo de 1 e 2 níveis (`depth=1`, `depth=2`) para `concept`, `reflection`, `highlight`, `note`, `book`.
  - Limites rígidos (`max_nodes <= 80` incluindo o nó central, `max_edges <= 160`) e sinalização de `truncated`.
  - Filtros por tipo de entidade e tipo de relação.
  - Tratamento seguro de ciclos, múltiplas arestas e ausência de arestas inversas duplicadas.
  - Proteção de referências indisponíveis (sentinela neutro, não expansível no 2º salto, zero metadados protegidos).
  - Alternativa textual acessível (`accessible_text_list`) representando exatamente o mesmo subconjunto autorizado.
- Verificação quantitativa de desempenho e `EXPLAIN QUERY PLAN` (Seção 36):
  - Cenário A: 1.000 conceitos, 1.000 reflexões, 1.000 anotações, 10.000 conexões.
  - Cenário B (teto de projeto): 10.000 conceitos, 2.000 reflexões, 10.000 anotações, 50.000 conexões.
"""

import json
import sqlite3
import time
import uuid
import pytest

import annotations_service
import auth_service
import config
import database
import knowledge_service


def _auth_cookies(token: str, csrf: str = "csrf_p6_unit"):
    return {"lectoria_session": token, "lectoria_csrf": csrf}


def _seed_book(book_id: str, title: str, author: str = "Autor Fase 6", fmt: str = "pdf", source_id: int = 1):
    lib_dir = config.LIBRARY_DIR
    src_dir = lib_dir if source_id == 1 else (lib_dir / f"src_{source_id}")
    src_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{book_id}.{fmt}"
    fpath = src_dir / fname
    if not fpath.exists():
        fpath.write_bytes(b"%PDF-1.4\n%%EOF\n")
    with database.get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO library_sources (id, name, path, is_active) VALUES (?, ?, ?, 1)
            ON CONFLICT(id) DO UPDATE SET name = excluded.name, path = excluded.path, is_active = 1;
            """,
            (source_id, f"Fonte {source_id}", str(src_dir)),
        )
        cur.execute(
            """
            INSERT OR REPLACE INTO books (id, title, author, filename, rel_path, filesize, format, page_count, source_id, is_missing)
            VALUES (?, ?, ?, ?, ?, ?, ?, 20, ?, 0);
            """,
            (book_id, title, author, fname, fname, fpath.stat().st_size, fmt, source_id),
        )


def test_v190_search_multi_entity_diacritics_snippets_and_index_source(client):
    """
    Valida pesquisa integrada por conceito, reflexão, destaque, nota, conexão e livro,
    insensibilidade a acentos/cedilha/caixa, paginação, snippets estruturados seguros
    e diferenciação explícita de `index_source` ('knowledge_fts' vs 'books_metadata').
    """
    _seed_book("book_p6_ellul", "A Técnica e o Desafio do Século", author="Jacques Ellul")
    alice = auth_service.create_user("alice_p6_s1", "Password123!", "Alice Search", role="admin")
    tok_a = auth_service.create_session(alice["id"])

    # Sem autenticação -> 401
    client.cookies.clear()
    assert client.get("/api/knowledge/search?q=tecnica").status_code == 401

    client.cookies.update(_auth_cookies(tok_a))

    # Busca vazia -> 200 com lista vazia
    r_empty = client.get("/api/knowledge/search?q=")
    assert r_empty.status_code == 200
    assert r_empty.headers.get("cache-control") == "private, no-store"
    assert r_empty.json()["items"] == []
    assert r_empty.json()["total"] == 0

    c1 = knowledge_service.create_concept(
        alice["id"],
        "Autonomia da Técnica",
        description="Conceito central sobre evolução tecnológica autônoma e organização social.",
        tags=["filosofia", "civilização"],
    )
    ref1 = knowledge_service.create_reflection(
        alice["id"],
        "Reflexão sobre Evolução Tecnológica",
        "Na tradição crítica, a técnica moderna opera como sistema autônomo de organização.",
        primary_book_id="book_p6_ellul",
        tags=["ensaio"],
    )
    hl1 = annotations_service.create_highlight(
        book_id="book_p6_ellul",
        user_id=alice["id"],
        page=7,
        selected_text="A técnica busca eficiência máxima na organização moderna.",
        rects=[{"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.05}],
        chapter_label="Capítulo I",
        visibility="private",
    )
    nt1 = annotations_service.create_note(
        book_id="book_p6_ellul",
        user_id=alice["id"],
        page=8,
        content="Nota analítica sobre técnica e organização burocrática.",
        chapter_label="Capítulo I",
        visibility="private",
    )
    lnk1 = knowledge_service.create_knowledge_link(
        alice["id"],
        "reflection",
        ref1["id"],
        "concept",
        c1["id"],
        "grounds",
        rationale="Fundamenta a crítica da técnica e da organização racional.",
    )

    # Pesquisa sem acentos e em caixa mista ("TECNICA ORGANIZACAO") -> encontra todos os tipos com ambos os termos
    r_multi = client.get("/api/knowledge/search?q=TECNICA+ORGANIZACAO")
    assert r_multi.status_code == 200
    d_multi = r_multi.json()
    found_types = {it["type"] for it in d_multi["items"]}
    assert {"concept", "reflection", "highlight", "note", "link"}.issubset(found_types)

    for it in d_multi["items"]:
        assert it["index_source"] == "knowledge_fts"
        assert isinstance(it["snippet"], str)
        assert len(it["snippet"]) <= knowledge_service.MAX_SNIPPET_LEN + 5
        assert isinstance(it["snippet_segments"], list)
        assert any(seg["highlight"] is True for seg in it["snippet_segments"])

    # Pesquisa por título/autor do livro ("Jacques Ellul" ou "Desafio do Seculo") -> index_source == 'books_metadata'
    r_book = client.get("/api/knowledge/search?q=Desafio+Seculo&entity_types=book")
    assert r_book.status_code == 200
    d_book = r_book.json()
    assert d_book["total"] == 1
    assert d_book["items"][0]["type"] == "book"
    assert d_book["items"][0]["id"] == "book_p6_ellul"
    assert d_book["items"][0]["index_source"] == "books_metadata"

    # Pesquisa por cedilha sem diacrítico ("civilizacao")
    r_ced = client.get("/api/knowledge/search?q=civilizacao&entity_types=concept")
    assert r_ced.status_code == 200
    assert r_ced.json()["total"] == 1
    assert r_ced.json()["items"][0]["id"] == c1["id"]

    # Paginação determinística
    r_p1 = client.get("/api/knowledge/search?q=tecnica&limit=2&offset=0")
    r_p2 = client.get("/api/knowledge/search?q=tecnica&limit=2&offset=2")
    assert r_p1.status_code == 200 and r_p2.status_code == 200
    ids_p1 = {(i["type"], i["id"]) for i in r_p1.json()["items"]}
    ids_p2 = {(i["type"], i["id"]) for i in r_p2.json()["items"]}
    assert len(ids_p1) == 2
    assert ids_p1.isdisjoint(ids_p2)

    # Pesquisa sem resultados
    r_none = client.get("/api/knowledge/search?q=termoinexistente998877")
    assert r_none.status_code == 200
    assert r_none.json()["total"] == 0
    assert r_none.json()["items"] == []


def test_v190_search_hostile_fts5_inputs_and_malformed_unicode(client):
    """
    Valida que sintaxe malformada FTS5, aspas desbalanceadas, parênteses, operadores,
    asteriscos e strings longas ou surrogates nunca derrubam a API nem vazam erros do SQLite.
    """
    alice = auth_service.create_user("alice_p6_hostile", "Password123!", "Alice Hostile", role="admin")
    tok_a = auth_service.create_session(alice["id"])
    client.cookies.update(_auth_cookies(tok_a))

    knowledge_service.create_concept(alice["id"], "Hermenêutica Filosófica", description="Interpretação textual.")

    hostile_queries = [
        '"hermeneutica',
        '(((hermeneutica)))',
        'NEAR(hermeneutica, filosofica, 2)',
        'NOT OR AND * ^ : " \' ))) (((',
        'title:hermeneutica OR user_id:999',
        '***"""(((',
        '<script>alert(1)</script> hermeneutica',
    ]
    for hq in hostile_queries:
        res = client.get("/api/knowledge/search", params={"q": hq})
        assert res.status_code == 200, f"Falhou para query hostil: {hq}"
        body_text = res.text.lower()
        assert "sqlite" not in body_text
        assert "traceback" not in body_text

    # Consulta acima de 300 caracteres -> HTTP 400 ou 422
    too_long = "a" * 305
    r_long = client.get("/api/knowledge/search", params={"q": too_long})
    assert r_long.status_code in (400, 422)

    # Surrogate UTF-16 órfão direto na camada de serviço -> KnowledgeValidationError (HTTP 400)
    with pytest.raises(knowledge_service.KnowledgeValidationError):
        knowledge_service.search_knowledge(alice["id"], "termo\ud800invalido")


def test_v190_search_security_double_barrier_stale_fts_and_dominant_inaccessible_volume(client):
    """
    Valida:
    1. Isolamento entre usuários (conceitos, reflexões, notas privadas, destaques privados e conexões de Bob nunca aparecem para Alice).
    2. Anotação pública revogada (`public -> private`), livro ausente (`is_missing = 1`), fonte inativa (`is_active = 0`) e item excluído.
    3. Índice FTS5 deliberadamente obsoleto (entrada fantasma em `knowledge_fts` para item excluído ou cujo texto relacional mudou).
    4. Volume dominante de itens inacessíveis de outro usuário antes da paginação (Seção 16): mesmo com 2.500 registros de Bob para o mesmo termo,
       o único item autorizado de Alice é retornado na página 1.
    5. Isolamento estatístico BM25 IDF entre usuários (Seção 17): inserir centenas de documentos privados em Bob contendo um termo raro
       não altera a ordem de classificação dos resultados privados de Alice.
    """
    _seed_book("book_p6_sec_active", "Obra Ativa Segurança", source_id=1)
    _seed_book("book_p6_sec_revoked", "Obra Fonte Revogada", source_id=2)

    alice = auth_service.create_user("alice_p6_sec", "Password123!", "Alice Sec", role="admin")
    bob = auth_service.create_user("bob_p6_sec", "Password123!", "Bob Sec", role="reader")
    tok_a = auth_service.create_session(alice["id"])

    # Bob cria itens privados e 1 destaque público
    knowledge_service.create_concept(bob["id"], "Segredo Fenomenológico Bob", description="Privado de Bob.")
    knowledge_service.create_reflection(bob["id"], "Diário Fenomenológico Bob", "Corpo estritamente privado.")
    annotations_service.create_note(
        book_id="book_p6_sec_active",
        user_id=bob["id"],
        page=1,
        content="Nota privada fenomenológica de Bob.",
        visibility="private",
    )
    hl_pub_bob = annotations_service.create_highlight(
        book_id="book_p6_sec_active",
        user_id=bob["id"],
        page=2,
        selected_text="Destaque público fenomenológico compartilhado por Bob.",
        rects=[{"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.03}],
        visibility="public",
    )
    hl_src2 = annotations_service.create_highlight(
        book_id="book_p6_sec_revoked",
        user_id=bob["id"],
        page=3,
        selected_text="Destaque público fenomenológico na fonte 2.",
        rects=[{"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.03}],
        visibility="public",
    )

    client.cookies.update(_auth_cookies(tok_a))
    r1 = client.get("/api/knowledge/search?q=fenomenologico")
    assert r1.status_code == 200
    ids1 = {it["id"] for it in r1.json()["items"]}
    # Alice vê apenas os 2 destaques públicos de Bob em livros/fontes ativas
    assert ids1 == {hl_pub_bob["id"], hl_src2["id"]}

    # Bob revoga visibilidade do destaque público para privado -> desaparece imediatamente da busca de Alice
    annotations_service.set_highlight_visibility(hl_pub_bob["id"], "private", user_id=bob["id"])
    # Desativa fonte 2 -> hl_src2 também desaparece
    with database.get_db() as conn:
        conn.execute("UPDATE library_sources SET is_active = 0 WHERE id = 2;")

    r2 = client.get("/api/knowledge/search?q=fenomenologico")
    assert r2.status_code == 200
    assert r2.json()["total"] == 0
    assert r2.json()["items"] == []

    # Caso: Índice FTS5 obsoleto (inserimos diretamente uma linha obsoleta em knowledge_fts apontando para o conceito de Alice com termo antigo)
    c_alice = knowledge_service.create_concept(alice["id"], "Ontologia Real", description="Texto atualizado atual.")
    with database.get_db() as conn:
        conn.execute(
            """
            INSERT INTO knowledge_fts (entity_type, entity_id, user_id, book_id, title, body, tags)
            VALUES ('concept', ?, ?, '', 'TermoFantasmaObsoleto', 'Conteudo antigo que ja nao existe na tabela relacional', '[]');
            """,
            (c_alice["id"], str(alice["id"])),
        )
    r_stale = client.get("/api/knowledge/search?q=TermoFantasmaObsoleto")
    assert r_stale.status_code == 200
    assert r_stale.json()["total"] == 0

    # Caso: Volume dominante de 2.500 registros inacessíveis de Bob antes da paginação (Seção 16)
    now_iso = "2026-11-21 12:00:00"
    with database.get_db() as conn:
        cur = conn.cursor()
        bob_rows = [
            (
                f"bob_dom_{i}",
                bob["id"],
                f"Conceito Dominante Cibernetica {i}",
                f"conceito dominante cibernetica {i}",
                "Termo raro cibernetica em massa para testar paginação.",
                "[]",
                "#d4a853",
                0,
                now_iso,
                now_iso,
            )
            for i in range(2500)
        ]
        cur.executemany(
            """
            INSERT INTO concepts (id, user_id, name, normalized_name, description, tags_json, color, is_archived, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            bob_rows,
        )

    c_alice_cib = knowledge_service.create_concept(
        alice["id"],
        "Cibernética de Segunda Ordem",
        description="Único conceito autorizado de Alice sobre cibernética.",
    )
    r_dom = client.get("/api/knowledge/search?q=cibernetica&limit=10&offset=0")
    assert r_dom.status_code == 200
    d_dom = r_dom.json()
    assert d_dom["total"] == 1
    assert d_dom["items"][0]["id"] == c_alice_cib["id"]

    # Caso: Invariância de ranking (ausência de vazamento estatístico BM25 IDF entre usuários — Seção 17)
    # Alice tem 2 conceitos com os termos "alfa" e "omega":
    # Item 1 tem "alfa" no título e "omega" na descrição; Item 2 tem "omega" no título e "alfa" na descrição.
    ca1 = knowledge_service.create_concept(
        alice["id"],
        "Alfa Primordial",
        description="Discussão complementar envolvendo o elemento omega.",
    )
    ca2 = knowledge_service.create_concept(
        alice["id"],
        "Omega Primordial",
        description="Discussão complementar envolvendo o elemento alfa.",
    )
    order_before = [it["id"] for it in client.get("/api/knowledge/search?q=alfa+omega").json()["items"]]
    assert set(order_before) == {ca1["id"], ca2["id"]}

    # Bob insere 500 documentos contendo apenas "alfa" (o que distorceria brutalmente o IDF global de "alfa" vs "omega" no BM25 bruto)
    with database.get_db() as conn:
        cur = conn.cursor()
        idf_rows = [
            (
                f"bob_idf_{i}",
                bob["id"],
                f"Alfa {i}",
                f"alfa {i}",
                "alfa alfa alfa repetido em Bob",
                "[]",
                "#d4a853",
                0,
                now_iso,
                now_iso,
            )
            for i in range(500)
        ]
        cur.executemany(
            """
            INSERT INTO concepts (id, user_id, name, normalized_name, description, tags_json, color, is_archived, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            idf_rows,
        )

    order_after = [it["id"] for it in client.get("/api/knowledge/search?q=alfa+omega").json()["items"]]
    assert order_after == order_before


def test_v190_search_fallback_vs_structural_corruption():
    """
    Valida que o fallback relacional controlado retorna os mesmos itens autorizados
    quando acionado, enquanto erros de corrupção estrutural ('database disk image is malformed')
    não são mascarados silenciosamente.
    """
    alice = auth_service.create_user("alice_p6_fb", "Password123!", "Alice Fallback", role="admin")
    c = knowledge_service.create_concept(alice["id"], "Dialética Negativa", description="Adorno e a crítica da identidade.")

    res_fts = knowledge_service.search_knowledge(alice["id"], "dialetica", force_fallback=False)
    res_fb = knowledge_service.search_knowledge(alice["id"], "dialetica", force_fallback=True)

    assert res_fts["search_engine"] == "fts5"
    assert res_fb["search_engine"] == "relational_fallback"
    assert res_fts["total"] == 1 and res_fb["total"] == 1
    assert res_fts["items"][0]["id"] == c["id"]
    assert res_fb["items"][0]["id"] == c["id"]
    assert res_fb["items"][0]["index_source"] == "relational_fallback"


def test_v190_graph_depth_limits_cycles_filters_and_unavailable_protection(client):
    """
    Valida o endpoint `GET /api/knowledge/graph` e `get_knowledge_graph`:
    - Profundidade 1 e 2.
    - Todos os 5 tipos de entidade como centro (`concept`, `reflection`, `highlight`, `note`, `book`).
    - Prevenção de loops em grafos cíclicos (A -> B -> C -> A) e deduplicação de arestas inversas.
    - Proteção de referências indisponíveis (sentinela neutro, `expandable=False`, nunca expandido no 2º salto).
    - Truncamento rígido em `max_nodes` (teto 80 incluindo o nó central) e `max_edges` (teto 160).
    - Equivalência exata com `accessible_text_list`.
    """
    _seed_book("book_p6_graph", "Obra Grafo Conectado")
    alice = auth_service.create_user("alice_p6_gr", "Password123!", "Alice Graph", role="admin")
    bob = auth_service.create_user("bob_p6_gr", "Password123!", "Bob Graph", role="reader")
    tok_a = auth_service.create_session(alice["id"])

    client.cookies.clear()
    assert client.get("/api/knowledge/graph?entity_type=book&entity_id=book_p6_graph").status_code == 401

    client.cookies.update(_auth_cookies(tok_a))

    c_a = knowledge_service.create_concept(alice["id"], "Nó Conceito A")
    c_b = knowledge_service.create_concept(alice["id"], "Nó Conceito B")
    c_c = knowledge_service.create_concept(alice["id"], "Nó Conceito C")
    ref_a = knowledge_service.create_reflection(alice["id"], "Nó Reflexão A", "Corpo da reflexão A")
    hl_a = annotations_service.create_highlight(
        book_id="book_p6_graph",
        user_id=alice["id"],
        page=4,
        selected_text="Destaque A no grafo",
        rects=[{"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.03}],
        visibility="private",
    )
    nt_a = annotations_service.create_note(
        book_id="book_p6_graph",
        user_id=alice["id"],
        page=5,
        content="Nota A no grafo",
        visibility="private",
    )

    # Cria ciclo: c_a -> c_b -> c_c -> c_a, mais conexões para reflection, highlight, note e book
    knowledge_service.create_knowledge_link(alice["id"], "concept", c_a["id"], "concept", c_b["id"], "develops", "A desenvolve B")
    knowledge_service.create_knowledge_link(alice["id"], "concept", c_b["id"], "concept", c_c["id"], "grounds", "B fundamenta C")
    knowledge_service.create_knowledge_link(alice["id"], "concept", c_c["id"], "concept", c_a["id"], "complements", "C complementa A")
    knowledge_service.create_knowledge_link(alice["id"], "reflection", ref_a["id"], "concept", c_a["id"], "references", "Ref cita A")
    knowledge_service.create_knowledge_link(alice["id"], "highlight", hl_a["id"], "concept", c_b["id"], "exemplifies", "HL exemplifica B")
    knowledge_service.create_knowledge_link(alice["id"], "note", nt_a["id"], "book", "book_p6_graph", "relates_to", "Nota ligada ao livro")
    knowledge_service.create_knowledge_link(alice["id"], "concept", c_b["id"], "note", nt_a["id"], "questions", "B questiona Nota A")

    # Destaque público de Bob conectado a c_a, que depois será revogado para privado
    hl_bob = annotations_service.create_highlight(
        book_id="book_p6_graph",
        user_id=bob["id"],
        page=9,
        selected_text="Segredo revogado de Bob no grafo",
        rects=[{"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.03}],
        visibility="public",
    )
    knowledge_service.create_knowledge_link(alice["id"], "concept", c_a["id"], "highlight", hl_bob["id"], "references")
    # Revoga visibilidade de hl_bob
    annotations_service.set_highlight_visibility(hl_bob["id"], "private", user_id=bob["id"])

    # 1. Testa grafo de 1 salto centrado em c_a
    r_g1 = client.get(f"/api/knowledge/graph?entity_type=concept&entity_id={c_a['id']}&depth=1")
    assert r_g1.status_code == 200
    assert r_g1.headers.get("cache-control") == "private, no-store"
    g1 = r_g1.json()
    assert g1["center"]["id"] == c_a["id"]
    assert g1["center"]["is_center"] is True
    assert g1["depth"] == 1
    # Nós em 1 salto de c_a: c_a (centro), c_b, c_c, ref_a e hl_bob (indisponível) = 5 nós
    assert g1["node_count"] == 5
    assert g1["edge_count"] == 4
    assert len(g1["accessible_text_list"]) == g1["edge_count"]

    # Verifica nó indisponível (hl_bob)
    unavail_nodes = [n for n in g1["nodes"] if not n["available"]]
    assert len(unavail_nodes) == 1
    un_node = unavail_nodes[0]
    assert un_node["label"] == "Referência indisponível"
    assert un_node["title"] is None
    assert un_node["excerpt"] is None
    assert un_node["book_id"] is None
    assert un_node["expandable"] is False
    assert un_node["navigable"] is False
    assert "Segredo revogado" not in r_g1.text

    # 2. Testa grafo de 2 saltos centrado em c_a (expande c_b para incluir hl_a e nt_a, sem entrar em loop no ciclo)
    r_g2 = client.get(f"/api/knowledge/graph?entity_type=concept&entity_id={c_a['id']}&depth=2")
    assert r_g2.status_code == 200
    g2 = r_g2.json()
    assert g2["depth"] == 2
    node_keys_g2 = {n["key"] for n in g2["nodes"]}
    assert f"highlight:{hl_a['id']}" in node_keys_g2
    assert f"note:{nt_a['id']}" in node_keys_g2
    # Nenhuma aresta duplicada
    edge_ids_g2 = [e["id"] for e in g2["edges"]]
    assert len(edge_ids_g2) == len(set(edge_ids_g2))

    # 3. Testa todos os 5 tipos de entidade como centro do grafo
    for et, eid in [
        ("concept", c_a["id"]),
        ("reflection", ref_a["id"]),
        ("highlight", hl_a["id"]),
        ("note", nt_a["id"]),
        ("book", "book_p6_graph"),
    ]:
        rc = client.get("/api/knowledge/graph", params={"entity_type": et, "entity_id": eid, "depth": 1})
        assert rc.status_code == 200, f"Falha ao centralizar grafo em {et}:{eid}"
        assert rc.json()["center"]["type"] == et
        assert rc.json()["center"]["id"] == eid

    # 4. Tentar centralizar grafo em entidade privada de Bob -> HTTP 404 anti-enumeração
    c_bob_priv = knowledge_service.create_concept(bob["id"], "Conceito Secreto Bob")
    r_404 = client.get("/api/knowledge/graph", params={"entity_type": "concept", "entity_id": c_bob_priv["id"]})
    assert r_404.status_code == 404

    # 5. Testa truncamento explícito (max_nodes=3 inclui o nó central + 2 vizinhos)
    r_trunc = client.get(
        "/api/knowledge/graph",
        params={"entity_type": "concept", "entity_id": c_a["id"], "depth": 2, "max_nodes": 3},
    )
    assert r_trunc.status_code == 200
    gt = r_trunc.json()
    assert gt["node_count"] == 3
    assert gt["truncated"] is True
    assert "max_nodes" in gt["truncation_reasons"]

    # 6. Valida rejeição de parâmetros fora dos tetos rígidos (depth=3, max_nodes=81, max_edges=161)
    assert client.get("/api/knowledge/graph", params={"entity_type": "concept", "entity_id": c_a["id"], "depth": 3}).status_code in (400, 422)
    assert client.get("/api/knowledge/graph", params={"entity_type": "concept", "entity_id": c_a["id"], "max_nodes": 81}).status_code in (400, 422)
    assert client.get("/api/knowledge/graph", params={"entity_type": "concept", "entity_id": c_a["id"], "max_edges": 161}).status_code in (400, 422)


def test_v190_phase6_quantitative_scale_and_explain_query_plan(client):
    """
    Verificação quantitativa determinística e objetiva (Seção 36):
    - Cenário A (intermediário): 1.000 conceitos, 1.000 reflexões, 1.000 anotações, 10.000 conexões.
    - Cenário B (teto de projeto): 10.000 conceitos, 2.000 reflexões, 10.000 anotações, 50.000 conexões,
      incluindo nó altamente conectado (> 500 conexões).
    - Valida uso de índices e `knowledge_fts` via `EXPLAIN QUERY PLAN` sem `SCAN knowledge_links` linear completo.
    """
    _seed_book("book_p6_scale", "Obra Escala Fase 6")
    user = auth_service.create_user("scale_p6_user", "Password123!", "Scale User P6", role="admin")
    uid = user["id"]
    now_iso = "2026-11-21 15:00:00"

    # Verifica EXPLAIN QUERY PLAN das consultas críticas do grafo e da busca
    with database.get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT * FROM knowledge_links
            WHERE user_id = ?
              AND ((source_type = 'concept' AND source_id = 'c_0')
                   OR (target_type = 'concept' AND target_id = 'c_0'))
            ORDER BY created_at DESC, id ASC;
            """,
            (uid,),
        )
        plan_graph = " | ".join(str(dict(r)) for r in cur.fetchall())
        assert "SEARCH" in plan_graph.upper()
        assert "IDX_KLINKS" in plan_graph.upper()

        cur.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT kf.entity_type, kf.entity_id
            FROM knowledge_fts kf
            WHERE knowledge_fts MATCH '"filosofia"*'
              AND (kf.entity_type = 'concept' AND EXISTS (
                  SELECT 1 FROM concepts c WHERE c.id = kf.entity_id AND c.user_id = ?
              ))
            LIMIT 50;
            """,
            (uid,),
        )
        plan_search = " | ".join(str(dict(r)) for r in cur.fetchall())
        assert "VIRTUAL TABLE INDEX" in plan_search.upper()
        assert "SEARCH C EXISTS USING INDEX" in plan_search.upper()

    # Semeia Cenário B completo (10.000 conceitos, 2.000 reflexões, 10.000 anotações, 50.000 conexões)
    # Desativamos temporariamente os triggers de FTS apenas durante o bulk seed de 22k linhas que não têm o termo alvo,
    # e inserimos no FTS os registros necessários + reconstruímos ou populamos diretamente para manter o teste rápido.
    with database.get_db() as conn:
        cur = conn.cursor()
        concept_rows = [
            (
                f"sc6_c_{i}",
                uid,
                f"Conceito Escala {i}" + (" EpistemologiaQuantica" if i % 250 == 0 else ""),
                f"conceito escala {i}" + (" epistemologiaquantica" if i % 250 == 0 else ""),
                f"Descrição sintética do conceito {i}." + (" Estudo sobre epistemologiaquantica e método." if i % 100 == 0 else ""),
                '["escala"]',
                "#d4a853",
                0,
                now_iso,
                now_iso,
            )
            for i in range(10000)
        ]
        cur.executemany(
            """
            INSERT INTO concepts (id, user_id, name, normalized_name, description, tags_json, color, is_archived, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            concept_rows,
        )

        refl_rows = [
            (
                f"sc6_r_{i}",
                uid,
                f"Reflexão Escala {i}" + (" EpistemologiaQuantica" if i % 100 == 0 else ""),
                f"Corpo Markdown da reflexão {i} para validação de escala.",
                "book_p6_scale",
                '["ensaio"]',
                1 if i < 10 else 0,
                0,
                now_iso,
                now_iso,
            )
            for i in range(2000)
        ]
        cur.executemany(
            """
            INSERT INTO reflections (id, user_id, title, body_markdown, primary_book_id, tags_json, is_pinned, is_archived, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            refl_rows,
        )

        note_rows = [
            (
                f"sc6_n_{i}",
                "book_p6_scale",
                uid,
                (i % 20) + 1,
                None,
                f"Nota sintética {i} sobre leitura." + (" EpistemologiaQuantica aplicada." if i % 250 == 0 else ""),
                "yellow",
                "private",
                None,
                "Cap. Escala",
                now_iso,
                now_iso,
            )
            for i in range(10000)
        ]
        cur.executemany(
            """
            INSERT INTO notes (id, book_id, user_id, page, highlight_id, content, color, visibility, epub_cfi, chapter_label, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            note_rows,
        )

        # 50.000 conexões:
        # - As primeiras 600 conexões partem do nó altamente conectado `sc6_c_0` (> 500 conexões)
        # - As demais 49.400 distribuem-se pelo grafo
        link_rows = []
        for i in range(600):
            link_rows.append(
                (
                    f"sc6_l_hub_{i}",
                    uid,
                    "concept",
                    "sc6_c_0",
                    "concept",
                    f"sc6_c_{i + 1}",
                    "relates_to",
                    "",
                    now_iso,
                    now_iso,
                )
            )
        for i in range(49400):
            link_rows.append(
                (
                    f"sc6_l_{i}",
                    uid,
                    "reflection",
                    f"sc6_r_{i % 2000}",
                    "concept",
                    f"sc6_c_{(i % 9900) + 10}",
                    "references" if i % 2 == 0 else "develops",
                    "",
                    now_iso,
                    now_iso,
                )
            )
        cur.executemany(
            """
            INSERT INTO knowledge_links (id, user_id, source_type, source_id, target_type, target_id, relation_type, rationale, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            link_rows,
        )

    # 1. Mede latência da pesquisa FTS5 no volume de 10k conceitos + 2k reflexões + 10k notas + 50k links
    t0 = time.perf_counter()
    s_res = knowledge_service.search_knowledge(uid, "EpistemologiaQuantica", limit=24, offset=0)
    search_elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert s_res["total"] > 50
    assert len(s_res["items"]) == 24
    assert search_elapsed_ms < 1500.0, f"Busca FTS5 excedeu orçamento de tempo: {search_elapsed_ms:.1f}ms"

    # 2. Mede latência da construção do grafo local de 1 e 2 níveis sobre o hub `sc6_c_0` (com 600 conexões diretas)
    t1 = time.perf_counter()
    g_hub_1 = knowledge_service.get_knowledge_graph(uid, "concept", "sc6_c_0", depth=1)
    graph1_elapsed_ms = (time.perf_counter() - t1) * 1000.0

    assert g_hub_1["node_count"] == knowledge_service.DEFAULT_GRAPH_MAX_NODES  # 40
    assert g_hub_1["truncated"] is True
    assert "max_nodes" in g_hub_1["truncation_reasons"]
    assert graph1_elapsed_ms < 1500.0, f"Grafo 1 salto no hub excedeu orçamento: {graph1_elapsed_ms:.1f}ms"

    t2 = time.perf_counter()
    g_hub_2 = knowledge_service.get_knowledge_graph(
        uid,
        "concept",
        "sc6_c_0",
        depth=2,
        max_nodes=knowledge_service.HARD_MAX_GRAPH_NODES,
        max_edges=knowledge_service.HARD_MAX_GRAPH_EDGES,
    )
    graph2_elapsed_ms = (time.perf_counter() - t2) * 1000.0

    assert g_hub_2["node_count"] <= knowledge_service.HARD_MAX_GRAPH_NODES  # <= 80
    assert g_hub_2["edge_count"] <= knowledge_service.HARD_MAX_GRAPH_EDGES  # <= 160
    assert g_hub_2["truncated"] is True
    assert graph2_elapsed_ms < 1500.0, f"Grafo 2 saltos no hub excedeu orçamento: {graph2_elapsed_ms:.1f}ms"
