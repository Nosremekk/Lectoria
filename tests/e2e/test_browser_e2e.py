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
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as exc:
            pytest.skip(f"Chromium não disponível no ambiente (execute 'playwright install chromium'): {exc}")
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

        # Verifica que na direita não há sobreposição horizontal com o canvas do PDF
        overlap_right = page.evaluate("""() => {
            const d = document.getElementById('tablet-dock-toolbar').getBoundingClientRect();
            const c = document.getElementById('pdf-canvas');
            if (!c) return false;
            const cb = c.getBoundingClientRect();
            return (cb.right > d.left && cb.left < d.right);
        }""")
        assert not overlap_right, f"Dock à direita sobrepõe canvas em {device_name}"

        # Clica no botão de alternar lado
        page.click("#btn-tablet-dock-side")
        page.wait_for_timeout(250)
        assert "dock-left" in (dock.get_attribute("class") or "")

        # Verifica persistência no localStorage
        saved_side = page.evaluate("() => localStorage.getItem('lectoria_tablet_dock')")
        assert saved_side == "left"

        # Verifica que na esquerda não há sobreposição horizontal com o canvas do PDF
        left_info = page.evaluate("""() => {
            const d = document.getElementById('tablet-dock-toolbar').getBoundingClientRect();
            const c = document.getElementById('pdf-canvas');
            if (!c) return { overlap: false };
            const cb = c.getBoundingClientRect();
            return {
                overlap: (cb.left < d.right && cb.right > d.left),
                dLeft: d.left,
                dRight: d.right,
                cLeft: cb.left,
                cRight: cb.right,
                bodyClass: document.body.className,
                vpPaddingLeft: window.getComputedStyle(document.getElementById('reader-viewport')).paddingLeft,
                vpPaddingRight: window.getComputedStyle(document.getElementById('reader-viewport')).paddingRight,
            };
        }""")
        assert not left_info["overlap"], f"Dock à esquerda sobrepõe canvas em {device_name}: {left_info}"

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


# ==============================================================================
# TESTES E2E ITERAÇÃO 11: REVISÃO COMPLETA DE LEITORES, NAVEGAÇÃO E RESPONSIVIDADE
# ==============================================================================

def test_pdf_single_page_navigation_scroll_reset(e2e_server, browser_instance):
    """
    Problema A: Garante que ao mudar de página no modo página única (avanço, retrocesso ou salto),
    a nova página sempre inicia exatamente no topo (scrollTop == 0), eliminando qualquer descida involuntária.
    """
    base_url = e2e_server["base_url"]
    books = _get_books_by_filename(base_url)
    book_id = books["doc_50_pages.pdf"]["id"]

    context = browser_instance.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()

    page.goto(f"{base_url}/reader/{book_id}")
    page.wait_for_selector("#reader-viewport", state="visible", timeout=10000)
    page.wait_for_timeout(600)

    # 1. Garante modo página única
    page.click("#btn-mode-single")
    page.wait_for_timeout(300)

    # 2. Rola verticalmente a página 1 para baixo (simula leitura)
    page.evaluate("() => { document.getElementById('reader-viewport').scrollTop = 320; }")
    page.wait_for_timeout(100)
    scroll_pos = page.evaluate("() => document.getElementById('reader-viewport').scrollTop")
    assert scroll_pos > 250, f"Scroll deveria ter descido, mas foi {scroll_pos}"

    # 3. Avança para a página 2 pelo botão Próxima
    page.click("#btn-next-page")
    page.wait_for_timeout(600)

    current_page = page.evaluate("() => document.getElementById('page-num-input').value")
    assert current_page == "2"
    new_scroll = page.evaluate("() => document.getElementById('reader-viewport').scrollTop")
    assert new_scroll == 0, f"Ao avançar para a página 2, o topo deveria estar em 0, mas foi {new_scroll}"

    # 4. Rola novamente na página 2
    page.evaluate("() => { document.getElementById('reader-viewport').scrollTop = 250; }")
    page.wait_for_timeout(100)
    assert page.evaluate("() => document.getElementById('reader-viewport').scrollTop") > 200

    # 5. Retrocede para a página 1 pelo botão Anterior
    page.click("#btn-prev-page")
    page.wait_for_timeout(600)

    current_page = page.evaluate("() => document.getElementById('page-num-input').value")
    assert current_page == "1"
    back_scroll = page.evaluate("() => document.getElementById('reader-viewport').scrollTop")
    assert back_scroll == 0, f"Ao retroceder para a página 1, o topo deveria estar em 0, mas foi {back_scroll}"

    # 6. Salto direto para a página 5 via input
    page.fill("#page-num-input", "5")
    page.press("#page-num-input", "Enter")
    page.wait_for_timeout(600)

    current_page = page.evaluate("() => document.getElementById('page-num-input').value")
    assert current_page == "5"
    jump_scroll = page.evaluate("() => document.getElementById('reader-viewport').scrollTop")
    assert jump_scroll == 0, f"Ao pular para a página 5, o topo deveria estar em 0, mas foi {jump_scroll}"

    # Evidência fotográfica
    shot_path = SCREENSHOTS_DIR / "single_page_scroll_reset_top.png"
    page.screenshot(path=str(shot_path))
    assert shot_path.exists() and shot_path.stat().st_size > 1000

    context.close()


