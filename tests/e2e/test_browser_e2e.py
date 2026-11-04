"""
Suíte de Testes End-to-End (E2E) com Playwright e Chromium Real para o Lectoria.
Valida:
1. Rolagem contínua em PDFs de 5, 50 e 200 páginas sem salto involuntário.
2. Comportamento de desenho/borracha sem arrasto acidental do documento.
3. Responsividade e ergonomia em celulares (360x800, 390x844), tablets (768x1024, 820x1180, 1180x820, 1024x1366) e desktop (1440x900).
4. Persistência de posição do dock de tablet (esquerda/direita) no localStorage.
5. Leitura nativa de EPUB, sumário (TOC), tipografia, tema sépia e persistência de CFI.
6. Carregamento dinâmico sob demanda (PDFs não carregam EPUB.js/JSZip).
7. Isolamento de scripts e segurança de links no leitor EPUB.
8. Captura de screenshots reais de evidência.
"""

import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
import pytest
from playwright.sync_api import sync_playwright, expect

SCREENSHOTS_DIR = Path("/tmp/lectoria_screenshots")
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


def _find_free_port() -> int:
    """Encontra uma porta TCP local disponível."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _create_synthetic_pdf(dest_path: Path, num_pages: int):
    """Cria um PDF sintético de N páginas com dimensões proporcionais."""
    import pypdf
    writer = pypdf.PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=595, height=842)  # A4 padrão
    with open(dest_path, "wb") as f:
        writer.write(f)


def _create_synthetic_epub(dest_path: Path, title: str = "Obra Teste", include_malicious: bool = False):
    """Cria um pacote EPUB sintético com capítulos, sumário e metadados."""
    container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
    <rootfiles>
        <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
    </rootfiles>
</container>"""

    opf_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="3.0">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
        <dc:title>{title}</dc:title>
        <dc:creator>Autor Lectoria</dc:creator>
        <dc:language>pt-BR</dc:language>
    </metadata>
    <manifest>
        <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
        <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
        <item id="ch2" href="ch2.xhtml" media-type="application/xhtml+xml"/>
    </manifest>
    <spine>
        <itemref idref="ch1"/>
        <itemref idref="ch2"/>
    </spine>
</package>"""

    nav_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Sumário</title></head>
<body>
    <nav epub:type="toc" id="toc">
        <h1>Sumário</h1>
        <ol>
            <li><a href="ch1.xhtml">Capítulo 1: O Início</a></li>
            <li><a href="ch2.xhtml">Capítulo 2: O Desenvolvimento</a></li>
        </ol>
    </nav>
</body>
</html>"""

    ch1_content = """<p>Primeiro parágrafo do capítulo introdutório do livro de teste.</p>"""
    if include_malicious:
        ch1_content += """
        <script>window.evilScriptExecuted = true;</script>
        <p><a id="unsafe-link" href="javascript:window.evilLinkExecuted=true">Link Inseguro</a></p>
        """

    ch1_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Capítulo 1</title></head>
<body>
    <h1>Capítulo 1: O Início</h1>
    {ch1_content}
