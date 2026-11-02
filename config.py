"""
Configurações centrais do Lectoria.
Mantém parâmetros de porta, diretórios e nome do sistema centralizados, portáveis e configuráveis
via variáveis de ambiente ou arquivo local .env (sem dependências externas).
"""

import os
from pathlib import Path
from typing import Optional

# Diretório raiz do projeto (resolvido dinamicamente a partir da localização deste arquivo)
BASE_DIR = Path(__file__).resolve().parent


def load_env_file(env_path: Optional[Path] = None, override: bool = False) -> dict[str, str]:
    """
    Carrega variáveis de configuração a partir de um arquivo .env simples (CHAVE=VALOR).
    Por padrão não sobrescreve variáveis já definidas no ambiente do processo (override=False).
    """
    target = env_path or Path(os.getenv("LECTORIA_ENV_FILE", BASE_DIR / ".env"))
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

# Diretório onde os arquivos PDF da biblioteca padrão ficam armazenados
LIBRARY_DIR = Path(
    os.getenv("LECTORIA_LIBRARY_DIR", str(BASE_DIR / "books"))
).expanduser().resolve()

# Diretório onde o banco de dados SQLite e dados persistentes ficam armazenados
DATA_DIR = Path(
    os.getenv("LECTORIA_DATA_DIR", str(BASE_DIR / "data"))
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
    os.getenv("LECTORIA_BACKUPS_DIR", str(BASE_DIR / "backups"))
).expanduser().resolve()

# Rede e escuta HTTP
HOST = os.getenv("LECTORIA_HOST", "0.0.0.0")
# Porta padrão 8765 para evitar conflitos com o Jellyfin (que utiliza 8096/8920)
try:
    PORT = int(os.getenv("LECTORIA_PORT", os.getenv("PORT", "8765")))
except ValueError:
    PORT = 8765


def ensure_runtime_directories() -> None:
    """Garante a existência dos diretórios locais essenciais para execução."""
    for directory in (LIBRARY_DIR, DATA_DIR, COVERS_DIR, BACKUPS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


ensure_runtime_directories()