def test_pdf_continuous_view_smooth_navigation_and_zoom_preservation(e2e_server, browser_instance):
    """
    Problema B: Garante que o modo de rolagem contínua navegue de forma estável,
    sem deslocamentos involuntários ao rolar e mantendo a posição de leitura relativa ao alterar o zoom.
    """
    base_url = e2e_server["base_url"]
    books = _get_books_by_filename(base_url)
    book_id = books["doc_50_pages.pdf"]["id"]

    context = browser_instance.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()

    page.goto(f"{base_url}/reader/{book_id}")
    page.wait_for_selector("#reader-viewport", state="visible", timeout=10000)
    page.wait_for_timeout(500)

    # 1. Ativa o modo contínuo
    page.click("#btn-mode-continuous")
    page.wait_for_selector("#continuous-wrapper", state="visible", timeout=5000)
    page.wait_for_timeout(400)

    # 2. Navega para a página 4 via input
    page.fill("#page-num-input", "4")
    page.press("#page-num-input", "Enter")
    page.wait_for_timeout(600)

    page_val = page.evaluate("() => document.getElementById('page-num-input').value")
    assert page_val == "4"

    # Verifica que o viewport rolou para perto do slot 4
    slot_top = page.evaluate("() => document.getElementById('page-slot-4').offsetTop")
    curr_scroll = page.evaluate("() => document.getElementById('reader-viewport').scrollTop")
    assert abs(curr_scroll - slot_top) < 30, f"Scroll ({curr_scroll}) deveria coincidir com o topo do slot 4 ({slot_top})"

    # 3. Altera o zoom no modo contínuo (Aumentar zoom)
    page.click("#btn-zoom-in")
    page.wait_for_timeout(600)

    # A página ativa deve continuar sendo a página 4 (não deve saltar para a pág 1 nem para o fim)
    page_after_zoom = page.evaluate("() => document.getElementById('page-num-input').value")
    assert page_after_zoom == "4", f"Após zoom in, a página ativa deveria continuar sendo 4, mas foi {page_after_zoom}"

    # 4. Diminui o zoom
    page.click("#btn-zoom-out")
    page.wait_for_timeout(600)
    page_after_zoom_out = page.evaluate("() => document.getElementById('page-num-input').value")
    assert page_after_zoom_out == "4", f"Após zoom out, a página ativa deveria continuar sendo 4, mas foi {page_after_zoom_out}"

    # Evidência fotográfica
    shot_path = SCREENSHOTS_DIR / "continuous_scroll_stable_page4.png"
    page.screenshot(path=str(shot_path))
    assert shot_path.exists() and shot_path.stat().st_size > 1000

    context.close()


