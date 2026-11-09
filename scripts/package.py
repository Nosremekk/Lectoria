#!/usr/bin/env python3
"""
Utilitário de Empacotamento do Lectoria para Distribuição e Transferência (Iteração 10).
Gera:
  1. Diretório do pacote limpo (Modalidade A): Lectoria-1.1.2-linux-x86_64/
  2. Arquivos compactados: Lectoria-1.1.2-linux-x86_64.tar.gz e .zip
  3. Pacote de migração opcional (Modalidade B): Lectoria-1.1.2-migration-bundle/
  4. Arquivos compactados de migração: Lectoria-1.1.2-migration-bundle.tar.gz e .zip
  5. SHA256SUMS e manifest.json para ambos os pacotes.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_BASE_DIR = PROJECT_ROOT / "dist"

# Importa versão canônica do sistema
sys.path.insert(0, str(PROJECT_ROOT))
try:
    import config
    PACKAGE_VERSION = getattr(config, "APP_VERSION", "1.5.2")
except Exception:
    PACKAGE_VERSION = "1.5.2"

TARGET_NAME = f"Lectoria-{PACKAGE_VERSION}-linux-x86_64"
MIGRATION_NAME = f"Lectoria-{PACKAGE_VERSION}-migration-bundle"


def get_git_info() -> dict:
    """Obtém hash do commit atual, branch e status do git."""
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"],
            text=True
        ).strip()
        branch = subprocess.check_output(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
            text=True
        ).strip()
    except Exception:
        commit = "unknown"
        branch = "main"
    return {"commit": commit, "branch": branch}


def compute_sha256(file_path: Path) -> str:
    """Calcula o hash SHA-256 de um arquivo."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def copy_app_tree(dest_app_dir: Path):
    """Copia os arquivos de código-fonte e recursos estáticos para app/, excluindo dados e venvs."""
    dest_app_dir.mkdir(parents=True, exist_ok=True)

    exclude_names = {
        ".git",
        ".venv",
        ".env",
        "__pycache__",
        ".pytest_cache",
        "data",
        "backups",
        "books",
        "dist",
        TARGET_NAME,
        MIGRATION_NAME,
    }

    def ignore_patterns(dir_path: str, names: list) -> set:
        ignored = set()
        for name in names:
            if name in exclude_names or name.endswith(".pyc") or name.endswith(".bak"):
                ignored.add(name)
        return ignored

    for item in PROJECT_ROOT.iterdir():
        if item.name in exclude_names or item.name.endswith(".pyc") or item.name.endswith(".bak"):
            continue
        dest_target = dest_app_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest_target, ignore=ignore_patterns, symlinks=True)
        elif item.is_file():
            shutil.copy2(item, dest_target)

    # Garante permissões de execução nos scripts do app
    for script_rel in [
        "deployment/install.sh",
        "deployment/configure.sh",
        "deployment/update.sh",
        "deployment/backup.sh",
        "deployment/restore.sh",
        "deployment/uninstall.sh",
        "deployment/diagnose.sh",
        "deployment/verify-package.sh",
        "scripts/diagnose.py",
        "scripts/backup.py",
        "scripts/manage_users.py",
    ]:
        sc = dest_app_dir / script_rel
        if sc.exists():
            sc.chmod(0o755)


