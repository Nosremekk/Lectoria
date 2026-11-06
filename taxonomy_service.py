"""
Módulo de Taxonomia e Classificação Bibliográfica do Lectoria (Fase 2).
Fornece:
1. Taxonomia hierárquica (Categorias e Subcategorias).
2. Separação multidimensional: Assunto/Disciplina, Gênero Literário, Origem/Nacionalidade Literária,
   Idioma do Documento e Período Histórico.
3. Mapeamento e normalização determinística de assuntos da Open Library, Google Books e CIP.
4. Preservação de classificações manuais atribuídas pelo usuário.
5. Agrupamento em árvore para navegação hierárquica e filtros combinados.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from database import get_db
from metadata_service import normalize_text_for_matching

logger = logging.getLogger("lectoria.taxonomy")

# ==============================================================================
# TAXONOMIA PADRÃO HIERÁRQUICA E MAPA DE PALAVRAS-CHAVE
# ==============================================================================

TAXONOMY_TREE: Dict[str, Dict[str, List[str]]] = {
    "Filosofia": {
        "Filosofia analítica": ["analytical philosophy", "filosofia analitica", "analytic philosophy", "russell", "frege", "quine"],
        "Filosofia continental": ["continental philosophy", "filosofia continental", "hegel", "nietzsche", "deleuze", "foucault"],
        "Fenomenologia": ["phenomenology", "fenomenologia", "husserl", "merleau-ponty", "heidegger"],
        "Existencialismo": ["existentialism", "existencialismo", "sartre", "camus", "kierkegaard", "beauvoir"],
        "Epistemologia": ["epistemology", "epistemologia", "teoria do conhecimento", "theory of knowledge"],
        "Metafísica": ["metaphysics", "metafisica", "ontologia", "ontology"],
        "Ética": ["ethics", "etica", "moral philosophy", "filosofia moral", "deontologia"],
        "Filosofia política": ["political philosophy", "filosofia politica", "teoria politica", "political theory", "maquiavel", "hobbes", "locke", "rousseau"],
        "Lógica": ["logic", "logica", "mathematical logic", "logica formal"],
        "Filosofia da linguagem": ["philosophy of language", "filosofia da linguagem", "linguagem e significado", "wittgenstein"],
        "Filosofia da mente": ["philosophy of mind", "filosofia da mente", "consciousness", "consciencia"],
    },
    "Literatura": {
        "Romance": ["novel", "romance", "fiction / literary", "literary fiction", "ficcao literaria"],
        "Conto": ["short stories", "contos", "short story", "novella"],
        "Poesia": ["poetry", "poesia", "poems", "poemas", "versos"],
        "Teatro": ["drama", "plays", "teatro", "dramaturgia", "tragedy", "comedy"],
        "Ensaio": ["essays", "ensaios", "literary essays", "critica literaria"],
        "Ficção científica": ["science fiction", "ficcao cientifica", "sci-fi", "cyberpunk", "space opera"],
        "Fantasia": ["fantasy", "fantasia", "fantasia epica", "high fantasy", "magic"],
        "Literatura brasileira": ["brazilian literature", "literatura brasileira", "autores brasileiros", "ficcao brasileira"],
        "Literatura latino-americana": ["latin american literature", "literatura latino-americana", "realismo magico"],
        "Literatura clássica": ["classic literature", "literatura classica", "classicos", "ancient literature", "greek literature", "roman literature"],
    },
    "Ciências humanas": {
        "História": ["history", "historia", "historia geral", "historiografia", "historical"],
        "Sociologia": ["sociology", "sociologia", "social sciences", "ciencias sociais", "marx", "weber", "durkheim"],
        "Antropologia": ["anthropology", "antropologia", "etnografia", "ethnography"],
        "Economia": ["economics", "economia", "politica economica", "macroeconomia", "microeconomia"],
        "Ciência política": ["political science", "ciencia politica", "relacoes internacionais", "governo", "democracia"],
        "Psicologia": ["psychology", "psicologia", "psicanalise", "psychoanalysis", "freud", "jung", "lacan"],
        "Educação": ["education", "educacao", "pedagogia", "pedagogy", "ensino"],
    },
    "Ciência e tecnologia": {
        "Computação": ["computer science", "ciencia da computacao", "computacao", "algorithms", "algoritmos"],
        "Programação": ["programming", "programacao", "software development", "desenvolvimento de software", "python", "javascript", "c++", "linux"],
        "Inteligência artificial": ["artificial intelligence", "inteligencia artificial", "machine learning", "deep learning", "redes neurais"],
        "Matemática": ["mathematics", "matematica", "calculo", "algebra", "geometria", "probabilidade"],
        "Física": ["physics", "fisica", "mecanica quantica", "relatividade", "termodinamica", "astrofisica"],
        "Biologia": ["biology", "biologia", "genetica", "evolucao", "evolution", "ecologia"],
        "Engenharia": ["engineering", "engenharia", "sistemas"],
    },
    "Artes e cultura": {
        "Cinema": ["cinema", "film", "filmes", "audiovisual", "cinematografia"],
        "Música": ["music", "musica", "teoria musical", "musicologia", "historia da musica"],
        "Artes visuais": ["visual arts", "artes visuais", "pintura", "escultura", "historia da arte", "art history"],
        "Design": ["design", "design grafico", "tipografia", "typography", "arquitetura", "architecture"],
        "Crítica cultural": ["cultural studies", "critica cultural", "estudos culturais", "teoria da cultura"],
    },
}

# Autores notáveis e sua origem literária para enriquecimento dimensional seguro
AUTHOR_ORIGINS: Dict[str, str] = {
    # Literatura Brasileira
    "machado de assis": "Literatura brasileira",
    "clarice lispector": "Literatura brasileira",
    "guimaraes rosa": "Literatura brasileira",
    "joao guimaraes rosa": "Literatura brasileira",
    "jorge amado": "Literatura brasileira",
    "graciliano ramos": "Literatura brasileira",
    "lima barreto": "Literatura brasileira",
    "jose de alencar": "Literatura brasileira",
    "monteiro lobato": "Literatura brasileira",
    "carlos drummond de andrade": "Literatura brasileira",
    "manuel bandeira": "Literatura brasileira",
    "rubem fonseca": "Literatura brasileira",
    "dalton trevisan": "Literatura brasileira",
    "raduan nassar": "Literatura brasileira",
    "ariano suassuna": "Literatura brasileira",
    "conceicao evaristo": "Literatura brasileira",
    # Literatura Latino-Americana
    "jorge luis borges": "Literatura latino-americana",
    "gabriel garcia marquez": "Literatura latino-americana",
    "julio cortazar": "Literatura latino-americana",
    "mario vargas llosa": "Literatura latino-americana",
    "pablo neruda": "Literatura latino-americana",
    "octavio paz": "Literatura latino-americana",
    "juan rulfo": "Literatura latino-americana",
    "roberto bolano": "Literatura latino-americana",
    # Literatura Clássica
    "homero": "Literatura clássica",
    "platao": "Filosofia clássica",
    "aristoteles": "Filosofia clássica",
    "virgilio": "Literatura clássica",
    "ovidio": "Literatura clássica",
    "dante alighieri": "Literatura clássica",
    "william shakespeare": "Teatro clássico",
    "miguel de cervantes": "Literatura clássica",
    # Filosofia Notável
    "immanuel kant": "Filosofia moderna",
    "friedrich nietzsche": "Filosofia continental",
    "martin heidegger": "Fenomenologia",
    "jean-paul sartre": "Existencialismo",
    "ludwig wittgenstein": "Filosofia da linguagem",
    "michel foucault": "Filosofia continental",
    "gilles deleuze": "Filosofia continental",
    "karl marx": "Ciências humanas",
    "max weber": "Ciências humanas",
}

GENRE_KEYWORDS: Dict[str, str] = {
    "romance": "Romance",
    "novel": "Romance",
    "novela": "Romance",
    "conto": "Conto",
    "contos": "Conto",
    "short stories": "Conto",
    "poesia": "Poesia",
    "poetry": "Poesia",
    "poema": "Poesia",
    "poemas": "Poesia",
    "teatro": "Teatro",
    "drama": "Teatro",
    "plays": "Teatro",
    "ensaio": "Ensaio",
    "ensaios": "Ensaio",
    "essays": "Ensaio",
    "ficcao cientifica": "Ficção científica",
    "science fiction": "Ficção científica",
    "sci-fi": "Ficção científica",
    "fantasia": "Fantasia",
    "fantasy": "Fantasia",
    "biografia": "Biografia",
    "biography": "Biografia",
    "autobiografia": "Autobiografia",
    "autobiography": "Autobiografia",
    "memorias": "Memórias",
    "memoirs": "Memórias",
}

PERIOD_KEYWORDS: Dict[str, str] = {
    "antiguidade": "Antiguidade",
    "antiga": "Antiguidade",
    "classica": "Antiguidade",
    "grecia": "Antiguidade",
    "roma": "Antiguidade",
    "medieval": "Idade Média",
    "idade media": "Idade Média",
    "renascenca": "Renascimento",
    "renascimento": "Renascimento",
    "seculo xvi": "Renascimento",
    "seculo xvii": "Idade Moderna",
    "seculo xviii": "Iluminismo",
    "iluminismo": "Iluminismo",
    "seculo xix": "Século XIX",
    "oitocentos": "Século XIX",
    "seculo xx": "Século XX",
    "novecentos": "Século XX",
    "modernismo": "Século XX",
    "contemporanea": "Contemporânea",
    "seculo xxi": "Contemporânea",
}


# ==============================================================================
# CLASSIFICAÇÃO DETERMINÍSTICA E MAPEAMENTO
# ==============================================================================

def map_raw_subjects_to_taxonomy(
    title: Optional[str],
    author: Optional[str],
    raw_subjects: Optional[List[str]],
    language: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Analisa os dados do livro e retorna a classificação bibliográfica estruturada:
    - categories: lista de categorias principais identificadas (ex: ['Filosofia', 'Literatura'])
    - subcategories: lista de pares (categoria, subcategoria)
    - genre: gênero literário (se aplicável)
    - origin: origem literária ou nacionalidade autoral (ex: 'Literatura brasileira')
    - period: período histórico
    - tags: lista limpa de tags normalizadas
    """
    detected_categories: Set[str] = set()
    detected_subcategories: List[Tuple[str, str]] = []
    detected_genre: Optional[str] = None
    detected_origin: Optional[str] = None
    detected_period: Optional[str] = None
    normalized_tags: Set[str] = set()

    # Prepara texto agregado normalizado para busca de padrões
    combined_tokens: List[str] = []
    if title:
        combined_tokens.append(normalize_text_for_matching(title))
    if author:
        norm_author = normalize_text_for_matching(author)
        combined_tokens.append(norm_author)
        # Consulta autor na tabela de origens literárias consolidadas
        for known_author, known_origin in AUTHOR_ORIGINS.items():
            if known_author in norm_author:
                detected_origin = known_origin
                if "literatura brasileira" in known_origin.lower():
                    detected_categories.add("Literatura")
                    detected_subcategories.append(("Literatura", "Literatura brasileira"))
                elif "literatura latino-americana" in known_origin.lower():
                    detected_categories.add("Literatura")
                    detected_subcategories.append(("Literatura", "Literatura latino-americana"))
                elif "literatura clássica" in known_origin.lower():
                    detected_categories.add("Literatura")
                    detected_subcategories.append(("Literatura", "Literatura clássica"))
                elif "filosofia" in known_origin.lower():
                    detected_categories.add("Filosofia")

    if raw_subjects:
        for subj in raw_subjects:
            if subj and isinstance(subj, str):
                cleaned_subj = subj.strip()
                if cleaned_subj:
                    normalized_tags.add(cleaned_subj)
                    combined_tokens.append(normalize_text_for_matching(cleaned_subj))

    full_text = " ".join(combined_tokens)

    # 1. Mapeamento com a Taxonomia Hierárquica
    for cat_name, subcats in TAXONOMY_TREE.items():
        cat_matched = False
        for subcat_name, keywords in subcats.items():
            for kw in keywords:
                # Verifica palavra ou expressão com fronteira de palavra
                pattern = r"\b" + re.escape(kw) + r"\b"
                if re.search(pattern, full_text):
                    detected_categories.add(cat_name)
                    detected_subcategories.append((cat_name, subcat_name))
                    cat_matched = True
                    break
        if not cat_matched:
            # Testa nome da própria categoria principal
            if re.search(r"\b" + re.escape(normalize_text_for_matching(cat_name)) + r"\b", full_text):
                detected_categories.add(cat_name)

    # 2. Detecção de Gênero Literário
    for kw, g_name in GENRE_KEYWORDS.items():
        if re.search(r"\b" + re.escape(kw) + r"\b", full_text):
            detected_genre = g_name
            detected_categories.add("Literatura")
            break

    # 3. Detecção de Período Histórico
    for kw, p_name in PERIOD_KEYWORDS.items():
        if re.search(r"\b" + re.escape(kw) + r"\b", full_text):
            detected_period = p_name
            break

    # Se nenhuma categoria foi identificada e for detectado apenas gênero narrativo
    if not detected_categories and detected_genre:
        detected_categories.add("Literatura")

    return {
        "categories": sorted(list(detected_categories)),
        "subcategories": detected_subcategories,
        "genre": detected_genre,
        "origin": detected_origin,
        "period": detected_period,
        "tags": sorted(list(normalized_tags)),
    }


