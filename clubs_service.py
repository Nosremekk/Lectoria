"""
Módulo de Clubes de Leitura e Leitura Coletiva — Lectoria v1.6.0.
Gerencia:
- Clubes de leitura permanentes (`reading_clubs`) com níveis de visibilidade ('public', 'private')
- Membros e papéis hierárquicos ('owner', 'moderator', 'member') em `reading_club_members`
- Sistema de convites direcionados para clubes privados em `reading_club_invites`
- Leituras coletivas associadas a obras da biblioteca em `collective_reads`
- Participantes de leituras coletivas em `collective_read_members`
- Preservação estrita de privacidade individual (anotações e progresso nunca são expostos)
- Preservação de histórico de leituras concluídas/canceladas sem exclusão acidental
"""

import logging
import sqlite3
import uuid
from typing import Any, Dict, List, Optional, Tuple

from database import get_db

logger = logging.getLogger("lectoria.clubs")


# =========================================================================
# EXCEÇÕES DE NEGÓCIO
# =========================================================================

class ClubError(Exception):
    """Exceção base para operações de clubes de leitura."""
    pass


class ClubNotFoundError(ClubError):
    """Lançada quando um clube não existe ou não é acessível (evita enumeração)."""
    pass


class ClubPermissionError(ClubError):
    """Lançada quando o usuário não possui permissão para executar a ação no clube."""
    pass


class CollectiveReadNotFoundError(ClubError):
    """Lançada quando uma leitura coletiva não existe ou pertence a clube inacessível."""
    pass


class InviteNotFoundError(ClubError):
    """Lançada quando um convite de clube não é encontrado."""
    pass


class InvalidOperationError(ClubError):
    """Lançada quando uma operação viola regras de negócio (ex: proprietário sair sem transferir)."""
    pass


# =========================================================================
# GERENCIAMENTO DE CLUBES DE LEITURA
# =========================================================================