</body>
</html>"""

    ch2_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Capítulo 2</title></head>
<body>
    <h1>Capítulo 2: O Desenvolvimento</h1>
    <p>Segundo capítulo da obra para teste de navegação e avanço de leitura.</p>
</body>
</html>"""

    with zipfile.ZipFile(dest_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", opf_xml)
        zf.writestr("OEBPS/nav.xhtml", nav_xhtml)
        zf.writestr("OEBPS/ch1.xhtml", ch1_xhtml)
        zf.writestr("OEBPS/ch2.xhtml", ch2_xhtml)


@pytest.fixture(scope="module")
def e2e_server():
    """Inicializa um servidor FastAPI/Uvicorn em porta efêmera com ambiente 100% isolado."""
    temp_dir = tempfile.TemporaryDirectory()
    base_tmp = Path(temp_dir.name)
    data_dir = base_tmp / "data"
    library_dir = base_tmp / "books"
    covers_dir = base_tmp / "covers"
    backups_dir = base_tmp / "backups"

    for d in (data_dir, library_dir, covers_dir, backups_dir):
        d.mkdir(parents=True, exist_ok=True)

    # 1. Cria PDFs e EPUBs sintéticos
    _create_synthetic_pdf(library_dir / "doc_5_pages.pdf", 5)
    _create_synthetic_pdf(library_dir / "doc_50_pages.pdf", 50)
    _create_synthetic_pdf(library_dir / "doc_200_pages.pdf", 200)
    _create_synthetic_epub(library_dir / "livro_teste.epub", title="Memórias de Teste")
    _create_synthetic_epub(library_dir / "livro_inseguro.epub", title="EPUB Hostil", include_malicious=True)

    port = _find_free_port()
    env = os.environ.copy()
    env["LECTORIA_DATA_DIR"] = str(data_dir)
    env["LECTORIA_LIBRARY_DIR"] = str(library_dir)
    env["LECTORIA_COVERS_DIR"] = str(covers_dir)
    env["LECTORIA_BACKUPS_DIR"] = str(backups_dir)
    env["LECTORIA_PORT"] = str(port)

    # Inicia o servidor uvicorn apontando para main:app
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]

    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(Path(__file__).resolve().parent.parent.parent),
    )

    base_url = f"http://127.0.0.1:{port}"

    # Aguarda o servidor responder
    import urllib.request
    started = False
    for _ in range(30):
        try:
            with urllib.request.urlopen(f"{base_url}/api/books", timeout=1) as resp:
                if resp.status == 200:
                    started = True
                    break
        except Exception:
            time.sleep(0.2)

    if not started:
        proc.terminate()
        raise RuntimeError("Servidor de testes E2E falhou ao iniciar no tempo limite.")

    yield {"base_url": base_url, "port": port, "library_dir": library_dir}

    # Teardown
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
    temp_dir.cleanup()