def create_readme_files(pkg_root: Path):
    """Cria os arquivos README.txt nas pastas data/, books/ e backups/."""
    (pkg_root / "data").mkdir(parents=True, exist_ok=True)
    (pkg_root / "data" / "README.txt").write_text(
        "Lectoria — Diretório de Dados Persistentes\n"
        "==========================================\n\n"
        "Esta pasta armazena o banco de dados SQLite principal (lectoria.db) e a pasta de capas (covers/).\n\n"
        "• Modalidade A (Instalação Limpa): O banco será criado automaticamente na primeira execução de ./install.sh.\n"
        "• Modalidade B (Migração): O banco transferido de uma instalação anterior pode ser colocado aqui antes de executar ./install.sh\n"
        "  ou importado com: ./install.sh --with-migration /caminho/do/migration-bundle\n\n"
        "IMPORTANTE: Não apague os arquivos desta pasta para não perder o histórico de leitura, usuários e anotações.\n",
        encoding="utf-8",
    )

    (pkg_root / "books").mkdir(parents=True, exist_ok=True)
    (pkg_root / "books" / "README.txt").write_text(
        "Lectoria — Diretório da Biblioteca Padrão de Livros PDF\n"
        "=======================================================\n\n"
        "Coloque aqui os seus arquivos PDF para serem catalogados e lidos no Lectoria.\n"
        "Você também pode configurar diretórios externos adicionais através do menu 'Fontes' na interface web.\n\n"
        "O Lectoria NUNCA modifica, renomeia ou apaga nenhum arquivo PDF colocado nesta pasta.\n",
        encoding="utf-8",
    )

    (pkg_root / "backups").mkdir(parents=True, exist_ok=True)
    (pkg_root / "backups" / "README.txt").write_text(
        "Lectoria — Diretório de Backups\n"
        "===============================\n\n"
        "Esta pasta recebe as cópias de segurança consistentes geradas automaticamente antes de migrações\n"
        "ou manualmente através de:\n"
        "    ./app/deployment/backup.sh\n"
        "ou na interface web de administração.\n",
        encoding="utf-8",
    )


def create_root_scripts(pkg_root: Path):
    """Cria os atalhos de conveniência na raiz do pacote de distribuição."""
    # install.sh
    install_sh = pkg_root / "install.sh"
    install_sh.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        'exec bash "${SCRIPT_DIR}/app/deployment/install.sh" "$@"\n',
        encoding="utf-8",
    )
    install_sh.chmod(0o755)

    # configure.sh
    config_sh = pkg_root / "configure.sh"
    config_sh.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        'exec bash "${SCRIPT_DIR}/app/deployment/configure.sh" "$@"\n',
        encoding="utf-8",
    )
    config_sh.chmod(0o755)

    # diagnose.sh
    diag_sh = pkg_root / "diagnose.sh"
    diag_sh.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        'exec bash "${SCRIPT_DIR}/app/deployment/diagnose.sh" "$@"\n',
        encoding="utf-8",
    )
    diag_sh.chmod(0o755)

    # verify-package.sh
    verify_sh = pkg_root / "verify-package.sh"
    shutil.copy2(pkg_root / "app" / "deployment" / "verify-package.sh", verify_sh)
    verify_sh.chmod(0o755)


