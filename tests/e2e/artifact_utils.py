"""
Utilitário portável para resolução do diretório de artefatos E2E (screenshots e traces).

Evita qualquer dependência de caminhos absolutos específicos do ambiente local,
resolvendo por padrão para `<repo_root>/tests/artifacts/` (ignorado no `.gitignore`),
com override opcional via `LECTORIA_TEST_ARTIFACTS_DIR` e fallback seguro para
`tempfile.gettempdir()` caso o diretório padrão não tenha permissão de escrita.
"""

import os
from pathlib import Path
import tempfile

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ARTIFACTS_DIR = REPO_ROOT / "tests" / "artifacts"


def get_artifact_dir() -> Path:
    """Retorna um diretório gravável e portável para artefatos dos testes E2E."""
    raw = os.getenv("LECTORIA_TEST_ARTIFACTS_DIR", "").strip()
    target = Path(raw).expanduser() if raw else DEFAULT_ARTIFACTS_DIR
    try:
        target.mkdir(parents=True, exist_ok=True)
        return target
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "lectoria-test-artifacts"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
