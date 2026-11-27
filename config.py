"""
Configurações centrais do Lectoria.
Mantém parâmetros de porta, diretórios e nome do sistema centralizados, portáveis e configuráveis
via variáveis de ambiente ou arquivo local .env (sem dependências externas).
"""

import ipaddress
import os
from pathlib import Path
from typing import Optional

# Diretório raiz do projeto (resolvido dinamicamente a partir da localização deste arquivo)
BASE_DIR = Path(__file__).resolve().parent

# Se este módulo estiver na pasta 'app/' de um pacote de distribuição onde
# as pastas de dados e manifesto existem na pasta superior:
if BASE_DIR.name == "app" and (BASE_DIR.parent / "manifest.json").exists():
    STORAGE_ROOT = BASE_DIR.parent
else:
    STORAGE_ROOT = BASE_DIR


def load_env_file(env_path: Optional[Path] = None, override: bool = False) -> dict[str, str]:
    """
    Carrega variáveis de configuração a partir de um arquivo .env simples (CHAVE=VALOR).
    Por padrão não sobrescreve variáveis já definidas no ambiente do processo (override=False).
    """
    if env_path:
        target = env_path
    elif os.getenv("LECTORIA_ENV_FILE"):
        target = Path(os.environ["LECTORIA_ENV_FILE"])
    elif (BASE_DIR / ".env").exists():
        target = BASE_DIR / ".env"
    elif (STORAGE_ROOT / ".env").exists():
        target = STORAGE_ROOT / ".env"
    else:
        target = BASE_DIR / ".env"

    loaded: dict[str, str] = {}
    if not target.exists() or not target.is_file():
        return loaded

    try:
        for raw_line in target.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            if not key:
                continue
            loaded[key] = val
            if override or key not in os.environ:
                os.environ[key] = val
    except OSError:
        pass
    return loaded


# Carrega .env local caso exista
load_env_file()

# Nome da aplicação
APP_NAME = os.getenv("LECTORIA_APP_NAME", "Lectoria")
APP_VERSION = "1.10.0"

# Diretório onde os arquivos PDF da biblioteca padrão ficam armazenados
LIBRARY_DIR = Path(
    os.getenv("LECTORIA_LIBRARY_DIR", str(STORAGE_ROOT / "books"))
).expanduser().resolve()

# Diretório onde o banco de dados SQLite e dados persistentes ficam armazenados
DATA_DIR = Path(
    os.getenv("LECTORIA_DATA_DIR", str(STORAGE_ROOT / "data"))
).expanduser().resolve()

# Caminho do banco SQLite principal
DB_PATH = Path(
    os.getenv("LECTORIA_DB_PATH", str(DATA_DIR / "lectoria.db"))
).expanduser().resolve()

# Diretório para cache de capas extraídas e capas personalizadas
COVERS_DIR = Path(
    os.getenv("LECTORIA_COVERS_DIR", str(DATA_DIR / "covers"))
).expanduser().resolve()

# Diretório para backups manuais e automáticos de pré-migração do banco de dados
BACKUPS_DIR = Path(
    os.getenv("LECTORIA_BACKUPS_DIR", str(STORAGE_ROOT / "backups"))
).expanduser().resolve()

# Rede e escuta HTTP
HOST = os.getenv("LECTORIA_HOST", "0.0.0.0")
# Porta padrão 8765 para evitar conflitos com o Jellyfin (que utiliza 8096/8920)
try:
    PORT = int(os.getenv("LECTORIA_PORT", os.getenv("PORT", "8765")))
except ValueError:
    PORT = 8765


# =========================================================================
# SEGURANÇA HTTP, COOKIES, PROXIES CONFIÁVEIS E CSP (Fase 11.3)
# =========================================================================
VALID_SECURE_COOKIES_MODES = ("auto", "true", "false")
VALID_CSP_MODES = ("off", "report-only", "enforce")
DEFAULT_FORWARDED_ALLOW_IPS = "127.0.0.1,::1"