def create_documentation_files(pkg_root: Path, git_info: dict):
    """Gera VERSION.txt, INSTALL.txt e TRANSFER.txt com instruções completas."""
    (pkg_root / "VERSION.txt").write_text(
        f"Lectoria {PACKAGE_VERSION} (Linux x86_64)\n"
        f"Commit de Origem: {git_info['commit']}\n"
        f"Branch de Origem: {git_info['branch']}\n"
        f"Data de Empacotamento: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        f"Ambiente Alvo: Positivo C1260 (Intel Celeron 847 1.5 GHz, 8 GB RAM, SSD, Linux Mint XFCE)\n"
        f"Coexistência: Jellyfin Home Server (portas 8096/8920 preservadas)\n",
        encoding="utf-8",
    )

    (pkg_root / "INSTALL.txt").write_text(
        "===============================================================================\n"
        f" LECTORIA v{PACKAGE_VERSION} — GUIA RÁPIDO DE INSTALAÇÃO NO LINUX MINT XFCE\n"
        "===============================================================================\n\n"
        "1. PRÉ-REQUISITOS DO SISTEMA OPERACIONAL (EXECUTAR UMA VEZ):\n"
        "   Abra o terminal no Linux Mint e execute:\n"
        "       sudo apt update\n"
        "       sudo apt install -y python3 python3-venv python3-pip sqlite3 zenity git\n\n"
        "2. VERIFICAÇÃO DO PACOTE:\n"
        "   Antes de instalar, confira a integridade do pacote:\n"
        "       ./verify-package.sh\n\n"
        "3. INSTALAÇÃO (MODALIDADE A — INSTALAÇÃO LIMPA):\n"
        "   Execute com o seu usuário comum (NÃO utilize sudo):\n"
        "       ./install.sh\n\n"
        "   OU INSTALAÇÃO COM MIGRAÇÃO DE BIBLIOTECA (MODALIDADE B):\n"
        "   Se você possui o pacote de migração (banco e capas anteriores):\n"
        f"       ./install.sh --with-migration /caminho/do/{MIGRATION_NAME}\n\n"
        "4. ATIVAÇÃO DO SERVIÇO AUTOMÁTICO (SYSTEMD):\n"
        "   O script install.sh gera automaticamente o arquivo lectoria.service configurado.\n"
        "   Para ativá-lo no boot do Linux Mint:\n"
        "       sudo cp app/deployment/lectoria.service /etc/systemd/system/lectoria.service\n"
        "       sudo systemctl daemon-reload\n"
        "       sudo systemctl enable --now lectoria.service\n\n"
        "5. DIAGNÓSTICO E HOMOLOGAÇÃO:\n"
        "   Para verificar que tudo está 100% operacional:\n"
        "       ./diagnose.sh\n\n"
        "6. ACESSO PELA REDE DOMÉSTICA:\n"
        "   O Lectoria escuta na porta 8765.\n"
        "   Em qualquer outro computador, celular ou tablet da casa, acesse:\n"
        "       http://<IP_DO_POSITIVO>:8765\n"
        "   (O comando ./diagnose.sh exibe o IP exato da sua rede local).\n",
        encoding="utf-8",
    )

    (pkg_root / "TRANSFER.txt").write_text(
        "===============================================================================\n"
        f" LECTORIA v{PACKAGE_VERSION} — MANUAL COMPLETO DE TRANSFERÊNCIA PARA O POSITIVO C1260\n"
        "===============================================================================\n\n"
        "1. QUAL PASTA OU ARQUIVO DEVE SER COPIADO?\n"
        f"   Você pode copiar a pasta descompactada '{TARGET_NAME}/'\n"
        f"   OU o arquivo compactado '{TARGET_NAME}.tar.gz' (ou '{TARGET_NAME}.zip')\n"
        "   para um pendrive ou HD externo.\n\n"
        "2. É NECESSÁRIO EXTRAIR ALGUM ARQUIVO?\n"
        "   Se transferir o arquivo compactado .tar.gz, extraia no Positivo com:\n"
        f"       tar -xzf {TARGET_NAME}.tar.gz\n"
        f"       cd {TARGET_NAME}\n\n"
        "3. ONDE COLOCAR O PROJETO NO POSITIVO C1260?\n"
        "   Recomenda-se colocar na sua pasta pessoal de usuário, por exemplo:\n"
        "       ~/lectoria (ou /opt/lectoria)\n"
        "   (Qualquer pasta sob sua home é válida; o instalador se ajusta dinamicamente).\n\n"
        "4. COMO PRESERVAR A PASTA DE LIVROS PDF EXISTENTE?\n"
        "   • Opção 1: Se você já tem uma pasta com PDFs no Positivo (ex: ~/Livros),\n"
        "     você NÃO precisa movê-los. Após instalar, acesse a interface web do Lectoria,\n"
        "     abra o menu 'Fontes' e selecione sua pasta existente.\n"
        "   • Opção 2: Se preferir manter tudo dentro do pacote, basta copiar os PDFs para\n"
        "     a pasta 'books/' que acompanha o Lectoria.\n"
        "   Em qualquer caso, o Lectoria opera em modo somente-leitura sobre os PDFs e NUNCA\n"
        "   apaga ou altera arquivos originais.\n\n"
        "5. ESCOLHENDO ENTRE INSTALAÇÃO LIMPA E MIGRAÇÃO:\n"
        "   • INSTALAÇÃO LIMPA: Execute './install.sh'. Um banco novo e limpo será criado.\n"
        "     Ao abrir o navegador pela primeira vez, o assistente solicitará a criação\n"
        "     do seu usuário administrador mestre.\n"
        f"   • MIGRAÇÃO: Copie também a pasta '{MIGRATION_NAME}/' para o Positivo\n"
        "     e execute:\n"
        f"       ./install.sh --with-migration /caminho/para/{MIGRATION_NAME}\n"
        "     Seus usuários, anotações, destaques, desenhos, progresso e capas serão 100% restaurados,\n"
        "     e as sessões ativas serão invalidadas por segurança para um login novo.\n\n"
        "6. COMO VERIFICAR A INTEGRIDADE DO PACOTE ANTES DE QUALQUER AÇÃO?\n"
        "   Dentro da pasta extraída, execute:\n"
        "       ./verify-package.sh\n"
        "   O script validará todos os arquivos com SHA-256 e confirmará se está tudo aprovado.\n\n"
        "7. QUAL SERÁ O PRIMEIRO COMANDO EXECUTADO NO LINUX MINT?\n"
        "   Após extrair o pacote e instalar os pacotes básicos via apt:\n"
        "       ./install.sh\n\n"
        "8. COMO REALIZAR O PRIMEIRO DIAGNÓSTICO?\n"
        "   Execute:\n"
        "       ./diagnose.sh\n"
        "   Ele verifica permissões, integridade do SQLite WAL, porta de rede e uso de RAM.\n\n"
        "9. COMO INSTALAR E ATIVAR O SERVIÇO SYSTEMD NO BOOT?\n"
        "   Execute os comandos gerados no final do instalador:\n"
        "       sudo cp app/deployment/lectoria.service /etc/systemd/system/lectoria.service\n"
        "       sudo systemctl daemon-reload\n"
        "       sudo systemctl enable --now lectoria.service\n\n"
        "10. COMO ACESSAR PELA REDE LOCAL (LAN)?\n"
        "    Abra o navegador em outro computador ou celular na mesma rede e digite:\n"
        "       http://<IP_DO_POSITIVO>:8765\n"
        "    Se o firewall do Mint (ufw) estiver ativo, libere a porta:\n"
        "       sudo ufw allow 8765/tcp comment 'Lectoria PDF'\n\n"
        "11. COMO VERIFICAR SE O JELLYFIN CONTINUA FUNCIONANDO NORMALMENTE?\n"
        "    O Lectoria roda na porta 8765 e consome apenas ~55 MB de RAM em repouso com prioridade\n"
        "    controlada de CPU (MemoryMax=512M, CPUWeight=100).\n"
        "    Abra o Jellyfin em seu navegador habitual (porta 8096) e certifique-se de que a reprodução\n"
        "    continua rápida e estável.\n\n"
        "12. COMO REALIZAR BACKUP ANTES DE QUALQUER ATUALIZAÇÃO FUTURA?\n"
        "    Basta executar a qualquer momento:\n"
        "       ./app/deployment/backup.sh\n"
        "    Uma cópia pontual consistente será salva em backups/.\n",
        encoding="utf-8",
    )