def create_club(
    name: str,
    description: str,
    owner_id: int,
    visibility: str = "public",
) -> Dict[str, Any]:
    """
    Cria um novo clube de leitura e associa o criador imediatamente como 'owner'.
    Valida nome e visibilidade ('public' ou 'private').
    """
    clean_name = (name or "").strip()
    if not clean_name:
        raise InvalidOperationError("O nome do clube de leitura é obrigatório.")
    if len(clean_name) > 120:
        raise InvalidOperationError("O nome do clube deve ter no máximo 120 caracteres.")

    clean_desc = (description or "").strip()
    vis = (visibility or "public").strip().lower()
    if vis not in ("public", "private"):
        vis = "public"

    club_id = uuid.uuid4().hex

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO reading_clubs (id, name, description, owner_id, visibility, is_archived)
            VALUES (?, ?, ?, ?, ?, 0);
            """,
            (club_id, clean_name, clean_desc, owner_id, vis),
        )
        cursor.execute(
            """
            INSERT INTO reading_club_members (club_id, user_id, role)
            VALUES (?, ?, 'owner');
            """,
            (club_id, owner_id),
        )

    return get_club(club_id, user_id=owner_id)


def get_club(club_id: str, user_id: int) -> Dict[str, Any]:
    """
    Obtém os detalhes completos de um clube de leitura.
    Para clubes privados, se o usuário não for membro e não tiver convite pendente,
    levanta ClubNotFoundError para impedir vazamento de existência (anti-enumeração).
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                c.id, c.name, c.description, c.owner_id, c.visibility,
                c.is_archived, c.created_at, c.updated_at,
                u.username AS owner_username,
                COALESCE(u.display_name, u.username) AS owner_display_name
            FROM reading_clubs c
            JOIN users u ON c.owner_id = u.id
            WHERE c.id = ?;
            """,
            (club_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise ClubNotFoundError("Clube de leitura não encontrado.")

        club = dict(row)

        # Checa vínculo do usuário solicitante
        cursor.execute(
            """
            SELECT role, joined_at
            FROM reading_club_members
            WHERE club_id = ? AND user_id = ?;
            """,
            (club_id, user_id),
        )
        member_row = cursor.fetchone()
        user_role = member_row["role"] if member_row else None
        is_member = member_row is not None

        # Checa se há convite pendente para este usuário caso não seja membro
        has_pending_invite = False
        if not is_member:
            cursor.execute(
                """
                SELECT id FROM reading_club_invites
                WHERE club_id = ? AND invitee_id = ? AND status = 'pending';
                """,
                (club_id, user_id),
            )
            has_pending_invite = cursor.fetchone() is not None

        # Se for privado e não for membro nem tiver convite, oculta a existência
        if club["visibility"] == "private" and not is_member and not has_pending_invite:
            raise ClubNotFoundError("Clube de leitura não encontrado.")

        # Contagem de membros
        cursor.execute(
            "SELECT COUNT(*) AS total FROM reading_club_members WHERE club_id = ?;",
            (club_id,),
        )
        club["member_count"] = cursor.fetchone()["total"]

        # Contagem de leituras ativas (planejadas ou em andamento)
        cursor.execute(
            """
            SELECT COUNT(*) AS total
            FROM collective_reads
            WHERE club_id = ? AND status IN ('planned', 'in_progress');
            """,
            (club_id,),
        )
        club["active_reads_count"] = cursor.fetchone()["total"]

        # Total histórico de leituras concluídas
        cursor.execute(
            """
            SELECT COUNT(*) AS total
            FROM collective_reads
            WHERE club_id = ? AND status = 'completed';
            """,
            (club_id,),
        )
        club["completed_reads_count"] = cursor.fetchone()["total"]

        club["user_role"] = user_role
        club["is_member"] = is_member
        club["has_pending_invite"] = has_pending_invite
        return club


def list_clubs(
    user_id: int,
    mine_only: bool = False,
    search: Optional[str] = None,
    include_archived: bool = False,
) -> List[Dict[str, Any]]:
    """
    Lista clubes de leitura acessíveis ao usuário.
    - Se mine_only=True: retorna apenas clubes onde o usuário é participante cadastrado.
    - Caso contrário: retorna clubes públicos da instância E clubes privados onde o usuário é membro.
    - Não expõe clubes privados de terceiros.
    """
    with get_db() as conn:
        cursor = conn.cursor()

        sql = """
            SELECT
                c.id, c.name, c.description, c.owner_id, c.visibility,
                c.is_archived, c.created_at, c.updated_at,
                u.username AS owner_username,
                COALESCE(u.display_name, u.username) AS owner_display_name,
                m.role AS user_role,
                (CASE WHEN m.user_id IS NOT NULL THEN 1 ELSE 0 END) AS is_member,
                (SELECT COUNT(*) FROM reading_club_members rcm WHERE rcm.club_id = c.id) AS member_count,
                (SELECT COUNT(*) FROM collective_reads cr WHERE cr.club_id = c.id AND cr.status IN ('planned', 'in_progress')) AS active_reads_count
            FROM reading_clubs c
            JOIN users u ON c.owner_id = u.id
            LEFT JOIN reading_club_members m ON c.id = m.club_id AND m.user_id = ?
            WHERE 1=1
        """
        params: List[Any] = [user_id]

        if not include_archived:
            sql += " AND c.is_archived = 0"

        if mine_only:
            sql += " AND m.user_id IS NOT NULL"
        else:
            # Mostra públicos ou privados dos quais o usuário já é membro
            sql += " AND (c.visibility = 'public' OR m.user_id IS NOT NULL)"

        if search and search.strip():
            st = f"%{search.strip()}%"
            sql += " AND (c.name LIKE ? OR c.description LIKE ?)"
            params.extend([st, st])

        sql += " ORDER BY c.is_archived ASC, is_member DESC, c.name COLLATE NOCASE ASC"

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        return [dict(r) for r in rows]


def update_club(
    club_id: str,
    user_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    visibility: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Atualiza nome, descrição ou visibilidade do clube.
    Apenas o proprietário (owner) ou administrador do sistema tem permissão.
    """
    club = get_club(club_id, user_id)
    if club["user_role"] != "owner":
        raise ClubPermissionError("Apenas o proprietário do clube pode editar suas configurações.")

    fields = []
    params = []

    if name is not None:
        clean_name = name.strip()
        if not clean_name:
            raise InvalidOperationError("O nome do clube não pode ser vazio.")
        if len(clean_name) > 120:
            raise InvalidOperationError("O nome do clube deve ter no máximo 120 caracteres.")
        fields.append("name = ?")
        params.append(clean_name)

    if description is not None:
        fields.append("description = ?")
        params.append(description.strip())

    if visibility is not None:
        vis = visibility.strip().lower()
        if vis not in ("public", "private"):
            vis = "public"
        fields.append("visibility = ?")
        params.append(vis)

    if not fields:
        return club

    fields.append("updated_at = CURRENT_TIMESTAMP")
    params.append(club_id)

    with get_db() as conn:
        conn.execute(
            f"UPDATE reading_clubs SET {', '.join(fields)} WHERE id = ?;",
            params,
        )

    return get_club(club_id, user_id)


def archive_club(
    club_id: str,
    user_id: int,
    archive: bool = True,
) -> Dict[str, Any]:
    """
    Arquiva ou desarquiva um clube de leitura.
    Preserva todo o histórico de leituras e membros sem exclusão física.
    """
    club = get_club(club_id, user_id)
    if club["user_role"] != "owner":
        raise ClubPermissionError("Apenas o proprietário pode arquivar ou desarquivar o clube.")

    new_val = 1 if archive else 0
    with get_db() as conn:
        conn.execute(
            "UPDATE reading_clubs SET is_archived = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
            (new_val, club_id),
        )

    return get_club(club_id, user_id)


