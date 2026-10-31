"""
Configurações centrais do Lectoria.
Mantém parâmetros de porta, diretórios e nome do sistema centralizados e configuráveis.
"""

import os
from pathlib import Path

# Nome da aplicação
APP_NAME = os.getenv("LECTORIA_APP_NAME", "Lectoria")

# Diretório raiz do projeto
BASE_DIR = Path(__file__).resolve().parent

# Diretório onde os arquivos PDF ficam armazenados
# Pode ser configurado via variável de ambiente LECTORIA_LIBRARY_DIR
LIBRARY_DIR = Path(
    os.getenv("LECTORIA_LIBRARY_DIR", BASE_DIR / "books")
).resolve()

# Diretório onde o banco de dados e arquivos de aplicação ficam armazenados
DATA_DIR = Path(
    os.getenv("LECTORIA_DATA_DIR", BASE_DIR / "data")
).resolve()

# Caminho do banco SQLite
DB_PATH = DATA_DIR / "lectoria.db"

# Rede e escuta HTTP
HOST = os.getenv("LECTORIA_HOST", "0.0.0.0")
# Porta padrão 8765 para evitar conflitos com o Jellyfin (que usa 8096)
PORT = int(os.getenv("LECTORIA_PORT", "8765"))

# Garante a existência dos diretórios locais essenciais
LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

