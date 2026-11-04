"""
Testes automatizados para a interface adaptativa (mobile/tablet),
correção de rolagem e recursos do leitor no Lectoria (Iteração 18).
"""

from pathlib import Path


def test_epubjs_vendor_assets_exist():
    vendor_dir = Path("static/vendor/epubjs")
    assert vendor_dir.is_dir()
    epub_min = vendor_dir / "epub.min.js"
    jszip_min = vendor_dir / "jszip.min.js"
    license_file = vendor_dir / "LICENSE"

    assert epub_min.is_file() and epub_min.stat().st_size > 100_000
    assert jszip_min.is_file() and jszip_min.stat().st_size > 50_000
    assert license_file.is_file() and "FuturePress" in license_file.read_text()


def test_reader_html_components_exist():
    reader_html = Path("static/reader.html").read_text(encoding="utf-8")

    # 1. Contêiner do leitor EPUB
    assert 'id="epub-wrapper"' in reader_html
    assert 'id="epub-viewer"' in reader_html

    # 2. Barra flutuante e acoplável para tablets
    assert 'id="tablet-dock-toolbar"' in reader_html
    assert 'id="btn-tablet-dock-side"' in reader_html
    assert 'id="tablet-mode-badge"' in reader_html
    assert 'id="tablet-page-pill"' in reader_html
    assert 'id="btn-tablet-prev"' in reader_html
    assert 'id="btn-tablet-next"' in reader_html

    # 3. Painel de Índice de Capítulos (TOC)
    assert 'id="toc-drawer"' in reader_html
    assert 'id="toc-list"' in reader_html

    # 4. Verificação de carregamento dinâmico sob demanda do EPUB.js (sem penalizar PDFs)
    reader_js = Path("static/js/reader.js").read_text(encoding="utf-8")
    assert "/static/vendor/epubjs/jszip.min.js" in reader_js
    assert "/static/vendor/epubjs/epub.min.js" in reader_js
    assert "ensureEpubScriptsLoaded" in reader_js
    # Garante que não são carregados estaticamente no HTML de forma a penalizar leitura de PDF
    assert '<script src="/static/vendor/epubjs/jszip.min.js">' not in reader_html


def test_style_css_rules_for_tablet_and_scroll():
    css_text = Path("static/css/style.css").read_text(encoding="utf-8")

    # 1. Tema Sépia
    assert '[data-theme="sepia"]' in css_text
    assert "--reader-bg: #efe4ca;" in css_text

    # 2. Badges de formato
    assert ".tag-format-pdf" in css_text
    assert ".tag-format-epub" in css_text

    # 3. Barra para tablet
    assert ".tablet-dock-toolbar" in css_text
    assert ".dock-right" in css_text
    assert ".dock-left" in css_text

    # 4. Prevenção de rolagem no desenho/borracha (touch-action)
    assert "touch-action: none !important;" in css_text
    assert ".tool-active-draw" in css_text


def test_reader_js_scroll_and_progress_guards():
    js_text = Path("static/js/reader.js").read_text(encoding="utf-8")

    # 1. Flags defensivas contra race conditions e saltos
    assert "isProgrammaticScroll" in js_text
    assert "isRestoringProgress" in js_text

    # 2. Guarda contra sobrescrita de progresso em restauração e rolagem programática
    assert "if (isRestoringProgress || isProgrammaticScroll) return;" in js_text

    # 3. Compensação de altura no carregamento virtualizado de slots
    assert "viewportEl.scrollTop +=" in js_text

    # 4. Inicializador nativo do EPUB
    assert "function initEpubReader" in js_text
    assert "function applyEpubTheme" in js_text
    assert "function changeEpubFontSize" in js_text
    assert "function renderEpubToc" in js_text