def transfer_club_ownership(
    club_id: str,
    current_owner_id: int,
    new_owner_id: int,
) -> Dict[str, Any]:
    """
    Transfere a posse do clube para outro membro existente.
    O proprietário anterior é rebaixado para moderador.
    """
    if current_owner_id == new_owner_id:
        raise InvalidOperationError("Você já é o proprietário deste clube.")

    club = get_club(club_id, current_owner_id)
    if club["user_role"] != "owner":
        raise ClubPermissionError("Apenas o proprietário atual pode transferir a posse do clube.")

    with get_db() as conn:
        cursor = conn.cursor()
        # Verifica se o novo proprietário é membro
        cursor.execute(
            "SELECT role FROM reading_club_members WHERE club_id = ? AND user_id = ?;",
            (club_id, new_owner_id),
        )
        row = cursor.fetchone()
        if not row:
            raise InvalidOperationError("O novo proprietário deve ser membro ativo do clube.")

        # Atualiza o clube
        cursor.execute(
            "UPDATE reading_clubs SET owner_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
            (new_owner_id, club_id),
        )
        # Promove o novo owner
        cursor.execute(
            "UPDATE reading_club_members SET role = 'owner' WHERE club_id = ? AND user_id = ?;",
            (club_id, new_owner_id),
        )
        # Rebaixa o owner anterior para moderador
        cursor.execute(
            "UPDATE reading_club_members SET role = 'moderator' WHERE club_id = ? AND user_id = ?;",
            (club_id, current_owner_id),
        )

    return get_club(club_id, current_owner_id)


def join_club(club_id: str, user_id: int) -> Dict[str, Any]:
    """
    Permite que um usuário entre em um clube de leitura.
    - Para clubes públicos: entrada direta permitida.
    - Para clubes privados: exige que o usuário tenha um convite pendente.
    Idempotente caso o usuário já seja membro.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, visibility, is_archived FROM reading_clubs WHERE id = ?;",
            (club_id,),
        )
        club_row = cursor.fetchone()
        if not club_row:
            raise ClubNotFoundError("Clube de leitura não encontrado.")

        if club_row["is_archived"] == 1:
            raise InvalidOperationError("Não é possível ingressar em um clube arquivado.")

        # Verifica se já é membro
        cursor.execute(
            "SELECT role FROM reading_club_members WHERE club_id = ? AND user_id = ?;",
            (club_id, user_id),
        )
        if cursor.fetchone():
            return get_club(club_id, user_id)

        # Regras de clube privado
        if club_row["visibility"] == "private":
            cursor.execute(
                """
                SELECT id FROM reading_club_invites
                WHERE club_id = ? AND invitee_id = ? AND status = 'pending';
                """,
                (club_id, user_id),
            )
            invite_row = cursor.fetchone()
            if not invite_row:
                raise ClubPermissionError(
                    "Este clube é privado. É necessário receber um convite para ingressar."
                )

            # Marca o convite como aceito
            cursor.execute(
                """
                UPDATE reading_club_invites
                SET status = 'accepted', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """,
                (invite_row["id"],),
            )

        # Insere como membro comum
        cursor.execute(
            """
            INSERT INTO reading_club_members (club_id, user_id, role)
            VALUES (?, ?, 'member');
            """,
            (club_id, user_id),
        )

    return get_club(club_id, user_id)


def leave_club(club_id: str, user_id: int) -> Dict[str, Any]:
    """
    Permite a saída de um membro do clube.
    Se for o proprietário, deve transferir a titularidade caso haja outros membros,
    ou arquivar o clube caso seja o único membro restante.
    Também remove o usuário de participações ativas em leituras coletivas do clube.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT owner_id FROM reading_clubs WHERE id = ?;",
            (club_id,),
        )
        club_row = cursor.fetchone()
        if not club_row:
            raise ClubNotFoundError("Clube não encontrado.")

        cursor.execute(
            "SELECT role FROM reading_club_members WHERE club_id = ? AND user_id = ?;",
            (club_id, user_id),
        )
        mem = cursor.fetchone()
        if not mem:
            raise InvalidOperationError("Você não é participante deste clube.")

        if mem["role"] == "owner":
            cursor.execute(
                "SELECT COUNT(*) AS total FROM reading_club_members WHERE club_id = ?;",
                (club_id,),
            )
            total = cursor.fetchone()["total"]
            if total > 1:
                raise InvalidOperationError(
                    "O proprietário deve transferir a posse do clube para outro membro antes de sair."
                )
            else:
                # Único membro: arquiva o clube antes de sair
                cursor.execute(
                    "UPDATE reading_clubs SET is_archived = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                    (club_id,),
                )

        # Remove das leituras coletivas ativas do clube
        cursor.execute(
            """
            DELETE FROM collective_read_members
            WHERE user_id = ? AND read_id IN (
                SELECT id FROM collective_reads WHERE club_id = ?
            );
            """,
            (user_id, club_id),
        )

        # Remove da lista de membros do clube
        cursor.execute(
            "DELETE FROM reading_club_members WHERE club_id = ? AND user_id = ?;",
            (club_id, user_id),
        )

    return {"status": "left", "club_id": club_id}