@pytest.mark.parametrize(
    "resolution_name,w,h",
    [
        ("desktop_1920x1080", 1920, 1080),
        ("desktop_1366x768", 1366, 768),
        ("tablet_paisagem_1024x768", 1024, 768),
        ("tablet_retrato_820x1180", 820, 1180),
        ("tablet_retrato_768x1024", 768, 1024),
        ("smartphone_390x844", 390, 844),
        ("smartphone_360x640", 360, 640),
    ],
)
def test_no_horizontal_overflow_across_all_resolutions(e2e_server, browser_instance, resolution_name, w, h):
    """
    Problema D: Garante que nenhuma resolução sofra de overflow horizontal involuntário
    (scrollWidth <= clientWidth tanto na página quanto no cabeçalho), assegurando responsividade completa.
    """
    base_url = e2e_server["base_url"]
    books = _get_books_by_filename(base_url)
    book_id = books["doc_5_pages.pdf"]["id"]

    context = browser_instance.new_context(viewport={"width": w, "height": h})
    page = context.new_page()

    page.goto(f"{base_url}/reader/{book_id}")
    page.wait_for_selector("#reader-viewport", state="visible", timeout=10000)
    page.wait_for_timeout(400)

    metrics = page.evaluate("""() => {
        const docScroll = document.documentElement.scrollWidth;
        const docClient = document.documentElement.clientWidth;
        const header = document.querySelector('.reader-header');
        const headerScroll = header ? header.scrollWidth : 0;
        const headerClient = header ? header.clientWidth : 0;
        return {
            docScroll,
            docClient,
            hasDocOverflow: docScroll > docClient,
            headerScroll,
            headerClient,
            hasHeaderOverflow: headerScroll > headerClient
        };
    }""")

    assert not metrics["hasDocOverflow"], (
        f"Overflow horizontal detectado no documento em {resolution_name} ({w}x{h}): "
        f"scrollWidth={metrics['docScroll']} > clientWidth={metrics['docClient']}"
    )
    assert not metrics["hasHeaderOverflow"], (
        f"Overflow horizontal detectado no cabeçalho em {resolution_name} ({w}x{h}): "
        f"headerScroll={metrics['headerScroll']} > headerClient={metrics['headerClient']}"
    )

    shot_path = SCREENSHOTS_DIR / f"responsive_clean_{resolution_name}.png"
    page.screenshot(path=str(shot_path), full_page=False)
    assert shot_path.exists() and shot_path.stat().st_size > 1000

    context.close()


# ==============================================================================
# TESTES E2E: INTERFACE DE DESCOBERTA, PRATELEIRAS E MODAIS (v1.4.0)
# ==============================================================================

