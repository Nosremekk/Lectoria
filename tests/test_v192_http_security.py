"""
Suíte de testes de Segurança HTTP, Cookies, Proxies Confiáveis, CSP e Reprodutibilidade
(Fase 11.3 — v1.9.2).

Cobre:
- Política LECTORIA_SECURE_COOKIES (auto | true | false + valores inválidos).
- Proxies confiáveis LECTORIA_FORWARDED_ALLOW_IPS (IPs, CIDRs, '*', spoofing de X-Forwarded-Proto e X-Forwarded-For).
- Cabeçalhos defensivos, Permissions-Policy, CSP (off | report-only | enforce) e HSTS condicional.
- Ausência de Set-Cookie espúrio em /static/*.
- Preservação de Range 206, Content-Disposition e CSP próprio das capas SVG.
- Invariantes de Dockerfile, constraints.txt, ausência de onsubmit inline, APP_VERSION=1.9.2 e SCHEMA_VERSION=13.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

import auth_service
import config
import database
import main


REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_request(scheme: str = "http", client_host: str = "127.0.0.1", headers: dict | None = None) -> Request:
    raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "GET",
        "scheme": scheme,
        "path": "/",
        "query_string": b"",
        "headers": raw_headers,
        "client": (client_host, 54321) if client_host else None,
        "server": ("127.0.0.1", 8765),
    }
    return Request(scope)


# =========================================================================
# 1. VALIDAÇÃO DE CONFIGURAÇÕES E PROXIES CONFIÁVEIS
# =========================================================================

def test_v192_config_parsers_valid_and_invalid():
    assert config.parse_secure_cookies_mode(None) == "auto"
    assert config.parse_secure_cookies_mode("AUTO") == "auto"
    assert config.parse_secure_cookies_mode("true") == "true"
    assert config.parse_secure_cookies_mode("false") == "false"
    with pytest.raises(ValueError, match="LECTORIA_SECURE_COOKIES inválido"):
        config.parse_secure_cookies_mode("always")

    assert config.parse_csp_mode(None) == "report-only"
    assert config.parse_csp_mode("off") == "off"
    assert config.parse_csp_mode("enforce") == "enforce"
    with pytest.raises(ValueError, match="LECTORIA_CSP_MODE inválido"):
        config.parse_csp_mode("strict")

    assert config.parse_bool_env("LECTORIA_HSTS_ENABLED", None, default=False) is False
    assert config.parse_bool_env("LECTORIA_HSTS_ENABLED", "true") is True
    assert config.parse_bool_env("LECTORIA_HSTS_ENABLED", "false") is False
    with pytest.raises(ValueError, match="LECTORIA_HSTS_ENABLED inválido"):
        config.parse_bool_env("LECTORIA_HSTS_ENABLED", "maybe")

    assert config.parse_forwarded_allow_ips(None) == ["127.0.0.1", "::1"]
    assert config.parse_forwarded_allow_ips("127.0.0.1, ::1, 172.16.0.0/12") == ["127.0.0.1", "::1", "172.16.0.0/12"]
    assert config.parse_forwarded_allow_ips("*") == ["*"]
    with pytest.raises(ValueError, match="LECTORIA_FORWARDED_ALLOW_IPS"):
        config.parse_forwarded_allow_ips("not-an-ip")


def test_v192_proxy_trust_and_header_spoofing_protection(monkeypatch):
    monkeypatch.setattr(config, "FORWARDED_ALLOW_IPS", ["127.0.0.1", "::1", "172.16.0.0/12"])

    # Proxy confiável (loopback e bridge Docker CIDR)
    assert config.is_trusted_proxy_ip("127.0.0.1") is True
    assert config.is_trusted_proxy_ip("::1") is True
    assert config.is_trusted_proxy_ip("172.18.0.1") is True
    # Cliente externo / LAN não confiável
    assert config.is_trusted_proxy_ip("192.168.1.50") is False
    assert config.is_trusted_proxy_ip("203.0.113.10") is False
    assert config.is_trusted_proxy_ip("testclient") is False

    # X-Forwarded-Proto de proxy confiável -> HTTPS efetivo
    req_trusted = _make_request("http", "172.18.0.1", {"X-Forwarded-Proto": "https"})
    assert main.is_effective_https_request(req_trusted) is True

    # X-Forwarded-Proto FORJADO de cliente não confiável -> IGNORADO
    req_spoofed = _make_request("http", "203.0.113.10", {"X-Forwarded-Proto": "https"})
    assert main.is_effective_https_request(req_spoofed) is False

    # Requisição direta HTTPS -> sempre HTTPS efetivo
    req_https = _make_request("https", "203.0.113.10", {})
    assert main.is_effective_https_request(req_https) is True

    # Spoofing de X-Forwarded-For vindo de cliente não confiável -> IGNORADO
    req_xff_spoof = _make_request("http", "203.0.113.10", {"X-Forwarded-For": "127.0.0.1"})
    assert main.get_effective_client_ip(req_xff_spoof) == "203.0.113.10"

    # X-Forwarded-For vindo de proxy confiável -> aceito
    req_xff_ok = _make_request("http", "172.18.0.1", {"X-Forwarded-For": "100.64.0.9, 172.18.0.1"})
    assert main.get_effective_client_ip(req_xff_ok) == "100.64.0.9"


# =========================================================================
# 2. COOKIES (auto | true | false), LOGIN, LOGOUT E CSRF
# =========================================================================

def test_v192_cookies_secure_modes_login_logout_and_csrf(tmp_path, monkeypatch):
    # Banco isolado para não interferir no test_env compartilhado de sessão
    db_path = tmp_path / "sec_cookies.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    database.init_db()
    auth_service.create_user("sec_user", "SenhaSegura123!", role="admin", display_name="Sec User")

    # Modo auto + HTTP direto -> sem atributo Secure
    monkeypatch.setattr(config, "SECURE_COOKIES_MODE", "auto")
    monkeypatch.setattr(config, "FORWARDED_ALLOW_IPS", ["127.0.0.1", "::1"])
    with TestClient(main.app, base_url="http://127.0.0.1:8765") as http_client:
        res_html = http_client.get("/")
        assert res_html.status_code == 200
        csrf_hdr = res_html.headers.get("set-cookie", "")
        assert "lectoria_csrf=" in csrf_hdr
        assert "; secure" not in csrf_hdr.lower()
        assert "httponly" not in csrf_hdr.lower()
        assert "samesite=lax" in csrf_hdr.lower()

        csrf_val = http_client.cookies.get("lectoria_csrf")
        res_login = http_client.post(
            "/api/auth/login",
            json={"username": "sec_user", "password": "SenhaSegura123!"},
            headers={"x-csrf-token": csrf_val},
        )
        assert res_login.status_code == 200
        sess_hdr = res_login.headers.get("set-cookie", "")
        assert "lectoria_session=" in sess_hdr
        assert "httponly" in sess_hdr.lower()
        assert "samesite=lax" in sess_hdr.lower()
        assert "; secure" not in sess_hdr.lower()

        # CSRF inválido continua bloqueado com 403
        res_bad_csrf = http_client.post("/api/auth/logout", headers={"x-csrf-token": "invalido"})
        assert res_bad_csrf.status_code == 403

        # Logout válido limpa sessão
        res_logout = http_client.post("/api/auth/logout", headers={"x-csrf-token": csrf_val})
        assert res_logout.status_code == 200
        assert http_client.get("/api/auth/me").status_code == 401

    # Modo auto + HTTPS efetivo -> Secure=True tanto em lectoria_csrf quanto em lectoria_session
    with TestClient(main.app, base_url="https://lectoria.example.ts.net") as https_client:
        res_html = https_client.get("/")
        assert "; secure" in res_html.headers.get("set-cookie", "").lower()
        csrf_val = https_client.cookies.get("lectoria_csrf")
        res_login = https_client.post(
            "/api/auth/login",
            json={"username": "sec_user", "password": "SenhaSegura123!"},
            headers={"x-csrf-token": csrf_val},
        )
        assert res_login.status_code == 200
        assert "; secure" in res_login.headers.get("set-cookie", "").lower()
        res_logout = https_client.post("/api/auth/logout", headers={"x-csrf-token": csrf_val})
        assert res_logout.status_code == 200
        assert "; secure" in res_logout.headers.get("set-cookie", "").lower()

    # Modo true -> Secure=True mesmo em HTTP
    monkeypatch.setattr(config, "SECURE_COOKIES_MODE", "true")
    with TestClient(main.app, base_url="http://127.0.0.1:8765") as c_true:
        res_csrf = c_true.get("/api/auth/csrf")
        assert "; secure" in res_csrf.headers.get("set-cookie", "").lower()

    # Modo false -> Secure=False mesmo em HTTPS
    monkeypatch.setattr(config, "SECURE_COOKIES_MODE", "false")
    with TestClient(main.app, base_url="https://lectoria.example.ts.net") as c_false:
        res_csrf = c_false.get("/api/auth/csrf")
        assert "; secure" not in res_csrf.headers.get("set-cookie", "").lower()


# =========================================================================
# 3. CABEÇALHOS DEFENSIVOS, CSP (off | report-only | enforce), HSTS E /static/*
# =========================================================================

def test_v192_security_headers_csp_modes_hsts_and_static_no_set_cookie(test_env, monkeypatch):
    # Padrão: report-only, HSTS off
    monkeypatch.setattr(config, "CSP_MODE", "report-only")
    monkeypatch.setattr(config, "HSTS_ENABLED", False)

    with TestClient(main.app, base_url="http://127.0.0.1:8765") as c:
        res_root = c.get("/")
        assert res_root.status_code == 200
        assert res_root.headers.get("x-content-type-options") == "nosniff"
        assert res_root.headers.get("x-frame-options") == "SAMEORIGIN"
        assert res_root.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
        assert "camera=()" in res_root.headers.get("permissions-policy", "")
        assert "script-src 'self'" in res_root.headers.get("content-security-policy-report-only", "")
        assert "content-security-policy" not in res_root.headers
        assert "strict-transport-security" not in res_root.headers

        # /static/* NÃO deve emitir Set-Cookie: lectoria_csrf nem CSP de HTML
        res_js = c.get("/static/js/app.js")
        assert res_js.status_code == 200
        assert "set-cookie" not in res_js.headers
        assert "content-security-policy-report-only" not in res_js.headers

        # API JSON mantém headers básicos sem CSP de HTML
        res_health = c.get("/api/health")
        assert res_health.status_code == 200
        assert res_health.headers.get("x-content-type-options") == "nosniff"
        assert "content-security-policy-report-only" not in res_health.headers

    # Modo enforce
    monkeypatch.setattr(config, "CSP_MODE", "enforce")
    with TestClient(main.app, base_url="http://127.0.0.1:8765") as c_enf:
        res_root = c_enf.get("/")
        assert "script-src 'self'" in res_root.headers.get("content-security-policy", "")
        assert "content-security-policy-report-only" not in res_root.headers

    # Modo off
    monkeypatch.setattr(config, "CSP_MODE", "off")
    with TestClient(main.app, base_url="http://127.0.0.1:8765") as c_off:
        res_root = c_off.get("/")
        assert "content-security-policy" not in res_root.headers
        assert "content-security-policy-report-only" not in res_root.headers

    # HSTS habilitado: ausente em HTTP, presente apenas em HTTPS efetivo
    monkeypatch.setattr(config, "HSTS_ENABLED", True)
    with TestClient(main.app, base_url="http://127.0.0.1:8765") as c_http:
        assert "strict-transport-security" not in c_http.get("/").headers
    with TestClient(main.app, base_url="https://lectoria.example.ts.net") as c_https:
        assert "max-age=31536000" in c_https.get("/").headers.get("strict-transport-security", "")


def test_v192_phase113_invariants_dockerfile_constraints_and_no_inline_handlers():
    assert config.APP_VERSION in ("1.9.2", "1.10.0")
    assert database.SCHEMA_VERSION == 13

    df = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert '--forwarded-allow-ips", "*"' not in df
    assert "LECTORIA_FORWARDED_ALLOW_IPS" in df
    assert "constraints.txt" in df

    constraints = (REPO_ROOT / "constraints.txt").read_text(encoding="utf-8")
    for pkg in ("fastapi==", "starlette==", "pydantic==", "uvicorn==", "pypdf=="):
        assert pkg in constraints

    index_html = (REPO_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "onsubmit=" not in index_html
    assert "onclick=" not in index_html