# ==============================================================================
# PERSISTÊNCIA NO BANCO DE DADOS
# ==============================================================================

def sync_book_taxonomy(
    book_id: str,
    taxonomy: Dict[str, Any],
    is_manual: bool = False,
    conn: Optional[Any] = None,
) -> None:
    """
    Sincroniza a taxonomia no banco SQLite (tabelas subjects e book_subjects).
    Preserva registros manuais existentes atribuídos previamente pelo usuário (is_manual = 1).
    Permite receber uma conexão ativa existente para evitar deadlocks de transação SQLite.
    """
    categories = taxonomy.get("categories", [])
    subcategories = taxonomy.get("subcategories", [])
    genre = taxonomy.get("genre")
    origin = taxonomy.get("origin")
    period = taxonomy.get("period")
    tags = taxonomy.get("tags", [])

    def _execute_sync(cursor):
        # Se não for manual, não sobrescreve os vínculos manuais já existentes
        cursor.execute(
            "SELECT subject_id FROM book_subjects WHERE book_id = ? AND is_manual = 1;",
            (book_id,),
        )
        manual_subject_ids = {row["subject_id"] for row in cursor.fetchall()}

        # Remove apenas os vínculos automáticos anteriores
        cursor.execute(
            "DELETE FROM book_subjects WHERE book_id = ? AND is_manual = 0;",
            (book_id,),
        )

        subjects_to_link: Set[Tuple[str, str, Optional[str]]] = set()

        # Adiciona categorias principais e subcategorias
        if subcategories:
            for cat, subcat in subcategories:
                subjects_to_link.add((f"{cat}: {subcat}", cat, subcat))
        elif categories:
            for cat in categories:
                subjects_to_link.add((cat, cat, None))

        # Adiciona tags gerais como subitens
        primary_cat = categories[0] if categories else "Geral"
        for t in tags[:8]:  # Limite de 8 tags principais
            t_clean = t.strip()
            if t_clean and not any(t_clean.lower() in item[0].lower() for item in subjects_to_link):
                subjects_to_link.add((t_clean, primary_cat, None))

        for name, cat, subcat in subjects_to_link:
            cursor.execute(
                """
                INSERT INTO subjects (name, category, subcategory, genre, origin, period)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    category = excluded.category,
                    subcategory = COALESCE(excluded.subcategory, subjects.subcategory),
                    genre = COALESCE(excluded.genre, subjects.genre),
                    origin = COALESCE(excluded.origin, subjects.origin),
                    period = COALESCE(excluded.period, subjects.period);
                """,
                (name, cat, subcat, genre, origin, period),
            )
            cursor.execute("SELECT id FROM subjects WHERE name = ? COLLATE NOCASE;", (name,))
            subj_id = cursor.fetchone()["id"]

            manual_flag = 1 if is_manual else (1 if subj_id in manual_subject_ids else 0)
            cursor.execute(
                """
                INSERT OR IGNORE INTO book_subjects (book_id, subject_id, is_manual)
                VALUES (?, ?, ?);
                """,
                (book_id, subj_id, manual_flag),
            )

    if conn is not None:
        _execute_sync(conn.cursor())
    else:
        with get_db() as local_conn:
            _execute_sync(local_conn.cursor())