def list_club_members(club_id: str, user_id: int) -> List[Dict[str, Any]]:
    """
    Lista todos os participantes de um clube com seus respectivos papéis.
    Verifica a permissão de visualização para clubes privados.
    """
    # Valida permissão de acesso ao clube
    get_club(club_id, user_id)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                m.user_id, m.role, m.joined_at,
                u.username,
                COALESCE(u.display_name, u.username) AS display_name,
                COALESCE(p.avatar_color, '#d4a853') AS avatar_color
            FROM reading_club_members m
            JOIN users u ON m.user_id = u.id
            LEFT JOIN user_profiles p ON m.user_id = p.user_id
            WHERE m.club_id = ?
            ORDER BY
                CASE m.role
                    WHEN 'owner' THEN 1
                    WHEN 'moderator' THEN 2
                    ELSE 3
                END,
                display_name COLLATE NOCASE ASC;
            """,
            (club_id,),
        )
        return [dict(r) for r in cursor.fetchall()]


def update_member_role(
    club_id: str,
    acting_user_id: int,
    target_user_id: int,
    new_role: str,
) -> Dict[str, Any]:
    """
    Permite ao proprietário promover ou rebaixar membros (ex: 'moderator' <-> 'member').
    Não permite alterar o papel do 'owner' (use transfer_club_ownership).
    """
    clean_role = (new_role or "").strip().lower()
    if clean_role not in ("moderator", "member"):
        raise InvalidOperationError("Papel inválido. Escolha 'moderator' ou 'member'.")

    club = get_club(club_id, acting_user_id)
    if club["user_role"] != "owner":
        raise ClubPermissionError("Apenas o proprietário pode alterar papéis de participantes.")

    if target_user_id == acting_user_id:
        raise InvalidOperationError("O proprietário não pode alterar seu próprio papel diretamente.")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT role FROM reading_club_members WHERE club_id = ? AND user_id = ?;",
            (club_id, target_user_id),
        )
        row = cursor.fetchone()
        if not row:
            raise InvalidOperationError("Participante não encontrado no clube.")

        cursor.execute(
            "UPDATE reading_club_members SET role = ? WHERE club_id = ? AND user_id = ?;",
            (clean_role, club_id, target_user_id),
        )

    return {"status": "updated", "user_id": target_user_id, "role": clean_role}


def remove_club_member(
    club_id: str,
    acting_user_id: int,
    target_user_id: int,
) -> Dict[str, Any]:
    """
    Remove um participante do clube de leitura.
    - O 'owner' pode remover qualquer moderador ou membro.
    - O 'moderator' pode remover membros comuns, mas não o proprietário nem outros moderadores.
    """
    club = get_club(club_id, acting_user_id)
    acting_role = club["user_role"]

    if acting_role not in ("owner", "moderator"):
        raise ClubPermissionError("Permissão negada. Apenas proprietários e moderadores podem remover membros.")

    if target_user_id == acting_user_id:
        raise InvalidOperationError("Para sair do clube, utilize a opção 'Sair do clube'.")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT role FROM reading_club_members WHERE club_id = ? AND user_id = ?;",
            (club_id, target_user_id),
        )
        target_row = cursor.fetchone()
        if not target_row:
            raise InvalidOperationError("Participante não encontrado no clube.")

        target_role = target_row["role"]
        if target_role == "owner":
            raise InvalidOperationError("Não é permitido remover o proprietário do clube.")

        if acting_role == "moderator" and target_role in ("owner", "moderator"):
            raise ClubPermissionError("Moderadores só podem remover membros comuns.")

        # Remove das leituras coletivas do clube
        cursor.execute(
            """
            DELETE FROM collective_read_members
            WHERE user_id = ? AND read_id IN (
                SELECT id FROM collective_reads WHERE club_id = ?
            );
            """,
            (target_user_id, club_id),
        )

        cursor.execute(
            "DELETE FROM reading_club_members WHERE club_id = ? AND user_id = ?;",
            (club_id, target_user_id),
        )

    return {"status": "removed", "user_id": target_user_id}


# =========================================================================
# CONVITES DIRECIONADOS PARA CLUBES PRIVADOS
# =========================================================================

def invite_user(
    club_id: str,
    inviter_id: int,
    invitee_id: int,
) -> Dict[str, Any]:
    """
    Cria um convite direcionado a um usuário da instância.
    Apenas proprietários e moderadores do clube podem enviar convites.
    """
    club = get_club(club_id, inviter_id)
    if club["user_role"] not in ("owner", "moderator"):
        raise ClubPermissionError("Apenas proprietários e moderadores podem convidar novos participantes.")

    if inviter_id == invitee_id:
        raise InvalidOperationError("Você não pode convidar a si mesmo.")

    with get_db() as conn:
        cursor = conn.cursor()
        # Valida se destinatário existe e está ativo
        cursor.execute("SELECT id, username FROM users WHERE id = ? AND is_active = 1;", (invitee_id,))
        invitee_row = cursor.fetchone()
        if not invitee_row:
            raise InvalidOperationError("Usuário convidado não encontrado ou inativo.")

        # Valida se destinatário já é membro
        cursor.execute(
            "SELECT role FROM reading_club_members WHERE club_id = ? AND user_id = ?;",
            (club_id, invitee_id),
        )
        if cursor.fetchone():
            raise InvalidOperationError("O usuário já é participante deste clube.")

        # Verifica se já há convite pendente
        cursor.execute(
            """
            SELECT id, status FROM reading_club_invites
            WHERE club_id = ? AND invitee_id = ? AND status = 'pending';
            """,
            (club_id, invitee_id),
        )
        existing = cursor.fetchone()
        if existing:
            return {"status": "already_invited", "invite_id": existing["id"]}

        invite_id = uuid.uuid4().hex
        cursor.execute(
            """
            INSERT INTO reading_club_invites (id, club_id, inviter_id, invitee_id, status)
            VALUES (?, ?, ?, ?, 'pending');
            """,
            (invite_id, club_id, inviter_id, invitee_id),
        )

    return {
        "status": "invited",
        "invite_id": invite_id,
        "club_id": club_id,
        "invitee_id": invitee_id,
    }


def respond_to_invite(
    invite_id: str,
    user_id: int,
    accept: bool,
) -> Dict[str, Any]:
    """
    Permite ao usuário convidado aceitar ou recusar o convite para um clube.
    Se aceito, insere o usuário em reading_club_members.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, club_id, inviter_id, invitee_id, status
            FROM reading_club_invites
            WHERE id = ?;
            """,
            (invite_id,),
        )
        invite = cursor.fetchone()
        if not invite:
            raise InviteNotFoundError("Convite não encontrado.")

        if invite["invitee_id"] != user_id:
            raise ClubPermissionError("Este convite foi destinado a outro usuário.")

        if invite["status"] != "pending":
            raise InvalidOperationError(f"Este convite já foi {invite['status']} anteriormente.")

        new_status = "accepted" if accept else "rejected"
        cursor.execute(
            "UPDATE reading_club_invites SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
            (new_status, invite_id),
        )

        if accept:
            # Insere em membros caso ainda não esteja
            cursor.execute(
                """
                INSERT OR IGNORE INTO reading_club_members (club_id, user_id, role)
                VALUES (?, ?, 'member');
                """,
                (invite["club_id"], user_id),
            )

    return {"status": new_status, "club_id": invite["club_id"]}


def cancel_invite(invite_id: str, acting_user_id: int) -> Dict[str, Any]:
    """
    Cancela um convite pendente.
    Apenas quem enviou o convite ou o proprietário/moderador do clube pode cancelar.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT i.id, i.club_id, i.inviter_id, i.status, m.role AS acting_role
            FROM reading_club_invites i
            LEFT JOIN reading_club_members m ON i.club_id = m.club_id AND m.user_id = ?
            WHERE i.id = ?;
            """,
            (acting_user_id, invite_id),
        )
        row = cursor.fetchone()
        if not row:
            raise InviteNotFoundError("Convite não encontrado.")

        if row["status"] != "pending":
            raise InvalidOperationError(f"O convite não está pendente (status atual: {row['status']}).")

        can_cancel = (
            row["inviter_id"] == acting_user_id
            or row["acting_role"] in ("owner", "moderator")
        )
        if not can_cancel:
            raise ClubPermissionError("Você não tem permissão para cancelar este convite.")

        cursor.execute(
            "UPDATE reading_club_invites SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
            (invite_id,),
        )

    return {"status": "cancelled", "invite_id": invite_id}


def list_user_invites(user_id: int, status: str = "pending") -> List[Dict[str, Any]]:
    """
    Lista convites recebidos pelo usuário autenticado.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                i.id, i.club_id, i.status, i.created_at,
                c.name AS club_name, c.description AS club_description, c.visibility,
                u.username AS inviter_username,
                COALESCE(u.display_name, u.username) AS inviter_display_name
            FROM reading_club_invites i
            JOIN reading_clubs c ON i.club_id = c.id
            JOIN users u ON i.inviter_id = u.id
            WHERE i.invitee_id = ? AND i.status = ?
            ORDER BY i.created_at DESC;
            """,
            (user_id, status),
        )
        return [dict(r) for r in cursor.fetchall()]


def list_club_invites(club_id: str, user_id: int) -> List[Dict[str, Any]]:
    """
    Lista convites pendentes de um clube específico.
    Acessível apenas por proprietários e moderadores.
    """
    club = get_club(club_id, user_id)
    if club["user_role"] not in ("owner", "moderator"):
        raise ClubPermissionError("Apenas proprietários e moderadores podem ver convites do clube.")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                i.id, i.invitee_id, i.status, i.created_at,
                u.username AS invitee_username,
                COALESCE(u.display_name, u.username) AS invitee_display_name,
                inv.username AS inviter_username
            FROM reading_club_invites i
            JOIN users u ON i.invitee_id = u.id
            JOIN users inv ON i.inviter_id = inv.id
            WHERE i.club_id = ? AND i.status = 'pending'
            ORDER BY i.created_at DESC;
            """,
            (club_id,),
        )
        return [dict(r) for r in cursor.fetchall()]