def generate_manifest_and_sha256(target_dir: Path, is_migration: bool = False, git_info: dict = None) -> dict:
    """Gera manifest.json e SHA256SUMS para a pasta indicada."""
    if git_info is None:
        git_info = get_git_info()

    # Coleta todos os arquivos relativos
    files_list = []
    sha_lines = []

    for root, dirs, files in os.walk(target_dir):
        dirs.sort()
        files.sort()
        for f in files:
            if f in ("manifest.json", "SHA256SUMS"):
                continue
            fpath = Path(root) / f
            rel_path = fpath.relative_to(target_dir).as_posix()
            f_size = fpath.stat().st_size
            f_sha = compute_sha256(fpath)
            files_list.append({
                "path": rel_path,
                "size_bytes": f_size,
                "sha256": f_sha,
            })
            sha_lines.append(f"{f_sha}  {rel_path}\n")

    # Escreve SHA256SUMS
    sha_file = target_dir / "SHA256SUMS"
    sha_file.write_text("".join(sha_lines), encoding="utf-8")

    manifest = {
        "project": "Lectoria",
        "version": PACKAGE_VERSION,
        "type": "migration_bundle" if is_migration else "distribution_package",
        "modality": "Modalidade B (Migração da Biblioteca)" if is_migration else "Modalidade A (Instalação Limpa)",
        "source_git": {
            "commit": git_info["commit"],
            "branch": git_info["branch"],
        },
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "min_python_version": "3.10",
        "target_os": "Linux Mint XFCE (base Ubuntu 22.04 / 24.04 / Debian)",
        "target_hardware": "Positivo C1260 (Intel Celeron 847, 8 GB DDR3L, SSD)",
        "total_files": len(files_list) + 1,  # incluindo SHA256SUMS
        "files": files_list,
    }

    manifest_file = target_dir / "manifest.json"
    manifest_file.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    return manifest