def get_book_taxonomy(book_id: str) -> Dict[str, Any]:
    """Retorna todas as informações taxonômicas associadas a um livro."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.id, s.name, s.category, s.subcategory, s.genre, s.origin, s.period, bs.is_manual
            FROM subjects s
            JOIN book_subjects bs ON s.id = bs.subject_id
            WHERE bs.book_id = ?
            ORDER BY s.category ASC, s.name ASC;
            """,
            (book_id,),
        )
        rows = [dict(r) for r in cursor.fetchall()]

    categories = sorted(list({r["category"] for r in rows if r["category"]}))
    subcategories = [r["name"] for r in rows if ":" in r["name"]]
    genres = [r["genre"] for r in rows if r["genre"]]
    origins = [r["origin"] for r in rows if r["origin"]]
    periods = [r["period"] for r in rows if r["period"]]

    return {
        "categories": categories,
        "subcategories": subcategories,
        "genre": genres[0] if genres else None,
        "origin": origins[0] if origins else None,
        "period": periods[0] if periods else None,
        "subjects": rows,
    }


def get_taxonomy_tree() -> List[Dict[str, Any]]:
    """
    Retorna a árvore taxonômica presente no acervo atual (apenas categorias e subcategorias
    que possuem livros associados, evitando categorias vazias na interface).
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.category, s.subcategory, COUNT(DISTINCT bs.book_id) as book_count
            FROM subjects s
            JOIN book_subjects bs ON s.id = bs.subject_id
            JOIN books b ON bs.book_id = b.id
            WHERE b.is_missing = 0
            GROUP BY s.category, s.subcategory
            HAVING book_count > 0
            ORDER BY s.category ASC, book_count DESC;
            """
        )
        rows = [dict(r) for r in cursor.fetchall()]

    # Agrupa por categoria principal
    tree_map: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        cat = r["category"]
        subcat = r["subcategory"]
        count = r["book_count"]

        if cat not in tree_map:
            tree_map[cat] = {
                "category": cat,
                "total_books": 0,
                "subcategories": [],
            }
        tree_map[cat]["total_books"] += count
        if subcat:
            tree_map[cat]["subcategories"].append({
                "name": subcat,
                "book_count": count,
            })

    return sorted(list(tree_map.values()), key=lambda x: x["total_books"], reverse=True)