# =========================================================================
# LEITURAS COLETIVAS (COLLECTIVE READS)
# =========================================================================

def create_collective_read(
    club_id: str,
    user_id: int,
    book_id: str,
    title: Optional[str] = None,
    description: str = "",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: str = "planned",
) -> Dict[str, Any]:
    """
    Cria uma leitura coletiva vinculada a um clube e a um livro existente.
    Apenas proprietários e moderadores do clube podem iniciar leituras.
    O criador da leitura é automaticamente inscrito como primeiro participante.
    Preserva o ID do livro como string hexadecimal.
    """
    club = get_club(club_id, user_id)
    if club["is_archived"] == 1:
        raise InvalidOperationError("Não é possível criar leituras em um clube arquivado.")

    if club["user_role"] not in ("owner", "moderator"):
        raise ClubPermissionError("Apenas o proprietário e moderadores podem criar leituras coletivas.")

    clean_status = (status or "planned").strip().lower()
    if clean_status not in ("planned", "in_progress", "completed", "cancelled"):
        clean_status = "planned"

    clean_book_id = str(book_id).strip()

    with get_db() as conn:
        cursor = conn.cursor()
        # Valida existência do livro na biblioteca
        cursor.execute(
            "SELECT id, title, author FROM books WHERE id = ? AND is_missing = 0;",
            (clean_book_id,),
        )
        book_row = cursor.fetchone()
        if not book_row:
            raise InvalidOperationError("Livro não encontrado na biblioteca ou arquivo ausente.")

        read_id = uuid.uuid4().hex
        final_title = (title or "").strip() or book_row["title"]

        cursor.execute(
            """
            INSERT INTO collective_reads (
                id, club_id, book_id, title, description,
                start_date, end_date, status, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                read_id,
                club_id,
                clean_book_id,
                final_title,
                (description or "").strip(),
                start_date.strip() if start_date else None,
                end_date.strip() if end_date else None,
                clean_status,
                user_id,
            ),
        )

        # Insere criador como participante da leitura
        cursor.execute(
            """
            INSERT INTO collective_read_members (read_id, user_id)
            VALUES (?, ?);
            """,
            (read_id, user_id),
        )

    return get_collective_read(read_id, user_id)


def get_collective_read(read_id: str, user_id: int) -> Dict[str, Any]:
    """
    Obtém os detalhes da leitura coletiva, metadados do livro, dados do clube
    e lista de participantes.
    Garante que se o clube for privado, apenas membros tenham acesso aos detalhes.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                r.id, r.club_id, r.book_id, r.title, r.description,
                r.start_date, r.end_date, r.status, r.created_by,
                r.created_at, r.updated_at,
                c.name AS club_name, c.visibility AS club_visibility, c.is_archived AS club_is_archived,
                COALESCE(b.title, r.title) AS book_title,
                COALESCE(b.author, 'Autor não informado') AS book_author,
                b.cover_type, b.cover_path,
                b.cover_updated_at, b.page_count, b.format, b.filesize,
                COALESCE(b.is_missing, 1) AS book_is_missing,
                creator.username AS creator_username,
                COALESCE(creator.display_name, creator.username) AS creator_display_name
            FROM collective_reads r
            JOIN reading_clubs c ON r.club_id = c.id
            LEFT JOIN books b ON r.book_id = b.id
            JOIN users creator ON r.created_by = creator.id
            WHERE r.id = ?;
            """,
            (read_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise CollectiveReadNotFoundError("Leitura coletiva não encontrada.")

        read = dict(row)

        # Checa vínculo do usuário no clube organizador
        cursor.execute(
            "SELECT role FROM reading_club_members WHERE club_id = ? AND user_id = ?;",
            (read["club_id"], user_id),
        )
        mem = cursor.fetchone()
        user_club_role = mem["role"] if mem else None
        is_club_member = mem is not None

        # Se o clube for privado e o usuário não pertencer a ele, oculta a leitura
        if read["club_visibility"] == "private" and not is_club_member:
            raise CollectiveReadNotFoundError("Leitura coletiva não encontrada.")

        # Checa se o usuário participa desta leitura específica
        cursor.execute(
            "SELECT joined_at FROM collective_read_members WHERE read_id = ? AND user_id = ?;",
            (read_id, user_id),
        )
        user_part_row = cursor.fetchone()
        read["is_participating"] = user_part_row is not None
        read["user_joined_at"] = user_part_row["joined_at"] if user_part_row else None
        read["user_club_role"] = user_club_role
        read["is_club_member"] = is_club_member

        # Participantes da leitura coletiva
        cursor.execute(
            """
            SELECT
                m.user_id, m.joined_at,
                u.username,
                COALESCE(u.display_name, u.username) AS display_name,
                COALESCE(p.avatar_color, '#d4a853') AS avatar_color
            FROM collective_read_members m
            JOIN users u ON m.user_id = u.id
            LEFT JOIN user_profiles p ON m.user_id = p.user_id
            WHERE m.read_id = ?
            ORDER BY m.joined_at ASC;
            """,
            (read_id,),
        )
        read["members"] = [dict(r) for r in cursor.fetchall()]
        read["members_count"] = len(read["members"])

        # Monta cover_url
        ver = read.get("cover_updated_at") or ""
        book_id = read["book_id"]
        read["cover_url"] = f"/api/books/{book_id}/cover?v={ver}" if ver else f"/api/books/{book_id}/cover"

        return read


def list_club_reads(
    club_id: str,
    user_id: int,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Lista todas as leituras coletivas de um clube de leitura específico.
    """
    # Valida permissão de acesso ao clube
    get_club(club_id, user_id)

    with get_db() as conn:
        cursor = conn.cursor()
        sql = """
            SELECT
                r.id, r.club_id, r.book_id, r.title, r.description,
                r.start_date, r.end_date, r.status, r.created_by,
                r.created_at, r.updated_at,
                COALESCE(b.title, r.title) AS book_title,
                COALESCE(b.author, 'Autor não informado') AS book_author,
                b.cover_updated_at,
                b.page_count, b.format,
                COALESCE(b.is_missing, 1) AS book_is_missing,
                (SELECT COUNT(*) FROM collective_read_members crm WHERE crm.read_id = r.id) AS members_count,
                (CASE WHEN EXISTS (
                    SELECT 1 FROM collective_read_members crm WHERE crm.read_id = r.id AND crm.user_id = ?
                ) THEN 1 ELSE 0 END) AS is_participating
            FROM collective_reads r
            LEFT JOIN books b ON r.book_id = b.id
            WHERE r.club_id = ?
        """
        params: List[Any] = [user_id, club_id]

        if status and status.strip():
            sql += " AND r.status = ?"
            params.append(status.strip().lower())

        sql += """
            ORDER BY
                CASE r.status
                    WHEN 'in_progress' THEN 1
                    WHEN 'planned' THEN 2
                    WHEN 'completed' THEN 3
                    ELSE 4
                END,
                r.created_at DESC;
        """

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        reads = []
        for row in rows:
            rd = dict(row)
            ver = rd.get("cover_updated_at") or ""
            bid = rd["book_id"]
            rd["cover_url"] = f"/api/books/{bid}/cover?v={ver}" if ver else f"/api/books/{bid}/cover"
            reads.append(rd)
        return reads


def list_user_collective_reads(
    user_id: int,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Lista todas as leituras coletivas nas quais o usuário autenticado está inscrito como participante.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        sql = """
            SELECT
                r.id, r.club_id, r.book_id, r.title, r.description,
                r.start_date, r.end_date, r.status, r.created_by,
                r.created_at, r.updated_at,
                c.name AS club_name, c.visibility AS club_visibility,
                COALESCE(b.title, r.title) AS book_title,
                COALESCE(b.author, 'Autor não informado') AS book_author,
                b.cover_updated_at,
                b.page_count, b.format,
                COALESCE(b.is_missing, 1) AS book_is_missing,
                crm.joined_at,
                (SELECT COUNT(*) FROM collective_read_members crm2 WHERE crm2.read_id = r.id) AS members_count
            FROM collective_reads r
            JOIN collective_read_members crm ON r.id = crm.read_id AND crm.user_id = ?
            JOIN reading_clubs c ON r.club_id = c.id
            LEFT JOIN books b ON r.book_id = b.id
            WHERE 1=1
        """
        params: List[Any] = [user_id]

        if status and status.strip():
            sql += " AND r.status = ?"
            params.append(status.strip().lower())

        sql += """
            ORDER BY
                CASE r.status
                    WHEN 'in_progress' THEN 1
                    WHEN 'planned' THEN 2
                    WHEN 'completed' THEN 3
                    ELSE 4
                END,
                crm.joined_at DESC;
        """

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        reads = []
        for row in rows:
            rd = dict(row)
            ver = rd.get("cover_updated_at") or ""
            bid = rd["book_id"]
            rd["cover_url"] = f"/api/books/{bid}/cover?v={ver}" if ver else f"/api/books/{bid}/cover"
            rd["is_participating"] = True
            reads.append(rd)
        return reads


def update_collective_read(
    read_id: str,
    user_id: int,
    title: Optional[str] = None,
    description: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Atualiza dados de uma leitura coletiva (datas, descrição, status).
    Apenas proprietários, moderadores do clube ou o criador da leitura podem alterar.
    """
    read = get_collective_read(read_id, user_id)
    can_edit = (
        read["user_club_role"] in ("owner", "moderator")
        or read["created_by"] == user_id
    )
    if not can_edit:
        raise ClubPermissionError("Você não tem permissão para editar esta leitura coletiva.")

    fields = []
    params = []

    if title is not None:
        clean_title = title.strip()
        if clean_title:
            fields.append("title = ?")
            params.append(clean_title)

    if description is not None:
        fields.append("description = ?")
        params.append(description.strip())

    if start_date is not None:
        fields.append("start_date = ?")
        params.append(start_date.strip() if start_date else None)

    if end_date is not None:
        fields.append("end_date = ?")
        params.append(end_date.strip() if end_date else None)

    if status is not None:
        st = status.strip().lower()
        if st not in ("planned", "in_progress", "completed", "cancelled"):
            st = "planned"
        fields.append("status = ?")
        params.append(st)

    if not fields:
        return read

    fields.append("updated_at = CURRENT_TIMESTAMP")
    params.append(read_id)

    with get_db() as conn:
        conn.execute(
            f"UPDATE collective_reads SET {', '.join(fields)} WHERE id = ?;",
            params,
        )

    return get_collective_read(read_id, user_id)


def join_collective_read(read_id: str, user_id: int) -> Dict[str, Any]:
    """
    Permite a um membro do clube ingressar como participante na leitura coletiva.
    Regra obrigatória: o usuário DEVE ser membro do clube organizador.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT club_id, status FROM collective_reads WHERE id = ?;",
            (read_id,),
        )
        read_row = cursor.fetchone()
        if not read_row:
            raise CollectiveReadNotFoundError("Leitura coletiva não encontrada.")

        if read_row["status"] == "cancelled":
            raise InvalidOperationError("Não é possível ingressar em uma leitura cancelada.")

        club_id = read_row["club_id"]

        # Verifica se é membro do clube
        cursor.execute(
            "SELECT role FROM reading_club_members WHERE club_id = ? AND user_id = ?;",
            (club_id, user_id),
        )
        if not cursor.fetchone():
            raise ClubPermissionError(
                "Você precisa ser membro do clube organizador para participar desta leitura coletiva."
            )

        # Insere em collective_read_members de forma idempotente
        cursor.execute(
            """
            INSERT OR IGNORE INTO collective_read_members (read_id, user_id)
            VALUES (?, ?);
            """,
            (read_id, user_id),
        )

    return get_collective_read(read_id, user_id)


def leave_collective_read(read_id: str, user_id: int) -> Dict[str, Any]:
    """
    Permite a um participante sair de uma leitura coletiva.
    Preserva todo o progresso individual e o histórico de leituras concluídas.
    """
    with get_db() as conn:
        conn.execute(
            "DELETE FROM collective_read_members WHERE read_id = ? AND user_id = ?;",
            (read_id, user_id),
        )
    return {"status": "left", "read_id": read_id}


def get_book_collective_reads_for_user(book_id: str, user_id: int) -> List[Dict[str, Any]]:
    """
    Retorna as leituras coletivas associadas a um livro específico nas quais o
    usuário participa (ou que estejam em clubes que o usuário integra).
    Utilizado para enriquecer os cards de livro na biblioteca sem duplicar itens.
    """
    clean_book_id = str(book_id).strip()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                r.id AS read_id, r.club_id, r.title, r.status,
                r.start_date, r.end_date,
                c.name AS club_name,
                (SELECT COUNT(*) FROM collective_read_members crm WHERE crm.read_id = r.id) AS members_count,
                (CASE WHEN EXISTS (
                    SELECT 1 FROM collective_read_members crm WHERE crm.read_id = r.id AND crm.user_id = ?
                ) THEN 1 ELSE 0 END) AS is_participating
            FROM collective_reads r
            JOIN reading_clubs c ON r.club_id = c.id
            WHERE r.book_id = ?
              AND r.status != 'cancelled'
              AND (
                  c.visibility = 'public'
                  OR EXISTS (
                      SELECT 1 FROM reading_club_members m WHERE m.club_id = c.id AND m.user_id = ?
                  )
              )
            ORDER BY is_participating DESC, r.created_at DESC;
            """,
            (user_id, clean_book_id, user_id),
        )
        return [dict(r) for r in cursor.fetchall()]
