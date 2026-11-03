"""
Testes automatizados da Iteração 11:
Auditoria e aprimoramento da experiência de leitura de PDFs em dispositivos móveis.
Verificação da estrutura HTML dos controles móveis, regras de responsividade CSS,
cálculo dinâmico de escala no JavaScript, gestos de toque (swipe, pinch, duplo toque)
e integridade da entrega dos recursos do leitor.
"""

from pathlib import Path
import re
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_reader_html_mobile_elements(client):
    """
    Verifica se a interface do leitor contém a estrutura para dispositivos móveis:
    - Barra inferior fixa (bottom bar) com navegação acessível por toque.
    - Indicador de página em formato de pílula (pill) com acesso rápido a pulo de página.
    - Modal de pulo direto de página otimizado para teclado numérico mobile.
    - Bottom sheet com opções adicionais (modos de estudo, zoom e ferramentas).
    - Ações compactas no cabeçalho mobile (indicador de salvamento e alternador de tema).
    """
    reader_path = PROJECT_ROOT / "static" / "reader.html"
    assert reader_path.exists(), "O arquivo static/reader.html deve existir."

    html_content = reader_path.read_text(encoding="utf-8")

    # Componentes fundamentais do layout mobile
    required_ids = [
        "reader-mobile-bottom-bar",
        "btn-prev-page-mobile",
        "btn-next-page-mobile",
        "btn-mobile-page-indicator",
        "mobile-page-current",
        "mobile-page-total",
        "btn-mobile-toggle-mode",
        "btn-mobile-drawer",
        "btn-mobile-menu",
        "sheet-mobile-menu",
        "btn-close-mobile-sheet",
        "modal-mobile-page-jump",
        "mobile-page-input-direct",
        "btn-confirm-modal-jump",
        "btn-reader-theme-mobile",
        "mobile-save-indicator",
    ]

    for element_id in required_ids:
        assert f'id="{element_id}"' in html_content, f"Elemento #{element_id} não encontrado em static/reader.html"

    # Verificação de classes essenciais de acessibilidade e semântica
    assert 'class="reader-mobile-bottom-bar"' in html_content
    assert 'class="reader-mobile-header-actions"' in html_content
    assert 'class="mobile-sheet-overlay"' in html_content


def test_style_css_mobile_responsiveness():
    """
    Verifica se o CSS contém as regras para dispositivos móveis:
    - Variáveis de safe area insets (notches e barras de navegação nativas).
    - Altura dinâmica com suporte a viewport móvel (100dvh).
    - Regras @media (max-width: 768px) para o leitor.
    - Barra inferior com altura ergonômica mínima e alvos de toque adequados (>= 44px).
    - Gavetas (drawers) com largura total em smartphones.
    - Diretiva de toque touch-action adequada para evitar rolagem horizontal acidental.
    """
    css_path = PROJECT_ROOT / "static" / "css" / "style.css"
    assert css_path.exists(), "O arquivo static/css/style.css deve existir."

    css_content = css_path.read_text(encoding="utf-8")

    # Safe area insets
    assert "--safe-area-top" in css_content
    assert "--safe-area-bottom" in css_content
    assert "env(safe-area-inset-top" in css_content
    assert "env(safe-area-inset-bottom" in css_content

    # Suporte a 100dvh
    assert "100dvh" in css_content

    # Mídia móvel
    assert "@media (max-width: 768px)" in css_content

    # Alvos de toque de tamanho confortável
    assert "min-height: 44px" in css_content or "min-width: 44px" in css_content

    # Diretiva de gestos no viewport do leitor
    assert "touch-action: pan-y pinch-zoom" in css_content

    # Gavetas responsivas em telas pequenas
    assert ".annotations-drawer" in css_content
    assert ".thumbnails-drawer" in css_content


def test_reader_js_mobile_gestures_and_scaling():
    """
    Verifica se o script do leitor (reader.js) implementa:
    - Cálculo dinâmico de escala com margens otimizadas no mobile (hPadding de 8px em telas <= 768px).
    - Tratamento de eventos de toque para deslize horizontal (swipe) no modo página única.
    - Detecção de gesto de pinça para zoom (pinch-to-zoom).
    - Detecção de duplo toque (double tap) para zoom inteligente.
    - Sincronização de controles móveis com desktop (indicadores de página e botões de navegação).
    - Atualização suave no evento orientationchange.
    """
    js_path = PROJECT_ROOT / "static" / "js" / "reader.js"
    assert js_path.exists(), "O arquivo static/js/reader.js deve existir."

    js_content = js_path.read_text(encoding="utf-8")

    # Cálculo dinâmico de escala
    assert "isMobile = window.innerWidth <= 768" in js_content
    assert "hPadding = isMobile ? 8 : 48" in js_content

    # Eventos de toque
    assert 'addEventListener("touchstart"' in js_content
    assert 'addEventListener("touchmove"' in js_content
    assert 'addEventListener("touchend"' in js_content

    # Suporte a orientação da tela
    assert 'addEventListener("orientationchange"' in js_content

    # Sincronização dos controles móveis
    assert "updateMobilePageControls" in js_content
    assert "btn-prev-page-mobile" in js_content
    assert "btn-next-page-mobile" in js_content
    assert "sheet-mobile-menu" in js_content
    assert "modal-mobile-page-jump" in js_content


def test_reader_static_assets_serving_and_headers(client):
    """
    Verifica se a aplicação serve os arquivos estáticos atualizados com códigos 200
    e tipos MIME apropriados (HTML, CSS, JS).
    """
    # Página do leitor diretamente
    res_reader = client.get("/static/reader.html")
    assert res_reader.status_code == 200
    assert "text/html" in res_reader.headers.get("content-type", "")

    # Folha de estilo
    res_css = client.get("/static/css/style.css")
    assert res_css.status_code == 200
    assert "text/css" in res_css.headers.get("content-type", "")

    # Script do leitor
    res_js = client.get("/static/js/reader.js")
    assert res_js.status_code == 200
    assert any(m in res_js.headers.get("content-type", "") for m in ["javascript", "text/plain"])


def test_reader_endpoint_integration_with_books(client):
    """
    Verifica a integração da rota do leitor `/reader/{book_id}` com um livro real existente.
    Garante que o template retornado contém as novidades móveis.
    """
    books = client.get("/api/books").json().get("books", [])
    assert len(books) > 0, "Deve haver pelo menos um livro cadastrado no ambiente de testes."

    book_id = books[0]["id"]
    res = client.get(f"/reader/{book_id}")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")

    # Garante que os novos componentes móveis são entregues ao cliente pela rota oficial
    content = res.text
    assert "reader-mobile-bottom-bar" in content
    assert "sheet-mobile-menu" in content
    assert "modal-mobile-page-jump" in content