def parse_secure_cookies_mode(raw: Optional[str]) -> str:
    """Valida LECTORIA_SECURE_COOKIES (auto | true | false)."""
    val = (raw if raw is not None else "auto").strip().lower()
    if not val:
        return "auto"
    if val not in VALID_SECURE_COOKIES_MODES:
        raise ValueError(
            f"LECTORIA_SECURE_COOKIES inválido: {raw!r}. "
            f"Valores permitidos: {', '.join(VALID_SECURE_COOKIES_MODES)}."
        )
    return val


def parse_csp_mode(raw: Optional[str]) -> str:
    """Valida LECTORIA_CSP_MODE (off | report-only | enforce)."""
    val = (raw if raw is not None else "report-only").strip().lower()
    if not val:
        return "report-only"
    if val not in VALID_CSP_MODES:
        raise ValueError(
            f"LECTORIA_CSP_MODE inválido: {raw!r}. "
            f"Valores permitidos: {', '.join(VALID_CSP_MODES)}."
        )
    return val


def parse_bool_env(name: str, raw: Optional[str], default: bool = False) -> bool:
    """Valida variáveis booleanas explícitas (true/false/1/0/yes/no)."""
    if raw is None or not raw.strip():
        return default
    val = raw.strip().lower()
    if val in ("true", "1", "yes", "on"):
        return True
    if val in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"{name} inválido: {raw!r}. Use 'true' ou 'false'.")


def parse_forwarded_allow_ips(raw: Optional[str]) -> list[str]:
    """
    Valida LECTORIA_FORWARDED_ALLOW_IPS: lista separada por vírgulas de IPs,
    CIDRs (ex.: 127.0.0.1,::1,172.16.0.0/12) ou '*' (não recomendado em produção).
    """
    text = (raw if raw is not None else DEFAULT_FORWARDED_ALLOW_IPS).strip()
    if not text:
        return ["127.0.0.1", "::1"]
    entries = [item.strip() for item in text.split(",") if item.strip()]
    if not entries:
        return ["127.0.0.1", "::1"]
    validated: list[str] = []
    for entry in entries:
        if entry == "*":
            validated.append("*")
            continue
        try:
            if "/" in entry:
                ipaddress.ip_network(entry, strict=False)
            else:
                ipaddress.ip_address(entry)
        except ValueError as exc:
            raise ValueError(
                f"LECTORIA_FORWARDED_ALLOW_IPS contém entrada inválida {entry!r}: "
                "informe IPs (ex.: 127.0.0.1, ::1), CIDRs (ex.: 172.16.0.0/12) ou '*'."
            ) from exc
        validated.append(entry)
    return validated


def is_trusted_proxy_ip(
    client_host: Optional[str],
    allow_list: Optional[list[str]] = None,
) -> bool:
    """Retorna True somente se client_host corresponder a um proxy explicitamente confiado."""
    if not client_host:
        return False
    allowed = allow_list if allow_list is not None else FORWARDED_ALLOW_IPS
    if "*" in allowed:
        return True
    host_str = client_host.strip()
    try:
        addr = ipaddress.ip_address(host_str)
    except ValueError:
        return False
    for entry in allowed:
        if "/" in entry:
            net = ipaddress.ip_network(entry, strict=False)
            if addr.version == net.version and addr in net:
                return True
        else:
            try:
                if addr == ipaddress.ip_address(entry):
                    return True
            except ValueError:
                continue
    return False


SECURE_COOKIES_MODE = parse_secure_cookies_mode(os.getenv("LECTORIA_SECURE_COOKIES"))
FORWARDED_ALLOW_IPS_RAW = os.getenv(
    "LECTORIA_FORWARDED_ALLOW_IPS",
    os.getenv("FORWARDED_ALLOW_IPS", DEFAULT_FORWARDED_ALLOW_IPS),
)
FORWARDED_ALLOW_IPS = parse_forwarded_allow_ips(FORWARDED_ALLOW_IPS_RAW)
CSP_MODE = parse_csp_mode(os.getenv("LECTORIA_CSP_MODE"))
HSTS_ENABLED = parse_bool_env("LECTORIA_HSTS_ENABLED", os.getenv("LECTORIA_HSTS_ENABLED"), default=False)


def ensure_runtime_directories() -> None:
    """Garante a existência dos diretórios locais essenciais para execução."""
    for directory in (LIBRARY_DIR, DATA_DIR, COVERS_DIR, BACKUPS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


ensure_runtime_directories()