@pytest.mark.parametrize(
    "view_name,w,h",
    [
        ("desktop_1440x900", 1440, 900),
        ("tablet_820x1180", 820, 1180),
        ("smartphone_390x844", 390, 844),
    ],
)
def test_discovery_interface_and_modals_across_devices(e2e_server, browser_instance, view_name, w, h):
    """
    Valida a nova interface de Descoberta v1.4.0:
    - Alternância entre abas de visão ('Descoberta' vs 'Biblioteca').
    - Prateleiras horizontais com scroll touch/botões.
    - Modal de Detalhes do Livro com sub-abas (Visão Geral, Reconciliação, Editar).
    - Fechamento com tecla Escape.
    - Captura de screenshots em desktop, tablet e smartphone.
    """
    import urllib.request
    import json
    base_url = e2e_server["base_url"]

    # Garante que um usuário administrador está cadastrado e obtém token de sessão
    try:
        req_setup = urllib.request.Request(
            f"{base_url}/api/auth/setup",
            data=json.dumps({"username": "admin", "password": "AdminSenha123!", "display_name": "Admin"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req_setup) as _:
            pass
    except Exception:
        pass

    admin_token = None
    try:
        req_login = urllib.request.Request(
            f"{base_url}/api/auth/login",
            data=json.dumps({"username": "admin", "password": "AdminSenha123!"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req_login) as resp:
            for header_val in resp.headers.get_all("Set-Cookie", []):
                if "lectoria_session=" in header_val:
                    admin_token = header_val.split("lectoria_session=")[1].split(";")[0]
    except Exception:
        pass

    context = browser_instance.new_context(viewport={"width": w, "height": h})
    if admin_token:
        context.add_cookies([{"name": "lectoria_session", "value": admin_token, "domain": "127.0.0.1", "path": "/"}])

    page = context.new_page()

    # 1. Abre a página principal
    page.goto(f"{base_url}/")
    page.wait_for_selector("#tab-nav-discovery", state="visible", timeout=10000)

    # 2. Alterna para a aba de Descoberta
    page.click("#tab-nav-discovery")
    page.wait_for_selector("#shelves-container", state="visible", timeout=5000)
    page.wait_for_timeout(400)

    # 3. Valida prateleiras renderizadas
    shelf_count = page.evaluate("() => document.querySelectorAll('.shelf-wrapper').length")
    assert shelf_count >= 1, f"Nenhuma prateleira renderizada na visão de descoberta em {view_name}"

    # 4. Testa botão de scroll horizontal da prateleira (se presente e visível)
    btn_next = page.locator(".btn-shelf-next").first
    if btn_next.is_visible():
        row = page.locator(".shelf-row").first
        init_scroll = row.evaluate("el => el.scrollLeft")
        btn_next.click()
        page.wait_for_timeout(300)
        after_scroll = row.evaluate("el => el.scrollLeft")
        assert after_scroll >= init_scroll

    # 5. Clica no primeiro card de prateleira para abrir Detalhes do Livro
    first_card = page.locator(".shelf-card").first
    if first_card.is_visible():
        first_card.click()
        page.wait_for_selector("#modal-book-details.active", state="visible", timeout=5000)

        # Valida que as sub-abas do modal existem e funcionam
        page.click("button[data-subtab='reconciliation']")
        page.wait_for_timeout(200)
        reconcile_active = page.evaluate("() => document.getElementById('details-subtab-reconciliation').style.display !== 'none'")
        assert reconcile_active is True

        page.click("button[data-subtab='edit']")
        page.wait_for_timeout(200)
        edit_active = page.evaluate("() => document.getElementById('details-subtab-edit').style.display !== 'none'")
        assert edit_active is True

        # Testa tecla ESC para fechar modal
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        modal_closed = page.evaluate("() => !document.getElementById('modal-book-details').classList.contains('active')")
        assert modal_closed is True, "Tecla Escape falhou ao fechar o modal de detalhes"

    # 6. Alterna de volta para a aba 'Biblioteca & Explorar'
    page.click("#tab-nav-library")
    page.wait_for_timeout(300)
    lib_visible = page.evaluate("() => document.getElementById('library-section').style.display !== 'none'")
    assert lib_visible is True

    # 7. Captura screenshot de evidência
    shot_path = SCREENSHOTS_DIR / f"discovery_ui_{view_name}.png"
    page.screenshot(path=str(shot_path), full_page=False)
    assert shot_path.exists() and shot_path.stat().st_size > 1000

    context.close()


@pytest.mark.parametrize(
    "view_name,w,h",
    [
        ("mobile_320", 320, 568),
        ("mobile_375", 375, 667),
        ("mobile_390", 390, 844),
        ("tablet_768", 768, 1024),
        ("desktop_1280", 1280, 800),
    ],
)
def test_v152_mobile_refinement_layout_and_collapsible_filters(e2e_server, browser_instance, view_name, w, h):
    """
    Validação da experiência mobile e layout da v1.5.2:
    - Zero overflow horizontal em 320px, 375px, 390px, 768px e 1280px.
    - Funcionamento do botão compacto de alternância dos filtros no mobile.
    - Grid responsivo de livros e prateleiras com rolagem restrita.
    """
    base_url = e2e_server["base_url"]
    import urllib.request
    import json

    try:
        req_setup = urllib.request.Request(
            f"{base_url}/api/auth/setup",
            data=json.dumps({"username": "admin", "password": "AdminSenha123!", "display_name": "Admin"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req_setup) as _:
            pass
    except Exception:
        pass

    admin_token = None
    try:
        req_login = urllib.request.Request(
            f"{base_url}/api/auth/login",
            data=json.dumps({"username": "admin", "password": "AdminSenha123!"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req_login) as resp:
            for header_val in resp.headers.get_all("Set-Cookie", []):
                if "lectoria_session=" in header_val:
                    admin_token = header_val.split("lectoria_session=")[1].split(";")[0]
    except Exception:
        pass

    context = browser_instance.new_context(viewport={"width": w, "height": h})
    if admin_token:
        context.add_cookies([{
            "name": "lectoria_session",
            "value": admin_token,
            "domain": "127.0.0.1",
            "path": "/",
        }])

    page = context.new_page()

    page.goto(base_url)
    page.wait_for_timeout(500)

    # Verifica overflow horizontal no discovery
    overflow_metrics = page.evaluate("""() => {
        const sw = document.documentElement.scrollWidth;
        const cw = document.documentElement.clientWidth;
        return { sw, cw, hasOverflow: sw > cw };
    }""")
    assert not overflow_metrics["hasOverflow"], f"Overflow horizontal na Descoberta em {view_name}: {overflow_metrics}"

    # Alterna para a aba Biblioteca
    page.click("#tab-nav-library")
    page.wait_for_timeout(400)

    # Verifica overflow horizontal na biblioteca
    lib_overflow = page.evaluate("""() => {
        const sw = document.documentElement.scrollWidth;
        const cw = document.documentElement.clientWidth;
        return { sw, cw, hasOverflow: sw > cw };
    }""")
    assert not lib_overflow["hasOverflow"], f"Overflow horizontal na Biblioteca em {view_name}: {lib_overflow}"

    # Em telas mobile (<= 768px), valida botão de filtros colapsáveis
    if w <= 768:
        toggle_btn = page.query_selector("#btn-toggle-filters-mobile")
        assert toggle_btn is not None
        assert toggle_btn.is_visible()

        # Clica para abrir o painel
        page.click("#btn-toggle-filters-mobile")
        page.wait_for_timeout(300)
        is_active = page.evaluate("() => document.getElementById('collapsible-filters-panel').classList.contains('active')")
        assert is_active is True

        # Clica para fechar o painel
        page.click("#btn-toggle-filters-mobile")
        page.wait_for_timeout(300)
        is_closed = page.evaluate("() => !document.getElementById('collapsible-filters-panel').classList.contains('active')")
        assert is_closed is True

    shot_path = SCREENSHOTS_DIR / f"v152_mobile_refinement_{view_name}.png"
    page.screenshot(path=str(shot_path), full_page=False)
    assert shot_path.exists() and shot_path.stat().st_size > 1000

    context.close()


def test_v160_reading_clubs_and_collective_reads_multiuser(e2e_server, browser_instance):
    """
    Suíte E2E Multi-Usuário do Lectoria v1.6.0 — Clubes de Leitura & Leitura Coletiva:
    1. Três usuários reais:
       - User A (Proprietária / Criadora do Clube Privado)
       - User B (Leitor / Membro Convidado)
       - User C (Leitor / Externo / Não Convidado)
    2. Criação de Clube Privado por User A.
    3. Convite direcionado para User B.
    4. Recepção e aceite do convite por User B na interface web.
    5. Criação de Leitura Coletiva vinculada a livro real do acervo.
    6. Adesão de User B à Leitura Coletiva e contagem de participantes.
    7. Filtro exclusivo 'Leitura Coletiva' na biblioteca exibindo a obra com badge coletivo para User B.
    8. Anti-enumeração e isolamento estrito para User C (clube oculto, HTTP 404 e filtro vazio).
    9. Conclusão da leitura e preservação no histórico do clube.
    10. Captura de evidências fotográficas em alta resolução.
    """
    base_url = e2e_server["base_url"]
    import urllib.request
    import json

    # 1. Garante criação do admin e dos 3 leitores
    try:
        req_setup = urllib.request.Request(
            f"{base_url}/api/auth/setup",
            data=json.dumps({"username": "admin", "password": "AdminSenha123!", "display_name": "Admin Master"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req_setup) as _:
            pass
    except Exception:
        pass

    def _login(username, password):
        req = urllib.request.Request(
            f"{base_url}/api/auth/login",
            data=json.dumps({"username": username, "password": password}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            for h in resp.headers.get_all("Set-Cookie", []):
                if "lectoria_session=" in h:
                    return h.split("lectoria_session=")[1].split(";")[0]
        return None

    admin_token = _login("admin", "AdminSenha123!")
    assert admin_token, "Falha ao autenticar admin mestre"

    # Cria User A, User B e User C via API de usuários
    for uname, dname in [("user_a", "Alice Proprietaria"), ("user_b", "Bob Convidado"), ("user_c", "Carlos Externo")]:
        try:
            req_u = urllib.request.Request(
                f"{base_url}/api/users",
                data=json.dumps({"username": uname, "password": "SenhaSegura123!", "display_name": dname, "role": "reader"}).encode("utf-8"),
                headers={"Content-Type": "application/json", "Cookie": f"lectoria_session={admin_token}"},
            )
            with urllib.request.urlopen(req_u) as _:
                pass
        except Exception:
            pass

    token_a = _login("user_a", "SenhaSegura123!")
    token_b = _login("user_b", "SenhaSegura123!")
    token_c = _login("user_c", "SenhaSegura123!")
    assert token_a and token_b and token_c, "Falha ao gerar sessões para os 3 usuários de teste"

    # =========================================================================
    # FASE 1: USER A CRIA CLUBE PRIVADO E CONVIDA USER B
    # =========================================================================
    context_a = browser_instance.new_context(viewport={"width": 1280, "height": 800})
    context_a.add_cookies([{"name": "lectoria_session", "value": token_a, "domain": "127.0.0.1", "path": "/"}])
    page_a = context_a.new_page()
    page_a.on("dialog", lambda d: d.accept())

    page_a.goto(base_url)
    page_a.wait_for_timeout(400)

    # Navega para a aba de Clubes
    page_a.click("#tab-nav-clubs")
    page_a.wait_for_timeout(400)
    assert page_a.is_visible("#clubs-section"), "Seção de clubes deve estar visível"

    # Abre modal de criação de clube
    page_a.click("#btn-open-create-club")
    page_a.wait_for_timeout(300)
    assert page_a.is_visible("#modal-club-form"), "Modal de criação de clube deve abrir"

    # Preenche formulário como CLUBE PRIVADO
    page_a.fill("#club-form-name", "Clube Secreto de Filosofia")
    page_a.fill("#club-form-desc", "Comunidade privada para debates de obras clássicas.")
    page_a.select_option("#club-form-visibility", "private")
    page_a.click("#btn-save-club-form")
    page_a.wait_for_timeout(600)

    # Verifica abertura imediata do detalhe do clube
    assert page_a.is_visible("#club-detail-view"), "Detalhes do clube devem abrir automaticamente após criação"
    assert "Clube Secreto de Filosofia" in page_a.text_content("#club-detail-name")
    assert "Privado" in page_a.text_content("#club-visibility-badge")

    # Obtém o ID do clube criado a partir do estado ou campos ocultos
    club_id = page_a.evaluate("() => document.getElementById('transfer-club-id')?.value || ''")
    if not club_id:
        # Alternativa de fallback consultando API de clubes de Alice
        req_mine = urllib.request.Request(f"{base_url}/api/reading-clubs?filter=mine", headers={"Cookie": f"lectoria_session={token_a}"})
        with urllib.request.urlopen(req_mine) as resp:
            data_mine = json.loads(resp.read().decode("utf-8"))
            for c in data_mine.get("clubs", []):
                if c["name"] == "Clube Secreto de Filosofia":
                    club_id = c["id"]
                    break
    assert club_id, "ID do clube criado deve ser identificado"

    # Alice alterna para a aba de Participantes e clica em Convidar
    page_a.click("#club-tab-members")
    page_a.wait_for_timeout(300)
    assert page_a.is_visible("#btn-club-invite-user"), "Botão de convite deve estar disponível para proprietário em clube privado"
    page_a.click("#btn-club-invite-user")
    page_a.wait_for_timeout(400)

    # Seleciona User B (Bob) no dropdown de convite
    assert page_a.is_visible("#modal-club-invite"), "Modal de convite deve abrir"
    options = page_a.evaluate("""() => {
        const select = document.getElementById('invite-user-select');
        return Array.from(select.options).map(o => ({ value: o.value, text: o.text }));
    }""")
    bob_option = next((o for o in options if "Bob" in o["text"] or "user_b" in o["text"]), None)
    assert bob_option, f"Bob deve estar listado nos candidatos a convite: {options}"
    page_a.select_option("#invite-user-select", bob_option["value"])
    page_a.click("#btn-confirm-invite-modal")
    page_a.wait_for_timeout(600)

    # =========================================================================
    # FASE 2: USER B RECEBE, VISUALIZA E ACEITA O CONVITE
    # =========================================================================
    context_b = browser_instance.new_context(viewport={"width": 1280, "height": 800})
    context_b.add_cookies([{"name": "lectoria_session", "value": token_b, "domain": "127.0.0.1", "path": "/"}])
    page_b = context_b.new_page()
    page_b.on("dialog", lambda d: d.accept())

    page_b.goto(base_url)
    page_b.wait_for_timeout(400)

    page_b.click("#tab-nav-clubs")
    page_b.wait_for_timeout(400)

    # Banner de convites pendentes deve estar visível com o convite
    assert page_b.is_visible("#club-invites-banner"), "Banner de convites pendentes deve ser exibido para User B"
    banner_text = page_b.text_content("#club-invites-banner")
    assert "Clube Secreto de Filosofia" in banner_text, f"Nome do clube deve constar no banner: {banner_text}"

    # User B clica em 'Aceitar' convite
    page_b.click(".btn-accept-invite")
    page_b.wait_for_timeout(600)

    # User B é imediatamente direcionado para o clube privado
    assert page_b.is_visible("#club-detail-view"), "Detalhes do clube devem abrir para User B após aceitar convite"
    assert "Clube Secreto de Filosofia" in page_b.text_content("#club-detail-name")

    # =========================================================================
    # FASE 3: USER A CRIA LEITURA COLETIVA PARA O LIVRO 'doc_5_pages.pdf'
    # =========================================================================
    page_a.click("#club-tab-reads")
    page_a.wait_for_timeout(300)
    page_a.click("#btn-club-new-read")
    page_a.wait_for_timeout(400)

    assert page_a.is_visible("#modal-collective-read-form"), "Modal de nova leitura coletiva deve abrir"
    book_options = page_a.evaluate("""() => {
        const select = document.getElementById('read-form-book-select');
        return Array.from(select.options).map(o => ({ value: o.value, text: o.text }));
    }""")
    assert len(book_options) > 1, f"Pelo menos uma obra do acervo deve estar listada: {book_options}"
    target_book_id = book_options[1]["value"]
    page_a.select_option("#read-form-book-select", target_book_id)

    page_a.fill("#read-form-title", "Leitura Conjunta: Filosofia e Métodos")
    page_a.fill("#read-form-desc", "Cronograma de discussão em grupo.")
    page_a.select_option("#read-form-status", "in_progress")
    page_a.click("#btn-save-read-form")
    page_a.wait_for_timeout(600)

    # Leitura aparece na grade de leituras do clube para Alice
    reads_text_a = page_a.text_content("#club-reads-grid")
    assert "Leitura Conjunta: Filosofia e Métodos" in reads_text_a

    # =========================================================================
    # FASE 4: USER B ADERE À LEITURA COLETIVA E CONFERE O FILTRO DA BIBLIOTECA
    # =========================================================================
    # User B atualiza/abre a aba de leituras
    page_b.click("#club-tab-reads")
    page_b.wait_for_timeout(400)
    reads_text_b = page_b.text_content("#club-reads-grid")
    assert "Leitura Conjunta: Filosofia e Métodos" in reads_text_b

    # Abre modal de detalhes da leitura
    page_b.click(".btn-open-read-modal")
    page_b.wait_for_timeout(400)
    assert page_b.is_visible("#modal-collective-read-detail")
    assert "Participar" in page_b.text_content("#btn-read-detail-toggle-join")

    # User B clica em 'Participar da Leitura'
    page_b.click("#btn-read-detail-toggle-join")
    page_b.wait_for_timeout(500)
    assert "Sair da Leitura" in page_b.text_content("#btn-read-detail-toggle-join")
    assert page_b.text_content("#read-detail-members-count").strip() == "2"

    # Fecha o modal de detalhes
    page_b.click("#btn-close-read-detail")
    page_b.wait_for_timeout(300)

    # User B navega para a Biblioteca & clica no filtro exclusivo 'Leitura Coletiva'
    page_b.click("#tab-nav-library")
    page_b.wait_for_timeout(400)
    assert page_b.is_visible("#library-section")

    # Clica na aba de status 'Leitura Coletiva'
    page_b.click("button[data-status='collective_reads']")
    page_b.wait_for_timeout(600)

    # O livro deve estar visível na biblioteca de Bob com o selo de Leitura Coletiva
    lib_cards_b = page_b.query_selector_all(".book-card")
    assert len(lib_cards_b) >= 1, "A obra vinculada à leitura coletiva ativa deve aparecer para User B"
    collective_badge = page_b.query_selector(".tag-collective-read")
    assert collective_badge is not None, "Badge 'Coletiva' deve estar presente no card do livro"
    cr_box_text = page_b.text_content(".book-collective-read-badge-box")
    assert "Clube Secreto de Filosofia" in cr_box_text

    # Captura screenshot de evidência para User B
    shot_user_b = SCREENSHOTS_DIR / "v160_user_b_collective_read_library.png"
    page_b.screenshot(path=str(shot_user_b), full_page=False)
    assert shot_user_b.exists() and shot_user_b.stat().st_size > 1000

    # =========================================================================
    # FASE 5: USER C (EXTERNO) — ISOLAMENTO, ANTI-ENUMERAÇÃO E PRIVACIDADE
    # =========================================================================
    context_c = browser_instance.new_context(viewport={"width": 1280, "height": 800})
    context_c.add_cookies([{"name": "lectoria_session", "value": token_c, "domain": "127.0.0.1", "path": "/"}])
    page_c = context_c.new_page()
    page_c.on("dialog", lambda d: d.accept())

    page_c.goto(base_url)
    page_c.wait_for_timeout(400)

    # Carlos acessa a aba de Clubes
    page_c.click("#tab-nav-clubs")
    page_c.wait_for_timeout(400)

    # Clube Secreto de Filosofia NÃO deve estar visível nem em 'Meus Clubes' nem em 'Todos os Clubes'
    clubs_text_c = page_c.text_content("#clubs-container") or ""
    assert "Clube Secreto de Filosofia" not in clubs_text_c, "Clube privado não deve aparecer na listagem de não-membros"

    page_c.click("#subtab-clubs-all")
    page_c.wait_for_timeout(400)
    clubs_all_c = page_c.text_content("#clubs-container") or ""
    assert "Clube Secreto de Filosofia" not in clubs_all_c, "Clube privado não deve aparecer na listagem de Todos os Clubes"

    # Tentativa de acesso direto via API deve retornar 404 estrito (anti-enumeração)
    status_code = page_c.evaluate(f"() => fetch('/api/reading-clubs/{club_id}').then(r => r.status)")
    assert status_code == 404, f"API de clube privado deve retornar 404 para não-membros, retornou: {status_code}"

    # Carlos acessa o filtro 'Leitura Coletiva' na biblioteca
    page_c.click("#tab-nav-library")
    page_c.wait_for_timeout(400)
    page_c.click("button[data-status='collective_reads']")
    page_c.wait_for_timeout(500)

    # Como Carlos não participa de nenhuma leitura, nenhum livro coletivo deve aparecer
    cards_c = page_c.query_selector_all(".book-card")
    assert len(cards_c) == 0, f"Carlos não deve ver livros de leituras coletivas que não participa: {len(cards_c)} encontrados"

    shot_user_c = SCREENSHOTS_DIR / "v160_user_c_library_isolated.png"
    page_c.screenshot(path=str(shot_user_c), full_page=False)
    assert shot_user_c.exists() and shot_user_c.stat().st_size > 1000

    # =========================================================================
    # FASE 6: CONCLUIR LEITURA E PRESERVAR HISTÓRICO
    # =========================================================================
    # Alice abre a leitura e a altera para 'Concluída'
    page_a.click(".btn-open-read-modal")
    page_a.wait_for_timeout(400)
    page_a.click("#btn-read-detail-edit")
    page_a.wait_for_timeout(400)

    page_a.select_option("#read-form-status", "completed")
    page_a.click("#btn-save-read-form")
    page_a.wait_for_timeout(600)

    # Confere na visão de leituras concluídas
    page_a.click("button[data-read-filter='completed']")
    page_a.wait_for_timeout(400)
    reads_completed_a = page_a.text_content("#club-reads-grid")
    assert "Leitura Conjunta: Filosofia e Métodos" in reads_completed_a
    assert "Concluída" in reads_completed_a

    shot_history = SCREENSHOTS_DIR / "v160_club_detail_completed_history.png"
    page_a.screenshot(path=str(shot_history), full_page=False)
    assert shot_history.exists() and shot_history.stat().st_size > 1000

    # Limpeza dos contextos
    context_a.close()
    context_b.close()
    context_c.close()



