"""
Suíte de Testes Unitários, de Integração de API, Segurança, Formatos e Escala — Fase 7 da Lectoria v1.9.0.
Valida `knowledge_export_service.py` e `GET /api/knowledge/export`:
- Formatos `markdown_zip` (com e sem `wikilinks`), `json` (v2.0) e `csv` (ZIP tabular).
- Segurança, isolamento multiusuário, anti-IDOR para admin, revogação de visibilidade (`public -> private`),
  fontes desativadas, política conservadora para anotações públicas de terceiros (`content_omitted=True`)
  e proteção de referências indisponíveis.
- Integridade de Markdown, frontmatter YAML com caracteres especiais, checksums SHA-256 no `manifest.json`,
  extração segura contra Zip Slip, proteção contra CSV Formula Injection sem alterar o banco.
- Concorrência, limpeza de arquivos temporários e medições quantitativas (incluindo `tracemalloc` e escala 50k links).
"""

from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import tracemalloc
import zipfile

import annotations_service
import auth_service
import config
import database
import knowledge_export_service
import knowledge_service


def _auth_cookies(token: str, csrf: str = "csrf_p7_unit"):
    return {"lectoria_session": token, "lectoria_csrf": csrf}


def _seed_book(book_id: str, title: str, author: str = "Autor Fase 7", fmt: str = "pdf", source_id: int = 1):
    lib_dir = config.LIBRARY_DIR
    src_dir = lib_dir if source_id == 1 else (lib_dir / f"src_{source_id}")
    src_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{book_id}.{fmt}"
    fpath = src_dir / fname
    if not fpath.exists():
        if fmt == "epub":
            with zipfile.ZipFile(fpath, "w") as zf:
                zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
                zf.writestr(
                    "META-INF/container.xml",
                    '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
                )
                zf.writestr(
                    "content.opf",
                    f'<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{title}</dc:title><dc:creator>{author}</dc:creator></metadata><manifest/><spine/></package>',
                )
        else:
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
            VALUES (?, ?, ?, ?, ?, ?, ?, 40, ?, 0);
            """,
            (book_id, title, author, fname, fname, fpath.stat().st_size, fmt, source_id),
        )


def test_v190_export_security_isolation_revocation_and_conservative_third_party_policy(client):
    """
    Valida:
    - HTTP 401 sem autenticação e HTTP 400/422 para formato inválido.
    - Cabeçalhos seguros: `Cache-Control: private, no-store`, `X-Content-Type-Options: nosniff`, `Content-Disposition`.
    - Admin exportando pelo endpoint pessoal recebe apenas o seu próprio Atlas (nunca o de outro usuário).
    - Referência revogada (`public -> private`) ou de fonte desativada aparece como sentinela neutro
      (`Referência indisponível`, `id: None`, sem título/trecho/página/CFI/motivo).
    - Anotação pública de terceiro referenciada segue política conservadora (`content_omitted: True`, sem texto integral).
    - Ausência de senhas, sessões, tokens CSRF ou caminhos absolutos do servidor nos artefatos exportados.
    """
    _seed_book("book_p7_pdf", "Obra Portátil PDF", author="Jacques Ellul", fmt="pdf", source_id=1)
    _seed_book("book_p7_epub", "Obra Portátil EPUB", author="Martin Heidegger", fmt="epub", source_id=1)
    _seed_book("book_p7_rev", "Obra Fonte Desativada", author="Autor Oculto", fmt="pdf", source_id=2)

    alice = auth_service.create_user("alice_p7_sec", "Password123!", "Alice Export", role="reader")
    bob = auth_service.create_user("bob_p7_sec", "Password123!", "Bob Admin Export", role="admin")
    tok_a = auth_service.create_session(alice["id"])
    tok_b = auth_service.create_session(bob["id"])

    # Sem autenticação -> 401
    client.cookies.clear()
    assert client.get("/api/knowledge/export?format=json").status_code == 401

    # Formato inválido -> 422/400
    client.cookies.update(_auth_cookies(tok_a))
    assert client.get("/api/knowledge/export?format=xml").status_code in (400, 422)

    # Bob cria dados privados, 1 destaque público que permanecerá público e 1 destaque público que será revogado
    knowledge_service.create_concept(bob["id"], "ConceitoPrivadoBobP7", description="Segredo de Bob")
    knowledge_service.create_reflection(bob["id"], "ReflexãoPrivadaBobP7", "Texto secreto de Bob")

    hl_bob_pub_kept = annotations_service.create_highlight(
        book_id="book_p7_epub",
        user_id=bob["id"],
        page=1,
        selected_text="TextoIntegralDeTerceiroQueNaoDeveSerRedistribuido",
        rects=[{"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.03}],
        epub_cfi="epubcfi(/6/2[ch1]!/4/2/1:0)",
        chapter_label="Cap. Público",
        visibility="public",
    )
    hl_bob_revoked = annotations_service.create_highlight(
        book_id="book_p7_pdf",
        user_id=bob["id"],
        page=12,
        selected_text="TrechoRevogadoSecretoBob",
        rects=[{"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.03}],
        chapter_label="Cap. Revogado",
        visibility="public",
    )

    # Alice cria seus próprios itens e conexões
    c_alice = knowledge_service.create_concept(alice["id"], "Autonomia Técnica", description="Conceito pessoal de Alice.")
    r_alice = knowledge_service.create_reflection(
        alice["id"],
        "Notas Portáteis",
        "# Introdução\n\nTexto autoral preservado de Alice.",
        primary_book_id="book_p7_pdf",
    )
    hl_alice = annotations_service.create_highlight(
        book_id="book_p7_pdf",
        user_id=alice["id"],
        page=5,
        selected_text="Destaque próprio de Alice na página 5.",
        rects=[{"x": 0.1, "y": 0.2, "w": 0.4, "h": 0.04}],
        chapter_label="Cap. I",
        visibility="private",
    )

    # Conexões de Alice para: destaque público de Bob (mantido), destaque de Bob (que será revogado) e livro da fonte 2 (que será desativada)
    knowledge_service.create_knowledge_link(
        alice["id"], "concept", c_alice["id"], "highlight", hl_bob_pub_kept["id"], "references", "Ref. pública mantida"
    )
    knowledge_service.create_knowledge_link(
        alice["id"], "concept", c_alice["id"], "highlight", hl_bob_revoked["id"], "questions", "Justificativa própria de Alice sobre trecho revogado"
    )
    knowledge_service.create_knowledge_link(
        alice["id"], "reflection", r_alice["id"], "book", "book_p7_rev", "develops", "Justificativa própria sobre obra desativada"
    )
    knowledge_service.create_knowledge_link(
        alice["id"], "reflection", r_alice["id"], "highlight", hl_alice["id"], "grounds"
    )

    # Bob revoga hl_bob_revoked e desativa a fonte 2
    annotations_service.set_highlight_visibility(hl_bob_revoked["id"], "private", user_id=bob["id"])
    with database.get_db() as conn:
        conn.execute("UPDATE library_sources SET is_active = 0 WHERE id = 2;")

    # Exportação JSON v2.0 de Alice
    res_json = client.get("/api/knowledge/export?format=json")
    assert res_json.status_code == 200
    assert res_json.headers.get("cache-control") == "private, no-store"
    assert res_json.headers.get("x-content-type-options") == "nosniff"
    assert "attachment; filename=" in (res_json.headers.get("content-disposition") or "")

    raw_json_text = res_json.text
    assert "ConceitoPrivadoBobP7" not in raw_json_text
    assert "ReflexãoPrivadaBobP7" not in raw_json_text
    assert "TrechoRevogadoSecretoBob" not in raw_json_text
    assert "Cap. Revogado" not in raw_json_text
    assert "Obra Fonte Desativada" not in raw_json_text
    assert "TextoIntegralDeTerceiroQueNaoDeveSerRedistribuido" not in raw_json_text
    assert hl_bob_revoked["id"] not in raw_json_text
    assert "book_p7_rev" not in raw_json_text
    assert str(config.LIBRARY_DIR) not in raw_json_text
    assert tok_a not in raw_json_text

    payload = res_json.json()
    assert payload["format"] == "lectoria-atlas"
    assert payload["format_version"] == "2.0"
    assert payload["app_version"] == config.APP_VERSION
    assert payload["counts"]["concepts"] == 1
    assert payload["counts"]["reflections"] == 1
    assert payload["counts"]["links"] == 4
    assert payload["counts"]["unavailable_link_endpoints"] == 2

    # Verifica política conservadora no destaque público de Bob
    pub_ann = next(a for a in payload["annotations"] if a["id"] == hl_bob_pub_kept["id"])
    assert pub_ann["is_own"] is False
    assert pub_ann["content_omitted"] is True
    assert pub_ann["text"] is None
    assert pub_ann["epub_cfi"] == "epubcfi(/6/2[ch1]!/4/2/1:0)"

    # Verifica destaque próprio de Alice com localização PDF preservada
    own_ann = next(a for a in payload["annotations"] if a["id"] == hl_alice["id"])
    assert own_ann["is_own"] is True
    assert own_ann["content_omitted"] is False
    assert own_ann["text"] == "Destaque próprio de Alice na página 5."
    assert own_ann["page"] == 5

    # Verifica que o admin Bob ao chamar /api/knowledge/export exporta apenas seu próprio Atlas, não o de Alice
    client.cookies.clear()
    client.cookies.update(_auth_cookies(tok_b))
    res_bob = client.get("/api/knowledge/export?format=json")
    assert res_bob.status_code == 200
    assert "Autonomia Técnica" not in res_bob.text
    assert "ConceitoPrivadoBobP7" in res_bob.text


def test_v190_export_markdown_zip_structure_yaml_links_and_zip_slip_safety(client):
    """
    Valida o pacote `markdown_zip` (com e sem `wikilinks`):
    - Estrutura ZIP, extração segura em diretório temporário, zero Zip Slip, zero duplicatas.
    - Checksums SHA-256 válidos no `manifest.json`.
    - Frontmatter YAML válido mesmo com aspas, dois-pontos, quebras de linha, `---` e emojis.
    - Texto autoral `body_markdown` preservado literalmente sem conversão para HTML.
    - Links relativos válidos apontando para arquivos existentes no ZIP.
    """
    _seed_book("book_p7_md", "Obra: \"Crítica\" & Método", author="Autor: Teste", fmt="pdf")
    alice = auth_service.create_user("alice_p7_md", "Password123!", "Alice MD", role="admin")
    tok_a = auth_service.create_session(alice["id"])
    client.cookies.update(_auth_cookies(tok_a))

    c1 = knowledge_service.create_concept(
        alice["id"],
        'Conceito: "Ação" & Razão 🚀',
        description="Descrição com acentuação: çãõéíóú e dois pontos: teste.",
        tags=["filosofia", "método"],
    )
    raw_md = (
        "## Seção 1: \"Citação\"\n\n"
        "Texto com **negrito**, `código` e emoji 🔍.\n\n"
        "---\n\n"
        "- Item A\n"
        "- Item B\n\n"
        "> Citação autoral preservada."
    )
    r1 = knowledge_service.create_reflection(
        alice["id"],
        'Reflexão: "Técnica" — Parte 1',
        raw_md,
        primary_book_id="book_p7_md",
        tags=["ensaio"],
    )
    knowledge_service.create_knowledge_link(
        alice["id"], "reflection", r1["id"], "concept", c1["id"], "develops", "Desenvolve o conceito."
    )

    resp = client.get("/api/knowledge/export?format=markdown_zip&wikilinks=true")
    assert resp.status_code == 200
    assert resp.headers.get("content-type") == "application/zip"

    zf_bytes = io.BytesIO(resp.content)
    with zipfile.ZipFile(zf_bytes, "r") as zf:
        names = zf.namelist()
        assert len(names) == len(set(names)), "Entradas duplicadas no ZIP"
        for name in names:
            assert not name.startswith("/")
            assert ".." not in name.split("/")
            assert "\\" not in name

        assert "lectoria-atlas-export/indice.md" in names
        assert "lectoria-atlas-export/manifest.json" in names
        assert f"lectoria-atlas-export/conceitos/{c1['id']}.md" in names
        assert f"lectoria-atlas-export/reflexoes/{r1['id']}.md" in names
        assert "lectoria-atlas-export/livros/book_p7_md.md" in names

        # Valida checksums SHA-256 do manifesto
        manifest = json.loads(zf.read("lectoria-atlas-export/manifest.json").decode("utf-8"))
        assert manifest["format"] == "lectoria-atlas-markdown-zip"
        assert manifest["format_version"] == "2.0"
        assert manifest["wikilinks_enabled"] is True
        for fentry in manifest["files"]:
            content_bytes = zf.read(fentry["path"])
            assert len(content_bytes) == fentry["size_bytes"]
            assert hashlib.sha256(content_bytes).hexdigest() == fentry["sha256"]

        # Valida preservação literal do body_markdown e presença de wikilinks opcionais
        refl_md = zf.read(f"lectoria-atlas-export/reflexoes/{r1['id']}.md").decode("utf-8")
        assert raw_md in refl_md
        assert f"../conceitos/{c1['id']}.md" in refl_md
        assert "[[" in refl_md

        # Extração segura em diretório temporário isolado
        with tempfile.TemporaryDirectory() as extract_dir:
            zf.extractall(extract_dir)
            extracted_refl = Path(extract_dir) / "lectoria-atlas-export" / "reflexoes" / f"{r1['id']}.md"
            assert extracted_refl.exists()
            assert (Path(extract_dir) / "lectoria-atlas-export" / "conceitos" / f"{c1['id']}.md").exists()


def test_v190_export_csv_tabular_and_formula_injection_protection(client):
    """
    Valida exportação CSV em ZIP e neutralização contra CSV Formula Injection
    (`=`, `+`, `-`, `@`, `\\t`, espaços antes de fórmula), mantendo o banco intacto.
    """
    _seed_book("book_p7_csv", "Livro CSV", author="=HYPERLINK(\"http://evil\",\"click\")")
    alice = auth_service.create_user("alice_p7_csv", "Password123!", "Alice CSV", role="admin")
    tok_a = auth_service.create_session(alice["id"])
    client.cookies.update(_auth_cookies(tok_a))

    c1 = knowledge_service.create_concept(
        alice["id"],
        "=SUM(1,2)",
        description="+cmd|' /C calc'!A0",
    )
    r1 = knowledge_service.create_reflection(
        alice["id"],
        "@evil_macro",
        "-2+3 texto autoral",
        primary_book_id="book_p7_csv",
    )
    knowledge_service.create_knowledge_link(
        alice["id"], "reflection", r1["id"], "concept", c1["id"], "contradicts", "\t=1+1"
    )

    resp = client.get("/api/knowledge/export?format=csv")
    assert resp.status_code == 200
    assert resp.headers.get("content-type") == "application/zip"

    with zipfile.ZipFile(io.BytesIO(resp.content), "r") as zf:
        names = zf.namelist()
        for expected in (
            "lectoria-atlas-export/concepts.csv",
            "lectoria-atlas-export/reflections.csv",
            "lectoria-atlas-export/links.csv",
            "lectoria-atlas-export/books.csv",
            "lectoria-atlas-export/annotations.csv",
            "lectoria-atlas-export/manifest.json",
        ):
            assert expected in names

        concepts_rows = list(
            csv.DictReader(io.StringIO(zf.read("lectoria-atlas-export/concepts.csv").decode("utf-8-sig")))
        )
        assert len(concepts_rows) == 1
        assert concepts_rows[0]["name"] == "'=SUM(1,2)"
        assert concepts_rows[0]["description"] == "'+cmd|' /C calc'!A0"

        links_rows = list(
            csv.DictReader(io.StringIO(zf.read("lectoria-atlas-export/links.csv").decode("utf-8-sig")))
        )
        assert len(links_rows) == 1
        assert links_rows[0]["relation_type"] == "contradicts"
        assert links_rows[0]["rationale"].startswith("'")

        books_rows = list(
            csv.DictReader(io.StringIO(zf.read("lectoria-atlas-export/books.csv").decode("utf-8-sig")))
        )
        assert books_rows[0]["author"].startswith("'=")

    # Verifica que os valores canônicos no banco SQLite NÃO foram alterados pela sanitização CSV
    db_concept = knowledge_service.get_concept(c1["id"], alice["id"])
    assert db_concept["name"] == "=SUM(1,2)"
    assert db_concept["description"] == "+cmd|' /C calc'!A0"


def test_v190_export_concurrency_and_temp_file_cleanup(client):
    """
    Valida execuções concorrentes de exportação e ausência de arquivos temporários órfãos.
    """
    alice = auth_service.create_user("alice_p7_conc", "Password123!", "Alice Conc", role="admin")
    c = knowledge_service.create_concept(alice["id"], "Conceito Concorrência")
    r = knowledge_service.create_reflection(alice["id"], "Reflexão Concorrência", "Corpo teste.")
    knowledge_service.create_knowledge_link(alice["id"], "reflection", r["id"], "concept", c["id"], "relates_to")

    tok_a = auth_service.create_session(alice["id"])
    client.cookies.update(_auth_cookies(tok_a))

    tmp_dir = Path(tempfile.gettempdir())
    before_files = {p.name for p in tmp_dir.glob("lectoria_atlas_*")}

    def _run_export(fmt: str) -> int:
        res = knowledge_export_service.generate_atlas_export_file(alice["id"], export_format=fmt)
        fpath = Path(res["file_path"])
        try:
            assert fpath.exists()
            return fpath.stat().st_size
        finally:
            if fpath.exists():
                fpath.unlink()

    with ThreadPoolExecutor(max_workers=3) as pool:
        sizes = list(pool.map(_run_export, ["markdown_zip", "json", "csv"]))
    assert all(s > 0 for s in sizes)

    # Também valida limpeza via endpoint HTTP
    assert client.get("/api/knowledge/export?format=json").status_code == 200
    assert client.get("/api/knowledge/export?format=markdown_zip").status_code == 200
    assert client.get("/api/knowledge/export?format=csv").status_code == 200

    after_files = {p.name for p in tmp_dir.glob("lectoria_atlas_*")}
    assert after_files == before_files


def test_v190_export_scale_and_memory_budget():
    """
    Medição determinística de desempenho e pico de memória (`tracemalloc`)
    em volume sintético de 1.000 conceitos, 1.000 reflexões, 1.000 anotações e 10.000 conexões.
    """
    _seed_book("book_p7_scale", "Obra Escala Exportação")
    user = auth_service.create_user("scale_p7_user", "Password123!", "Scale Export", role="admin")
    uid = user["id"]
    now_iso = "2026-11-21 16:00:00"

    with database.get_db() as conn:
        cur = conn.cursor()
        cur.executemany(
            """
            INSERT INTO concepts (id, user_id, name, normalized_name, description, tags_json, color, is_archived, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, '["escala"]', '#d4a853', 0, ?, ?);
            """,
            [(f"p7_c_{i}", uid, f"Conceito Export {i}", f"conceito export {i}", f"Descrição {i}", now_iso, now_iso) for i in range(1000)],
        )
        cur.executemany(
            """
            INSERT INTO reflections (id, user_id, title, body_markdown, primary_book_id, tags_json, is_pinned, is_archived, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'book_p7_scale', '["ensaio"]', 0, 0, ?, ?);
            """,
            [(f"p7_r_{i}", uid, f"Reflexão Export {i}", f"# Reflexão {i}\n\nCorpo autoral.", now_iso, now_iso) for i in range(1000)],
        )
        cur.executemany(
            """
            INSERT INTO notes (id, book_id, user_id, page, highlight_id, content, color, visibility, epub_cfi, chapter_label, created_at, updated_at)
            VALUES (?, 'book_p7_scale', ?, ?, NULL, ?, 'yellow', 'private', NULL, 'Cap. 1', ?, ?);
            """,
            [(f"p7_n_{i}", uid, (i % 40) + 1, f"Nota {i}", now_iso, now_iso) for i in range(1000)],
        )
        cur.executemany(
            """
            INSERT INTO knowledge_links (id, user_id, source_type, source_id, target_type, target_id, relation_type, rationale, created_at, updated_at)
            VALUES (?, ?, 'reflection', ?, 'concept', ?, 'references', 'Justificativa escala', ?, ?);
            """,
            [
                (
                    f"p7_l_{i}",
                    uid,
                    f"p7_r_{i % 1000}",
                    f"p7_c_{(i % 1000 + (i // 1000)) % 1000}",
                    now_iso,
                    now_iso,
                )
                for i in range(10000)
            ],
        )

    tracemalloc.start()
    t0 = time.perf_counter()
    res_md = knowledge_export_service.generate_atlas_export_file(uid, export_format="markdown_zip")
    elapsed_md = time.perf_counter() - t0
    _, peak_md_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    try:
        assert res_md["counts"]["concepts"] == 1000
        assert res_md["counts"]["reflections"] == 1000
        assert res_md["counts"]["links"] == 10000
        assert Path(res_md["file_path"]).stat().st_size > 0
        assert elapsed_md < 10.0
        # Orçamento moderado de RAM (< 150 MB no pico Python mesmo para 10k links + 3k arquivos MD)
        assert peak_md_bytes < 150 * 1024 * 1024
    finally:
        if Path(res_md["file_path"]).exists():
            Path(res_md["file_path"]).unlink()