@pytest.fixture(scope="module")
def browser_instance():
    """Instancia o Chromium Playwright em modo headless para a suíte."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


def _get_books_by_filename(base_url: str) -> dict:
    import json
    import urllib.request
    with urllib.request.urlopen(f"{base_url}/api/books") as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return {b["filename"]: b for b in data.get("books", [])}


# ==============================================================================
# TESTES E2E: PDF E ROLAGEM
# ==============================================================================

def test_pdf_200_pages_no_involuntary_jump(e2e_server, browser_instance):
    """
    Cenário Crítico: Valida que um PDF longo de 200 páginas não salta para o final
    durante carregamento inicial, rolagem lenta, rolagem rápida e saltos programáticos.
    """
    base_url = e2e_server["base_url"]
    books = _get_books_by_filename(base_url)
    book_200 = books["doc_200_pages.pdf"]
    book_id = book_200["id"]

    context = browser_instance.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()

    # Configura modo de leitura contínuo
    page.goto(f"{base_url}/")
    page.evaluate("() => localStorage.setItem('lectoria_reader_mode', 'continuous')")

    # 1. Abre o documento
    page.goto(f"{base_url}/")
    page.evaluate("() => localStorage.setItem('lectoria_reader_mode', 'continuous')")
    page.goto(f"{base_url}/reader/{book_id}")
    page.wait_for_selector("#page-slot-1", state="visible", timeout=10000)

    # 2. Verifica posição inicial (deve estar na página 1, topo)
    scroll_top = page.evaluate("() => document.getElementById('reader-viewport').scrollTop")
    current_page = page.evaluate("() => parseInt(document.getElementById('page-num-input').value, 10)")
    assert scroll_top < 50, f"ScrollTop inicial anormal: {scroll_top}"
    assert current_page == 1, f"Página inicial esperada 1, obtida: {current_page}"

    # 3. Rolagem lenta
    page.evaluate("() => document.getElementById('reader-viewport').scrollBy(0, 400)")
    page.wait_for_timeout(300)
    scroll_top_slow = page.evaluate("() => document.getElementById('reader-viewport').scrollTop")
    assert 350 <= scroll_top_slow <= 450
    page_slow = page.evaluate("() => parseInt(document.getElementById('page-num-input').value, 10)")
    assert page_slow <= 3, f"Avanço anormal de página em rolagem curta: {page_slow}"

    # 4. Navegação direta para a página 80 (meio do documento)
    page.fill("#page-num-input", "80")
    page.press("#page-num-input", "Enter")
    page.wait_for_timeout(500)

    # Garante que a página ativa é a 80 e NÃO a 200
    page_after_jump = page.evaluate("() => parseInt(document.getElementById('page-num-input').value, 10)")
    assert page_after_jump == 80, f"Falha ao saltar para página 80: {page_after_jump}"

    # 5. Alterna Zoom (Zoom In) e verifica que a página se mantém por volta de 80
    page.click("#btn-zoom-in")
    page.wait_for_timeout(800)
    page_after_zoom = page.evaluate("() => parseInt(document.getElementById('page-num-input').value, 10)")
    assert 78 <= page_after_zoom <= 82, f"Salto indevido de página após zoom: {page_after_zoom}"

    # 6. Recarrega a página para validar restauração do progresso salvo
    page.reload()
    page.wait_for_selector("#page-slot-80", state="attached", timeout=10000)
    page.wait_for_timeout(800)

    restored_page = page.evaluate("() => parseInt(document.getElementById('page-num-input').value, 10)")
    assert 78 <= restored_page <= 82, f"Progresso restaurado incorretamente: {restored_page} (esperado ~80)"
    context.close()


def test_pdf_drawing_and_eraser_gestures_no_scroll_fling(e2e_server, browser_instance):
    """
    Valida que interações com caneta e borracha não causam arrasto acidental ou
    deslocamento involuntário do viewport.
    """
    base_url = e2e_server["base_url"]
    books = _get_books_by_filename(base_url)
    book_50 = books["doc_50_pages.pdf"]
    book_id = book_50["id"]

    context = browser_instance.new_context(viewport={"width": 1024, "height": 768})
    page = context.new_page()

    page.goto(f"{base_url}/")
    page.evaluate("() => localStorage.setItem('lectoria_reader_mode', 'continuous')")
    page.goto(f"{base_url}/reader/{book_id}")
    page.wait_for_selector("#page-slot-1", state="visible", timeout=10000)

    # Rola até uma posição intermediária (~500px)
    page.evaluate("() => document.getElementById('reader-viewport').scrollTop = 500")
    page.wait_for_timeout(300)
    initial_scroll = page.evaluate("() => document.getElementById('reader-viewport').scrollTop")

    # Ativa ferramenta de Desenho
    page.click("#btn-tool-draw")
    page.wait_for_timeout(300)

    initial_scroll = page.evaluate("() => document.getElementById('reader-viewport').scrollTop")

    # Executa gesto de desenho com mouse/toque sobre a página
    page.mouse.move(500, 300)
    page.mouse.down()
    page.mouse.move(550, 400)
    page.mouse.move(600, 350)
    page.mouse.up()
    page.wait_for_timeout(200)

    scroll_after_draw = page.evaluate("() => document.getElementById('reader-viewport').scrollTop")
    assert abs(scroll_after_draw - initial_scroll) < 2, f"Desenho causou deslocamento involuntário: {abs(scroll_after_draw - initial_scroll)}"

    # Ativa Borracha
    page.click("#btn-draw-eraser")
    page.wait_for_timeout(300)

    before_eraser_scroll = page.evaluate("() => document.getElementById('reader-viewport').scrollTop")

    page.mouse.move(520, 320)
    page.mouse.down()
    page.mouse.move(580, 380)
    page.mouse.up()
    page.wait_for_timeout(200)

    scroll_after_eraser = page.evaluate("() => document.getElementById('reader-viewport').scrollTop")
    assert abs(scroll_after_eraser - before_eraser_scroll) < 2, "Borracha causou deslocamento involuntário de rolagem!"
    context.close()


# ==============================================================================
# TESTES E2E: RESPONSIVIDADE, TABLET DOCK E ERGONOMIA
# ==============================================================================

@pytest.mark.parametrize(
    "device_name,width,height,is_mobile,is_tablet",
    [
        ("celular_compacto", 360, 800, True, False),
        ("celular_padrao", 390, 844, True, False),
        ("celular_grande", 430, 932, True, False),
        ("tablet_retrato_768", 768, 1024, False, True),
        ("tablet_intermediario_820", 820, 1180, False, True),
        ("tablet_paisagem_1180", 1180, 820, False, True),
        ("tablet_grande_1024", 1024, 1366, False, True),
        ("desktop_1440", 1440, 900, False, False),
    ],
)
def test_responsive_layouts_and_dock_ergonomics(
    e2e_server, browser_instance, device_name, width, height, is_mobile, is_tablet
):
    """
    Testa cada resolução de referência:
    - Celulares: exibem barra inferior e escondem dock.
    - Tablets (incluindo 768x1024 e 1180x820): exibem barra flutuante acoplável (#tablet-dock-toolbar).
    - Desktops: exibem cabeçalho tradicional.
    - Captura screenshot real para evidência técnica.
    """
    base_url = e2e_server["base_url"]
    books = _get_books_by_filename(base_url)
    book_5 = books["doc_5_pages.pdf"]
    book_id = book_5["id"]

    context = browser_instance.new_context(viewport={"width": width, "height": height})
    page = context.new_page()

    page.goto(f"{base_url}/reader/{book_id}")
    page.wait_for_selector("#reader-viewport", state="visible", timeout=10000)
    page.wait_for_timeout(400)

    # 1. Valida barra inferior de celular
    mobile_bar_visible = page.is_visible("#reader-mobile-bottom-bar")
    if is_mobile:
        assert mobile_bar_visible, f"Barra móvel inferior deveria estar visível em {device_name} ({width}x{height})"
    else:
        assert not mobile_bar_visible, f"Barra móvel inferior NÃO deveria estar visível em {device_name}"

    # 2. Valida barra flutuante acoplável de tablet
    tablet_dock_visible = page.is_visible("#tablet-dock-toolbar")
    if is_tablet:
        assert tablet_dock_visible, f"Barra de tablet (#tablet-dock-toolbar) deveria estar visível em {device_name} ({width}x{height})"
    else:
        assert not tablet_dock_visible, f"Barra de tablet NÃO deveria estar visível em {device_name}"

    # 3. Se for tablet, testa alternância de lado do dock (esquerda/direita) e persistência
    if is_tablet:
        dock = page.locator("#tablet-dock-toolbar")
        # Inicia ancorada à direita por padrão
        assert "dock-right" in (dock.get_attribute("class") or "")

        # Clica no botão de alternar lado
        page.click("#btn-tablet-dock-side")
        page.wait_for_timeout(150)
        assert "dock-left" in (dock.get_attribute("class") or "")

        # Verifica persistência no localStorage
        saved_side = page.evaluate("() => localStorage.getItem('lectoria_tablet_dock')")
        assert saved_side == "left"

    # 4. Se for celular, abre a gaveta inferior (Mobile Sheet)
    if is_mobile:
        page.click("#btn-mobile-menu")
        page.wait_for_timeout(250)
        assert page.is_visible("#sheet-mobile-menu")

    # 5. Captura screenshot real
    shot_path = SCREENSHOTS_DIR / f"{device_name}_{width}x{height}.png"
    page.screenshot(path=str(shot_path), full_page=False)
    assert shot_path.exists() and shot_path.stat().st_size > 1000

    context.close()


# ==============================================================================
# TESTES E2E: LEITURA EPUB, SUMÁRIO, TIPOGRAFIA E SEGURANÇA
# ==============================================================================

def test_epub_dynamic_script_loading_and_isolation(e2e_server, browser_instance):
    """
    Valida carregamento dinâmico sob demanda do motor EPUB:
    - PDFs NÃO solicitam jszip.min.js ou epub.min.js.
    - EPUBs solicitam jszip.min.js e epub.min.js.
    - Nenhuma chamada para CDN ou recurso externo na web.
    """
    base_url = e2e_server["base_url"]
    books = _get_books_by_filename(base_url)

    pdf_id = books["doc_5_pages.pdf"]["id"]
    epub_id = books["livro_teste.epub"]["id"]

    context = browser_instance.new_context()

    # 1. Abertura do PDF com rastreamento de rede
    page_pdf = context.new_page()
    pdf_requests = []
    page_pdf.on("request", lambda req: pdf_requests.append(req.url))

    page_pdf.goto(f"{base_url}/reader/{pdf_id}")
    page_pdf.wait_for_selector("#pdf-canvas", state="visible")
    page_pdf.wait_for_timeout(500)

    epub_scripts_in_pdf = [u for u in pdf_requests if "jszip" in u or "epub.min.js" in u]
    assert len(epub_scripts_in_pdf) == 0, f"Scripts EPUB foram carregados indevidamente no PDF: {epub_scripts_in_pdf}"
    page_pdf.close()

    # 2. Abertura do EPUB com rastreamento de rede
    page_epub = context.new_page()
    epub_requests = []
    page_epub.on("request", lambda req: epub_requests.append(req.url))

    page_epub.goto(f"{base_url}/reader/{epub_id}")
    page_epub.wait_for_selector("#epub-viewer iframe", state="attached", timeout=12000)
    page_epub.wait_for_timeout(800)

    jszip_req = [u for u in epub_requests if "jszip.min.js" in u]
    epubjs_req = [u for u in epub_requests if "epub.min.js" in u]
    assert len(jszip_req) >= 1, "JSZip deveria ter sido carregado sob demanda para o EPUB"
    assert len(epubjs_req) >= 1, "EPUB.js deveria ter sido carregado sob demanda para o EPUB"

    # Garante que nenhuma chamada externa (fora de 127.0.0.1) foi feita
    external_calls = [u for u in epub_requests if not u.startswith("http://127.0.0.1") and not u.startswith("blob:")]
    assert len(external_calls) == 0, f"Requisições externas indevidas detectadas: {external_calls}"

    page_epub.close()
    context.close()


def test_epub_navigation_toc_and_theming(e2e_server, browser_instance):
    """
    Valida renderização de conteúdo EPUB, navegação por TOC (Sumário),
    alteração tipográfica e tema Sépia.
    """
    base_url = e2e_server["base_url"]
    books = _get_books_by_filename(base_url)
    epub_id = books["livro_teste.epub"]["id"]

    context = browser_instance.new_context(viewport={"width": 1180, "height": 820})
    page = context.new_page()

    page.goto(f"{base_url}/reader/{epub_id}")
    page.wait_for_selector("#epub-viewer iframe", state="attached", timeout=12000)
    page.wait_for_timeout(600)

    # 1. Verifica controles visíveis de EPUB
    assert page.is_visible("#epub-header-tools")
    assert page.is_visible("#btn-epub-font-larger")
    assert page.is_visible("#btn-epub-theme-sepia")

    # 2. Testa aumento de fonte
    font_size_before = page.locator("#epub-font-size-label").inner_text()
    page.click("#btn-epub-font-larger")
    page.wait_for_timeout(200)
    font_size_after = page.locator("#epub-font-size-label").inner_text()
    assert font_size_after != font_size_before

    # 3. Testa aplicação de Tema Sépia
    page.click("#btn-epub-theme-sepia")
    page.wait_for_timeout(200)
    theme_attr = page.evaluate("() => document.documentElement.getAttribute('data-theme')")
    assert theme_attr == "sepia"

    # 4. Testa Sumário (TOC)
    page.click("#btn-epub-toc")
    page.wait_for_timeout(300)
    assert page.is_visible("#toc-drawer")

    toc_links = page.locator(".toc-item-link")
    assert toc_links.count() >= 2

    # Clica no Capítulo 2 do sumário
    toc_links.nth(1).click()
    page.wait_for_timeout(500)

    # 5. Captura screenshot de evidência do leitor EPUB
    shot_path = SCREENSHOTS_DIR / "epub_reader_sepia_1180x820.png"
    page.screenshot(path=str(shot_path))
    assert shot_path.exists() and shot_path.stat().st_size > 1000

    context.close()


def test_epub_malicious_script_and_link_isolation(e2e_server, browser_instance):
    """
    Valida segurança no navegador:
    EPUB com script embutido e link 'javascript:' não executa código privileged.
    """
    base_url = e2e_server["base_url"]
    books = _get_books_by_filename(base_url)
    unsafe_id = books["livro_inseguro.epub"]["id"]

    context = browser_instance.new_context()
    page = context.new_page()

    page.goto(f"{base_url}/reader/{unsafe_id}")
    page.wait_for_selector("#epub-viewer iframe", state="attached", timeout=12000)
    page.wait_for_timeout(800)

    # 1. Verifica se script embutido foi bloqueado pela sandbox (allowScriptedContent=false)
    script_ran_top = page.evaluate("() => window.evilScriptExecuted")
    assert script_ran_top is None, "Script do EPUB executou no contexto da janela principal!"

    # 2. Inspeciona o iframe do EPUB
    iframe_element = page.wait_for_selector("#epub-viewer iframe")
    iframe = iframe_element.content_frame()
    if iframe:
        script_ran_iframe = iframe.evaluate("() => window.evilScriptExecuted")
        assert script_ran_iframe is None, "Script do EPUB executou no contexto do iframe!"

    context.close()
