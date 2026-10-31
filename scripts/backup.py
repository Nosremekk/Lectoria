#!/usr/bin/env python3
"""
Utilitário de linha de comando para Backup e Restauração do Lectoria.
Uso:
    python scripts/backup.py --backup
    python scripts/backup.py --list
    python scripts/backup.py --restore <nome_do_arquivo>
    python scripts/backup.py --verify
"""

import argparse
import sys
from pathlib import Path

# Adiciona raiz do projeto ao path para importar módulos da aplicação
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config
from backup_service import (
    create_backup,
    list_backups,
    restore_backup,
    verify_sqlite_integrity,
    BackupError,
)


def main():
    parser = argparse.ArgumentParser(
        description="Gerenciador manual de backup e restauração do Lectoria."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--backup",
        action="store_true",
        help="Cria um novo backup consistente do banco de dados",
    )
    group.add_argument(
        "--list",
        action="store_true",
        help="Lista todos os backups disponíveis",
    )
    group.add_argument(
        "--restore",
        type=str,
        metavar="ARQUIVO",
        help="Restaura a base de dados a partir do arquivo de backup indicado",
    )
    group.add_argument(
        "--verify",
        action="store_true",
        help="Verifica a integridade do banco de dados SQLite ativo",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignora a confirmação interativa na restauração",
    )

    args = parser.parse_args()

    if args.backup:
        print("[*] Criando backup do banco de dados SQLite...")
        try:
            res = create_backup()
            print(f"[OK] Backup gerado com sucesso!")
            print(f"     Arquivo: {res['filename']}")
            print(f"     Tamanho: {res['size_bytes']} bytes")
            print(f"     Localização: {res['path']}")
            print(f"     Integridade: {res['integrity']}")
        except BackupError as e:
            print(f"[ERRO] {e}", file=sys.stderr)
            sys.exit(1)

    elif args.list:
        backups = list_backups()
        if not backups:
            print("[INFO] Nenhum backup encontrado em backups/.")
            return

        print(f"[*] Backups disponíveis ({len(backups)}):")
        print("-" * 65)
        for b in backups:
            kb = round(b["size_bytes"] / 1024, 1)
            print(f" • {b['filename']:<38} | {kb:>6} KB | {b['created_at']}")
        print("-" * 65)

    elif args.verify:
        print(f"[*] Verificando integridade de: {config.DB_PATH}")
        if not config.DB_PATH.exists():
            print("[ERRO] Arquivo de banco de dados não encontrado.", file=sys.stderr)
            sys.exit(1)

        ok = verify_sqlite_integrity(config.DB_PATH)
        if ok:
            print("[OK] Banco de dados íntegro e sem erros de consistência (PRAGMA integrity_check: ok).")
        else:
            print("[ERRO] O banco de dados apresentou problemas de integridade!", file=sys.stderr)
            sys.exit(1)

    elif args.restore:
        target_file = args.restore
        print(f"[ATENÇÃO] Você solicitou restaurar o banco a partir de: {target_file}")
        print("          Isso substituirá o banco atual 'data/lectoria.db'.")

        if not args.force:
            confirm = input("Tem certeza de que deseja prosseguir com a restauração? (s/N): ").strip().lower()
            if confirm not in ("s", "sim", "y", "yes"):
                print("[CANCELADO] Operação de restauração abortada pelo usuário.")
                sys.exit(0)

        try:
            res = restore_backup(target_file)
            print(f"[OK] Restauração concluída com sucesso!")
            print(f"     Origem: {res['restored_from']}")
            print(f"     Destino: {res['target_path']}")
            print(f"     Integridade pós-restauração verificada: {res['verified_integrity']}")
        except BackupError as e:
            print(f"[ERRO na Restauração] {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
