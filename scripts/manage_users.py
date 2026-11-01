#!/usr/bin/env python3
"""
Utilitário de linha de comando para administração de usuários e perfis do Lectoria.
Uso:
    python scripts/manage_users.py --list
    python scripts/manage_users.py --create-admin
    python scripts/manage_users.py --create-user <username>
    python scripts/manage_users.py --reset-password <username>
    python scripts/manage_users.py --set-role <username> <admin|reader>
    python scripts/manage_users.py --deactivate <username>
    python scripts/manage_users.py --activate <username>
"""

import argparse
import getpass
from pathlib import Path
import sys

# Adiciona a raiz do projeto ao path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import database
from auth_service import (
    create_user,
    list_users,
    update_user,
    get_user_by_id,
    AuthError,
    UserAlreadyExistsError,
    UserNotFoundError,
)


def _find_user_by_name(username: str):
    users = list_users()
    for u in users:
        if u["username"].lower() == username.lower().strip():
            return u
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Gerenciador seguro de usuários e credenciais do Lectoria."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--list",
        action="store_true",
        help="Lista todos os usuários cadastrados",
    )
    group.add_argument(
        "--create-admin",
        action="store_true",
        help="Cadastra um novo usuário com privilégios de administrador",
    )
    group.add_argument(
        "--create-user",
        type=str,
        metavar="USERNAME",
        help="Cadastra um novo leitor comum",
    )
    group.add_argument(
        "--reset-password",
        type=str,
        metavar="USERNAME",
        help="Redefine a senha de um usuário existente",
    )
    group.add_argument(
        "--set-role",
        nargs=2,
        metavar=("USERNAME", "ROLE"),
        help="Altera o papel de um usuário (ex: fulano admin)",
    )
    group.add_argument(
        "--activate",
        type=str,
        metavar="USERNAME",
        help="Ativa a conta do usuário informado",
    )
    group.add_argument(
        "--deactivate",
        type=str,
        metavar="USERNAME",
        help="Desativa a conta do usuário informado",
    )

    # Parâmetros opcionais para automação/scripts
    parser.add_argument(
        "--username",
        type=str,
        help="Nome de usuário para criação de admin (quando utilizado com --create-admin)",
    )
    parser.add_argument(
        "--password",
        type=str,
        help="Senha (opcional em modo não-interativo; se omitido, solicita via prompt seguro)",
    )
    parser.add_argument(
        "--display-name",
        type=str,
        help="Nome amigável de exibição do usuário",
    )

    args = parser.parse_args()

    # Garante que as migrações estão aplicadas
    database.init_db()

    if args.list:
        users = list_users()
        print(f"\n[*] Usuários cadastrados no Lectoria ({len(users)}):")
        print("-" * 75)
        print(f" {'ID':<4} | {'Usuário':<18} | {'Nome de Exibição':<20} | {'Papel':<8} | {'Ativo':<5}")
        print("-" * 75)
        for u in users:
            status_str = "Sim" if u["is_active"] else "Não"
            print(f" {u['id']:<4} | {u['username']:<18} | {u['display_name']:<20} | {u['role']:<8} | {status_str:<5}")
        print("-" * 75 + "\n")

    elif args.create_admin:
        username = args.username
        if not username:
            username = input("Informe o nome de usuário (username) para o Administrador: ").strip()

        password = args.password
        if not password:
            password = getpass.getpass("Informe a senha: ")
            confirm = getpass.getpass("Confirme a senha: ")
            if password != confirm:
                print("[ERRO] As senhas digitadas não conferem.", file=sys.stderr)
                sys.exit(1)

        disp = args.display_name or username.capitalize()
        try:
            created = create_user(username=username, password=password, display_name=disp, role="admin")
            print(f"\n[OK] Administrador '{created['username']}' criado com sucesso!")
            print(f"     ID: {created['id']}")
            print(f"     Papel: {created['role']}\n")
        except (ValueError, UserAlreadyExistsError) as e:
            print(f"[ERRO] {e}", file=sys.stderr)
            sys.exit(1)

    elif args.create_user:
        username = args.create_user.strip()
        password = args.password
        if not password:
            password = getpass.getpass(f"Informe a senha para o leitor '{username}': ")
            confirm = getpass.getpass("Confirme a senha: ")
            if password != confirm:
                print("[ERRO] As senhas digitadas não conferem.", file=sys.stderr)
                sys.exit(1)

        disp = args.display_name or username.capitalize()
        try:
            created = create_user(username=username, password=password, display_name=disp, role="reader")
            print(f"\n[OK] Usuário leitor '{created['username']}' criado com sucesso!")
            print(f"     ID: {created['id']}")
            print(f"     Papel: {created['role']}\n")
        except (ValueError, UserAlreadyExistsError) as e:
            print(f"[ERRO] {e}", file=sys.stderr)
            sys.exit(1)

    elif args.reset_password:
        username = args.reset_password.strip()
        user = _find_user_by_name(username)
        if not user:
            print(f"[ERRO] Usuário '{username}' não encontrado.", file=sys.stderr)
            sys.exit(1)

        password = args.password
        if not password:
            password = getpass.getpass(f"Informe a nova senha para '{user['username']}': ")
            confirm = getpass.getpass("Confirme a nova senha: ")
            if password != confirm:
                print("[ERRO] As senhas digitadas não conferem.", file=sys.stderr)
                sys.exit(1)

        try:
            update_user(user["id"], new_password=password)
            print(f"[OK] Senha de '{user['username']}' redefinida com sucesso.")
        except Exception as e:
            print(f"[ERRO] {e}", file=sys.stderr)
            sys.exit(1)

    elif args.set_role:
        username, new_role = args.set_role[0].strip(), args.set_role[1].strip().lower()
        if new_role not in ("admin", "reader"):
            print("[ERRO] Papel inválido. Deve ser 'admin' ou 'reader'.", file=sys.stderr)
            sys.exit(1)

        user = _find_user_by_name(username)
        if not user:
            print(f"[ERRO] Usuário '{username}' não encontrado.", file=sys.stderr)
            sys.exit(1)

        try:
            updated = update_user(user["id"], role=new_role)
            print(f"[OK] Papel de '{updated['username']}' alterado para '{updated['role']}'.")
        except AuthError as e:
            print(f"[ERRO] {e}", file=sys.stderr)
            sys.exit(1)

    elif args.activate:
        username = args.activate.strip()
        user = _find_user_by_name(username)
        if not user:
            print(f"[ERRO] Usuário '{username}' não encontrado.", file=sys.stderr)
            sys.exit(1)

        update_user(user["id"], is_active=True)
        print(f"[OK] Conta de '{user['username']}' ativada com sucesso.")

    elif args.deactivate:
        username = args.deactivate.strip()
        user = _find_user_by_name(username)
        if not user:
            print(f"[ERRO] Usuário '{username}' não encontrado.", file=sys.stderr)
            sys.exit(1)

        try:
            update_user(user["id"], is_active=False)
            print(f"[OK] Conta de '{user['username']}' desativada com sucesso.")
        except AuthError as e:
            print(f"[ERRO] {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