def build_migration_bundle(mig_dir: Path) -> bool:
    """
    Constrói a Modalidade B:
    - Exporta data/lectoria.db usando SQLite Online Backup API.
    - Revoga/invalida sessões ativas (DELETE FROM sessions;) no banco exportado, sem tocar no original.
    - Copia capas personalizadas e extraídas.
    - Adiciona README e manifesto.
    """
    source_db_path = PROJECT_ROOT / "data" / "lectoria.db"
    if not source_db_path.exists():
        print("[!] Modalidade B ignorada: data/lectoria.db não encontrado.")
        return False

    mig_dir.mkdir(parents=True, exist_ok=True)
    target_db_path = mig_dir / "lectoria.db"

    print("[*] Modalidade B: Gerando cópia consistente do banco via SQLite Online Backup API...")
    source_conn = sqlite3.connect(str(source_db_path), timeout=30.0)
    target_conn = sqlite3.connect(str(target_db_path))
    source_conn.backup(target_conn)
    source_conn.close()

    # Higienização de segurança no banco migrado: revoga sessões ativas
    cur = target_conn.cursor()
    cur.execute("SELECT COUNT(*) FROM sessions;")
    active_sessions = cur.fetchone()[0]
    print(f"[*] Inativando {active_sessions} sessão(ões) ativa(s) no banco transferido por segurança...")
    cur.execute("DELETE FROM sessions;")
    target_conn.commit()

    # Validação de integridade do banco migrado
    cur.execute("PRAGMA integrity_check;")
    chk = cur.fetchone()
    assert chk and chk[0] == "ok", "Banco de migração falhou na verificação de integridade"

    # Coleta estatísticas dos dados preservados
    cur.execute("SELECT COUNT(*) FROM users;")
    user_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM books;")
    book_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM reading_progress;")
    prog_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM collections;")
    col_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM highlights;")
    hl_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM notes;")
    note_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM page_drawings;")
    draw_count = cur.fetchone()[0]
    target_conn.close()

    # Copia capas
    covers_source = PROJECT_ROOT / "data" / "covers"
    covers_dest = mig_dir / "covers"
    covers_dest.mkdir(parents=True, exist_ok=True)
    cover_count = 0
    if covers_source.exists():
        for f in covers_source.glob("*"):
            if f.is_file():
                shutil.copy2(f, covers_dest / f.name)
                cover_count += 1

    (mig_dir / "README.txt").write_text(
        "===============================================================================\n"
        f" LECTORIA v{PACKAGE_VERSION} — PACOTE DE MIGRAÇÃO DE BIBLIOTECA (MODALIDADE B)\n"
        "===============================================================================\n\n"
        "Este pacote contém a base de dados consistente do Lectoria e as capas preservadas.\n\n"
        "DADOS CONTIDOS NESTE PACOTE:\n"
        f"  • Usuários cadastrados:      {user_count}\n"
        f"  • Livros registrados:        {book_count}\n"
        f"  • Registros de progresso:    {prog_count}\n"
        f"  • Coleções personalizadas:   {col_count}\n"
        f"  • Destaques de texto:        {hl_count}\n"
        f"  • Notas de estudo:           {note_count}\n"
        f"  • Desenhos vetoriais:        {draw_count}\n"
        f"  • Capas preservadas:         {cover_count}\n"
        "  • Sessões ativas:            0 (revogadas por segurança para login novo)\n\n"
        "COMO USAR NO POSITIVO C1260:\n"
        "1. Copie esta pasta junto com o pacote principal do Lectoria.\n"
        "2. Ao instalar o Lectoria, execute:\n"
        f"       ./install.sh --with-migration {MIGRATION_NAME}\n"
        "   Ou copie diretamente o arquivo lectoria.db para a pasta data/ e as capas para data/covers/.\n",
        encoding="utf-8",
    )

    generate_manifest_and_sha256(mig_dir, is_migration=True)
    print(f"[OK] Modalidade B concluída: {mig_dir} ({book_count} livros, {user_count} usuários)")
    return True


def make_tar_gz(source_dir: Path, archive_path: Path):
    """Cria um tar.gz preservando permissões executáveis dos scripts."""
    print(f"[*] Gerando arquivo tar.gz: {archive_path}...")
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(source_dir, arcname=source_dir.name)


def make_zip(source_dir: Path, archive_path: Path):
    """Cria um zip preservando atributos de permissão UNIX."""
    print(f"[*] Gerando arquivo zip: {archive_path}...")
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(source_dir):
            for file in files:
                fpath = Path(root) / file
                rel_path = fpath.relative_to(source_dir.parent)
                zinfo = zipfile.ZipInfo.from_file(fpath, arcname=str(rel_path))
                # Preserva permissões unix no zip
                file_mode = fpath.stat().st_mode
                zinfo.external_attr = (file_mode & 0xFFFF) << 16
                with open(fpath, "rb") as fp:
                    zf.writestr(zinfo, fp.read())


def main():
    parser = argparse.ArgumentParser(
        description="Utilitário de Empacotamento do Lectoria para Distribuição e Transferência."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_BASE_DIR,
        help=f"Diretório base de saída dos pacotes (padrão: {DEFAULT_OUTPUT_BASE_DIR})"
    )
    parser.add_argument(
        "--skip-migration",
        action="store_true",
        help="Ignora a geração do pacote de migração (Modalidade B)"
    )
    args = parser.parse_args()

    output_base_dir = args.output_dir.resolve()
    output_base_dir.mkdir(parents=True, exist_ok=True)

    package_dir = output_base_dir / TARGET_NAME
    migration_dir = output_base_dir / MIGRATION_NAME

    print("============================================================")
    print(" Lectoria — Gerador de Pacote de Transferência")
    print("============================================================")

    git_info = get_git_info()
    print(f"[*] Versão alvo: {PACKAGE_VERSION}")
    print(f"[*] Commit base: {git_info['commit']} (branch {git_info['branch']})")
    print(f"[*] Diretório de saída: {output_base_dir}")

    # Limpeza prévia de diretórios temporários se existirem
    for d in (package_dir, migration_dir):
        if d.exists():
            shutil.rmtree(d)

    # 1. Constrói Modalidade A (Pacote de Distribuição Limpo)
    print(f"\n[1/4] Montando estrutura do pacote limpo em {package_dir}...")
    copy_app_tree(package_dir / "app")
    create_readme_files(package_dir)
    create_root_scripts(package_dir)
    create_documentation_files(package_dir, git_info)
    manifest_a = generate_manifest_and_sha256(package_dir, is_migration=False, git_info=git_info)
    print(f"[OK] Modalidade A pronta: {len(manifest_a['files'])} arquivos mapeados.")

    # 2. Constrói Modalidade B (Pacote de Migração da Biblioteca)
    has_migration = False
    if not args.skip_migration:
        print(f"\n[2/4] Montando pacote de migração em {migration_dir}...")
        has_migration = build_migration_bundle(migration_dir)
    else:
        print("\n[2/4] Modalidade B pulada por flag (--skip-migration).")

    # 3. Compactação dos pacotes (.tar.gz e .zip)
    print("\n[3/4] Compactando arquivos para distribuição...")
    archives = []
    tar_pkg = output_base_dir / f"{TARGET_NAME}.tar.gz"
    zip_pkg = output_base_dir / f"{TARGET_NAME}.zip"
    make_tar_gz(package_dir, tar_pkg)
    make_zip(package_dir, zip_pkg)
    archives.extend([tar_pkg, zip_pkg])

    if has_migration:
        tar_mig = output_base_dir / f"{MIGRATION_NAME}.tar.gz"
        zip_mig = output_base_dir / f"{MIGRATION_NAME}.zip"
        make_tar_gz(migration_dir, tar_mig)
        make_zip(migration_dir, zip_mig)
        archives.extend([tar_mig, zip_mig])

    # 4. Hashes dos arquivos compactados
    print("\n[4/4] Calculando somas de verificação dos arquivos compactados...")
    checksums = []
    for arch in archives:
        sha = compute_sha256(arch)
        size_mb = round(arch.stat().st_size / (1024 * 1024), 2)
        checksums.append(f"{sha}  {arch.name} ({size_mb} MB)")
        (arch.with_suffix(arch.suffix + ".sha256")).write_text(f"{sha}  {arch.name}\n", encoding="utf-8")

    print("\n" + "=" * 60)
    print(" EMPACOTAMENTO CONCLUÍDO COM SUCESSO!")
    print("=" * 60)
    for c in checksums:
        print(f" • {c}")
    print("=" * 60)


if __name__ == "__main__":
    main()
