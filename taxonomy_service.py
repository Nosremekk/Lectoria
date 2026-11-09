"""
Módulo de Taxonomia, Categorias Personalizáveis e Classificação Bibliográfica do Lectoria (v1.5.2).
Fornece:
1. Taxonomia hierárquica (Categorias Principais, Categorias Personalizadas e Subcategorias).
2. Separação multidimensional: Categoria da Biblioteca (`categories`/`book_categories`) vs
   Assuntos Bibliográficos Importados (`subjects`/`book_subjects`), Gênero Literário,
   Origem/Nacionalidade Literária, Idioma do Documento e Período Histórico.
3. Classificação automática ponderada e explicável considerando título, autor, assuntos,
   descrição e editora, evitando falsos positivos por palavras isoladas fora de contexto.
4. Preservação estrita de classificações manuais atribuídas pelo administrador/usuário.
5. Gerenciamento completo de categorias: criar, renomear, editar descrição, reordenar,
   mesclar, excluir com segurança e atribuir em lote.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from database import get_db
from metadata_service import normalize_text_for_matching

logger = logging.getLogger("lectoria.taxonomy")

# ==============================================================================
# TAXONOMIA PADRÃO HIERÁRQUICA E REGRAS PONDERADAS DE CLASSIFICAÇÃO
# ==============================================================================

DEFAULT_CATEGORY_ORDER: Dict[str, int] = {
    "Filosofia": 10,
    "Literatura": 20,
    "Ciências humanas": 30,
    "Ciência e tecnologia": 40,
    "Artes e cultura": 50,
}

TAXONOMY_TREE: Dict[str, Dict[str, List[str]]] = {
    "Filosofia": {
        "Filosofia antiga e clássica": [
            "filosofia antiga", "ancient philosophy", "filosofia classica", "pre-socraticos",
            "estoicismo", "stoicism", "epicurismo", "neoplatonismo", "as leis", "a republica",
            "etica a nicomaco", "banquete", "fedon", "apologia de socrates", "meditacoes",
            "constituicao de atenas", "organon", "metafisica de aristoteles",
        ],
        "Filosofia analítica": [
            "analytical philosophy", "filosofia analitica", "analytic philosophy",
            "filosofia da ciencia", "philosophy of science", "positivismo logico",
        ],
        "Filosofia continental": [
            "continental philosophy", "filosofia continental", "escola de frankfurt",
            "teoria critica", "critical theory", "sociedade do cansaco", "sociedade da transparencia",
            "psicopolitica", "agonia do eros", "topologia da violencia", "hiperculturalidade",
            "genealogia da moral", "alem do bem e do mal", "assim falava zaratustra",
            "fenomenologia do espirito", "critica da razao pura", "critica da razao pratica",
        ],
        "Fenomenologia": [
            "phenomenology", "fenomenologia", "ser e tempo", "being and time", "intencionalidade",
        ],
        "Existencialismo": [
            "existentialism", "existencialismo", "o ser e o nada", "o mito de sisifo",
            "temor e tremor", "angustia existencial",
        ],
        "Epistemologia": [
            "epistemology", "epistemologia", "teoria do conhecimento", "theory of knowledge",
            "filosofia do conhecimento",
        ],
        "Metafísica": [
            "metaphysics", "metafisica", "ontologia", "ontology", "filosofia primeira",
        ],
        "Ética": [
            "ethics", "etica", "moral philosophy", "filosofia moral", "deontologia",
            "utilitarismo", "bioetica", "fundamentacao da metafisica dos costumes",
        ],
        "Filosofia política": [
            "political philosophy", "filosofia politica", "contrato social", "leviata",
            "o principe", "origens do totalitarismo", " a condicao humana", "justica como equidade",
        ],
        "Lógica": [
            "formal logic", "logica formal", "mathematical logic", "logica matematica",
            "logica proposicional", "silogismo", "argumentacao filosofica",
        ],
        "Filosofia da linguagem": [
            "philosophy of language", "filosofia da linguagem", "investigações filosoficas",
            "tractatus logico-philosophicus", "semiotica filosofica",
        ],
        "Filosofia da mente": [
            "philosophy of mind", "filosofia da mente", "filosofia da consciencia",
        ],
    },
    "Literatura": {
        "Romance": [
            "novel", "romance literario", "fiction / literary", "literary fiction",
            "ficcao literaria", "romances brasileiros", "romances estrangeiros",
        ],
        "Conto": ["short stories", "contos", "short story", "novella", "antologia de contos"],
        "Poesia": ["poetry", "poesia", "poems", "poemas", "versos", "sonetos", "antologia poetica"],
        "Teatro": ["drama", "plays", "teatro", "dramaturgia", "tragedia grega", "comedia teatral"],
        "Ensaio": ["literary essays", "ensaios literarios", "critica literaria", "literary criticism", "teoria literaria"],
        "Ficção científica": ["science fiction", "ficcao cientifica", "sci-fi", "cyberpunk", "space opera", "distopia", "dystopian"],
        "Fantasia": ["fantasy fiction", "literatura fantastica", "fantasia epica", "high fantasy", "realismo fantastico"],
        "Literatura brasileira": ["brazilian literature", "literatura brasileira", "ficcao brasileira", "modernismo brasileiro"],
        "Literatura latino-americana": ["latin american literature", "literatura latino-americana", "realismo magico"],
        "Literatura clássica": [
            "classic literature", "literatura classica", "classicos da literatura",
            "ancient literature", "greek literature", "roman literature", "epopeia",
            "iliada", "odisseia", "eneida", "divina comedia", "dom quixote",
        ],
    },
    "Ciências humanas": {
        "História": [
            "world history", "historia geral", "historiografia", "historiography",
            "historia do brasil", "historia antiga", "historia medieval", "historia moderna",
            "historia contemporanea", "revolucao francesa", "revolucao industrial",
            "segunda guerra mundial", "primeira guerra mundial", "imperio romano",
            "idade media", "civilizacao ocidental", "arqueologia",
        ],
        "Sociologia": [
            "sociology", "sociologia", "social sciences", "ciencias sociais",
            "teoria sociologica", "estratificacao social", "modernidade liquida",
        ],
        "Antropologia": ["anthropology", "antropologia", "etnografia", "ethnography", "antropologia cultural"],
        "Economia": [
            "economics", "economia politica", "politica economica", "macroeconomia",
            "microeconomia", "historia economica", "capitalismo",
        ],
        "Ciência política": [
            "political science", "ciencia politica", "relacoes internacionais",
            "geopolitica", "teoria do estado", "democracia e cidadania",
        ],
        "Psicologia": [
            "psychology", "psicologia", "psicanalise", "psychoanalysis",
            "terapia cognitivo", "psicologia analitica", "inconsciente",
        ],
        "Educação": [
            "education", "educacao", "pedagogia", "pedagogy", "didatica",
            "ensino e aprendizagem", "politicas educacionais",
        ],
        "Direito": [
            "direito constitucional", "direito civil", "direito penal", "jurisprudencia",
            "teoria geral do direito", "filosofia do direito",
        ],
    },
    "Ciência e tecnologia": {
        "Computação": [
            "computer science", "ciencia da computacao", "computacao", "computador", "computadores",
            "algorithms", "algoritmos", "estrutura de dados", "data structures",
            "arquitetura e organizacao de computadores", "arquitetura de computadores",
            "organizacao de computadores", "computer organization", "computer architecture",
            "sistemas operacionais", "operating systems", "sistema operacional",
            "redes de computadores", "computer networks", "criptografia", "cryptography",
            "seguranca de redes", "network security", "seguranca da informacao", "cybersecurity",
            "banco de dados", "databases", "sql", "compiladores", "sistemas distribuidos",
        ],
        "Programação": [
            "programming", "programacao", "software development", "desenvolvimento de software",
            "engenharia de software", "software engineering", "arquitetura de software",
            "design de software", "software architecture", "software design", "clean code",
            "codigo limpo", "padroes de projeto", "design patterns", "refatoracao",
            "pro git", "controle de versao", "version control", "python", "javascript",
            "typescript", "c++", "java", "rust", "golang", "linux", "unix", "devops", "docker",
        ],
        "Inteligência artificial": [
            "artificial intelligence", "inteligencia artificial", "machine learning",
            "aprendizado de maquina", "deep learning", "redes neurais", "neural networks",
            "ciencia de dados", "data science", "processamento de linguagem natural",
        ],
        "Matemática": [
            "mathematics", "matematica", "calculo", "calculus", "um curso de calculo",
            "algebra linear", "linear algebra", "geometria analitica", "equacoes diferenciais",
            "probabilidade e estatistica", "analise real", "teoria dos numeros", "trigonometria",
        ],
        "Física": [
            "physics", "fisica", "topicos de fisica", "fundamentos de fisica",
            "mecanica classica", "mecanica quantica", "quantum mechanics", "eletromagnetismo",
            "relatividade", "termodinamica", "optica", "astrofisica", "cosmologia",
        ],
        "Biologia e ciências naturais": [
            "biology", "biologia", "genetica", "evolucao biologica", "ecologia",
            "neurociencia", "bioquimica", "quimica geral", "quimica organica", "astronomia",
        ],
        "Engenharia": [
            "engineering", "engenharia eletrica", "engenharia mecanica", "engenharia civil",
            "eletronica", "circuitos eletricos", "automacao e controle", "telecomunicacoes",
        ],
    },
    "Artes e cultura": {
        "Cinema": ["cinema", "film studies", "teoria do cinema", "audiovisual", "cinematografia", "roteiro cinematografico"],
        "Música": ["music", "musica", "teoria musical", "musicologia", "historia da musica", "harmonia musical"],
        "Artes visuais": ["visual arts", "artes visuais", "pintura", "escultura", "historia da arte", "art history", "fotografia artistica"],
        "Arquitetura e design": [
            "design grafico", "graphic design", "tipografia", "typography",
            "arquitetura e urbanismo", "historia da arquitetura", "desenho industrial", "ilustracao",
        ],
        "Crítica cultural": ["cultural studies", "critica cultural", "estudos culturais", "teoria da cultura", "cultura contemporanea"],
    },
}

# Domínios autorais conhecidos (peso elevado e desambiguação confiável)
AUTHOR_DOMAIN_MAP: Dict[str, Tuple[str, str, Optional[str]]] = {
    # (Categoria, Subcategoria, Origem/Detalhe)
    # Filosofia
    "platao": ("Filosofia", "Filosofia antiga e clássica", "Filosofia clássica"),
    "plato": ("Filosofia", "Filosofia antiga e clássica", "Filosofia clássica"),
    "aristoteles": ("Filosofia", "Filosofia antiga e clássica", "Filosofia clássica"),
    "aristotle": ("Filosofia", "Filosofia antiga e clássica", "Filosofia clássica"),
    "socrates": ("Filosofia", "Filosofia antiga e clássica", "Filosofia clássica"),
    "seneca": ("Filosofia", "Filosofia antiga e clássica", "Filosofia clássica"),
    "marco aurelio": ("Filosofia", "Filosofia antiga e clássica", "Filosofia clássica"),
    "marcus aurelius": ("Filosofia", "Filosofia antiga e clássica", "Filosofia clássica"),
    "epicteto": ("Filosofia", "Filosofia antiga e clássica", "Filosofia clássica"),
    "santo agostinho": ("Filosofia", "Metafísica", "Filosofia medieval"),
    "tomas de aquino": ("Filosofia", "Metafísica", "Filosofia medieval"),
    "nicolau maquiavel": ("Filosofia", "Filosofia política", "Filosofia política"),
    "rene descartes": ("Filosofia", "Epistemologia", "Filosofia moderna"),
    "baruch spinoza": ("Filosofia", "Metafísica", "Filosofia moderna"),
    "espinosa": ("Filosofia", "Metafísica", "Filosofia moderna"),
    "thomas hobbes": ("Filosofia", "Filosofia política", "Filosofia moderna"),
    "john locke": ("Filosofia", "Filosofia política", "Filosofia moderna"),
    "david hume": ("Filosofia", "Epistemologia", "Filosofia moderna"),
    "jean-jacques rousseau": ("Filosofia", "Filosofia política", "Filosofia moderna"),
    "immanuel kant": ("Filosofia", "Epistemologia", "Filosofia moderna"),
    "georg wilhelm friedrich hegel": ("Filosofia", "Filosofia continental", "Filosofia moderna"),
    "arthur schopenhauer": ("Filosofia", "Filosofia continental", "Filosofia moderna"),
    "soren kierkegaard": ("Filosofia", "Existencialismo", "Existencialismo"),
    "friedrich nietzsche": ("Filosofia", "Filosofia continental", "Filosofia continental"),
    "nietzsche": ("Filosofia", "Filosofia continental", "Filosofia continental"),
    "edmund husserl": ("Filosofia", "Fenomenologia", "Fenomenologia"),
    "martin heidegger": ("Filosofia", "Fenomenologia", "Fenomenologia"),
    "heidegger": ("Filosofia", "Fenomenologia", "Fenomenologia"),
    "ludwig wittgenstein": ("Filosofia", "Filosofia da linguagem", "Filosofia da linguagem"),
    "wittgenstein": ("Filosofia", "Filosofia da linguagem", "Filosofia da linguagem"),
    "bertrand russell": ("Filosofia", "Filosofia analítica", "Filosofia analítica"),
    "jean-paul sartre": ("Filosofia", "Existencialismo", "Existencialismo"),
    "albert camus": ("Filosofia", "Existencialismo", "Existencialismo"),
    "simone de beauvoir": ("Filosofia", "Existencialismo", "Existencialismo"),
    "hannah arendt": ("Filosofia", "Filosofia política", "Filosofia política"),
    "michel foucault": ("Filosofia", "Filosofia continental", "Filosofia continental"),
    "foucault": ("Filosofia", "Filosofia continental", "Filosofia continental"),
    "gilles deleuze": ("Filosofia", "Filosofia continental", "Filosofia continental"),
    "jacques derrida": ("Filosofia", "Filosofia continental", "Filosofia continental"),
    "jurgen habermas": ("Filosofia", "Filosofia continental", "Filosofia continental"),
    "theodor adorno": ("Filosofia", "Filosofia continental", "Filosofia continental"),
    "walter benjamin": ("Filosofia", "Filosofia continental", "Filosofia continental"),
    "byung-chul han": ("Filosofia", "Filosofia continental", "Filosofia contemporânea"),
    "byung chul han": ("Filosofia", "Filosofia continental", "Filosofia contemporânea"),
    "slavoj zizek": ("Filosofia", "Filosofia continental", "Filosofia contemporânea"),
    "giorgio agamben": ("Filosofia", "Filosofia política", "Filosofia contemporânea"),
    "marilena chaui": ("Filosofia", "Filosofia política", "Filosofia brasileira"),
    # Ciência e Tecnologia (Computação, Matemática, Física)
    "andrew s. tanenbaum": ("Ciência e tecnologia", "Computação", None),
    "tanenbaum": ("Ciência e tecnologia", "Computação", None),
    "william stallings": ("Ciência e tecnologia", "Computação", None),
    "stallings": ("Ciência e tecnologia", "Computação", None),
    "ian sommerville": ("Ciência e tecnologia", "Programação", None),
    "sommerville": ("Ciência e tecnologia", "Programação", None),
    "scott chacon": ("Ciência e tecnologia", "Programação", None),
    "ben straub": ("Ciência e tecnologia", "Programação", None),
    "robert c. martin": ("Ciência e tecnologia", "Programação", None),
    "martin fowler": ("Ciência e tecnologia", "Programação", None),
    "thomas h. cormen": ("Ciência e tecnologia", "Computação", None),
    "donald knuth": ("Ciência e tecnologia", "Computação", None),
    "abraham silberschatz": ("Ciência e tecnologia", "Computação", None),
    "james f. kurose": ("Ciência e tecnologia", "Computação", None),
    "bjarne stroustrup": ("Ciência e tecnologia", "Programação", None),
    "hamilton luiz guidorizzi": ("Ciência e tecnologia", "Matemática", None),
    "guidorizzi": ("Ciência e tecnologia", "Matemática", None),
    "james stewart": ("Ciência e tecnologia", "Matemática", None),
    "elon lages lima": ("Ciência e tecnologia", "Matemática", None),
    "geraldo avila": ("Ciência e tecnologia", "Matemática", None),
    "david halliday": ("Ciência e tecnologia", "Física", None),
    "robert resnick": ("Ciência e tecnologia", "Física", None),
    "paul a. tipler": ("Ciência e tecnologia", "Física", None),
    "richard p. feynman": ("Ciência e tecnologia", "Física", None),
    "francisco ramalho": ("Ciência e tecnologia", "Física", None),
    "ramalho junior": ("Ciência e tecnologia", "Física", None),
    "stephen hawking": ("Ciência e tecnologia", "Física", None),
    "carl sagan": ("Ciência e tecnologia", "Física", None),
    # Literatura
    "machado de assis": ("Literatura", "Literatura brasileira", "Literatura brasileira"),
    "clarice lispector": ("Literatura", "Literatura brasileira", "Literatura brasileira"),
    "guimaraes rosa": ("Literatura", "Literatura brasileira", "Literatura brasileira"),
    "joao guimaraes rosa": ("Literatura", "Literatura brasileira", "Literatura brasileira"),
    "jorge amado": ("Literatura", "Literatura brasileira", "Literatura brasileira"),
    "graciliano ramos": ("Literatura", "Literatura brasileira", "Literatura brasileira"),
    "lima barreto": ("Literatura", "Literatura brasileira", "Literatura brasileira"),
    "jose de alencar": ("Literatura", "Literatura brasileira", "Literatura brasileira"),
    "monteiro lobato": ("Literatura", "Literatura brasileira", "Literatura brasileira"),
    "carlos drummond de andrade": ("Literatura", "Poesia", "Literatura brasileira"),
    "manuel bandeira": ("Literatura", "Poesia", "Literatura brasileira"),
    "cecilia meireles": ("Literatura", "Poesia", "Literatura brasileira"),
    "rubem fonseca": ("Literatura", "Conto", "Literatura brasileira"),
    "dalton trevisan": ("Literatura", "Conto", "Literatura brasileira"),
    "raduan nassar": ("Literatura", "Literatura brasileira", "Literatura brasileira"),
    "ariano suassuna": ("Literatura", "Teatro", "Literatura brasileira"),
    "conceicao evaristo": ("Literatura", "Literatura brasileira", "Literatura brasileira"),
    "fernando pessoa": ("Literatura", "Poesia", "Literatura portuguesa"),
    "jose saramago": ("Literatura", "Romance", "Literatura portuguesa"),
    "eca de queiros": ("Literatura", "Romance", "Literatura portuguesa"),
    "jorge luis borges": ("Literatura", "Literatura latino-americana", "Literatura latino-americana"),
    "gabriel garcia marquez": ("Literatura", "Literatura latino-americana", "Literatura latino-americana"),
    "julio cortazar": ("Literatura", "Literatura latino-americana", "Literatura latino-americana"),
    "mario vargas llosa": ("Literatura", "Literatura latino-americana", "Literatura latino-americana"),
    "pablo neruda": ("Literatura", "Poesia", "Literatura latino-americana"),
    "octavio paz": ("Literatura", "Ensaio", "Literatura latino-americana"),
    "juan rulfo": ("Literatura", "Literatura latino-americana", "Literatura latino-americana"),
    "roberto bolano": ("Literatura", "Literatura latino-americana", "Literatura latino-americana"),
    "homero": ("Literatura", "Literatura clássica", "Literatura clássica"),
    "virgilio": ("Literatura", "Literatura clássica", "Literatura clássica"),
    "ovidio": ("Literatura", "Literatura clássica", "Literatura clássica"),
    "dante alighieri": ("Literatura", "Literatura clássica", "Literatura clássica"),
    "william shakespeare": ("Literatura", "Teatro", "Teatro clássico"),
    "miguel de cervantes": ("Literatura", "Literatura clássica", "Literatura clássica"),
    "fiodor dostoievski": ("Literatura", "Romance", "Literatura russa"),
    "dostoievski": ("Literatura", "Romance", "Literatura russa"),
    "liev tolstoi": ("Literatura", "Romance", "Literatura russa"),
    "franz kafka": ("Literatura", "Romance", "Literatura europeia"),
    "george orwell": ("Literatura", "Ficção científica", "Literatura inglesa"),
    "aldous huxley": ("Literatura", "Ficção científica", "Literatura inglesa"),
    "j. r. r. tolkien": ("Literatura", "Fantasia", "Literatura inglesa"),
    "isaac asimov": ("Literatura", "Ficção científica", "Ficção científica"),
    # Ciências Humanas
    "karl marx": ("Ciências humanas", "Sociologia", "Ciências humanas"),
    "max weber": ("Ciências humanas", "Sociologia", "Ciências humanas"),
    "emile durkheim": ("Ciências humanas", "Sociologia", "Ciências humanas"),
    "zygmunt bauman": ("Ciências humanas", "Sociologia", "Ciências humanas"),
    "pierre bourdieu": ("Ciências humanas", "Sociologia", "Ciências humanas"),
    "eric hobsbawm": ("Ciências humanas", "História", "História"),
    "marc bloch": ("Ciências humanas", "História", "História"),
    "jacques le goff": ("Ciências humanas", "História", "História"),
    "yuval noah harari": ("Ciências humanas", "História", "História"),
    "lilia moritz schwarcz": ("Ciências humanas", "História", "História do Brasil"),
    "boris fausto": ("Ciências humanas", "História", "História do Brasil"),
    "sergio buarque de holanda": ("Ciências humanas", "História", "História do Brasil"),
    "gilberto freyre": ("Ciências humanas", "Sociologia", "Ciências humanas"),
    "caio prado junior": ("Ciências humanas", "História", "História do Brasil"),
    "celso furtado": ("Ciências humanas", "Economia", "Economia"),
    "sigmund freud": ("Ciências humanas", "Psicologia", "Psicanálise"),
    "carl gustav jung": ("Ciências humanas", "Psicologia", "Psicologia"),
    "jacques lacan": ("Ciências humanas", "Psicologia", "Psicanálise"),
    "paulo freire": ("Ciências humanas", "Educação", "Educação"),
}

# Compatibilidade retroativa com AUTHOR_ORIGINS
AUTHOR_ORIGINS: Dict[str, str] = {
    k: v[2] for k, v in AUTHOR_DOMAIN_MAP.items() if v[2]
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

# Termos de contexto tecnológico que impedem falsos positivos em "Artes e cultura > Arquitetura e design"
TECH_CONTEXT_MARKERS: Set[str] = {
    "computador", "computadores", "computer", "computers", "computing", "computacao",
    "software", "hardware", "sistema operacional", "sistemas operacionais", "operating system",
    "rede", "redes", "network", "networks", "criptografia", "cryptography", "seguranca",
    "programacao", "programming", "algoritmo", "algoritmos", "codigo", "code", "git",
    "linux", "unix", "kernel", "processador", "cpu", "memoria", "banco de dados",
    "database", "engenharia de software", "arquitetura de software", "design de software",
    "arquitetura de computadores", "organizacao de computadores", "arquitetura e organizacao",
    "padroes de projeto", "design patterns", "sistemas distribuidos", "microsservicos",
    "web", "api", "python", "java", "javascript", "c++", "calculo", "fisica", "matematica",
}


# ==============================================================================
# CLASSIFICAÇÃO PONDERADA, CONTEXTUAL E EXPLICÁVEL
# ==============================================================================

def _has_keyword(text: str, kw: str) -> bool:
    if not text or not kw:
        return False
    pattern = r"\b" + re.escape(kw) + r"\b"
    return bool(re.search(pattern, text))


def map_raw_subjects_to_taxonomy(
    title: Optional[str],
    author: Optional[str],
    raw_subjects: Optional[List[str]],
    language: Optional[str] = None,
    description: Optional[str] = None,
    publisher: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Analisa os dados do livro usando regras ponderadas e contextuais e retorna:
    - categories: lista ordenada por pontuação de categorias principais identificadas
    - subcategories: lista de pares (categoria, subcategoria)
    - genre: gênero literário (se aplicável)
    - origin: origem literária ou nacionalidade autoral
    - period: período histórico
    - tags: lista limpa de tags normalizadas
    - confidence: nível numérico de confiança (0..100)
    - reasons: justificativas auditáveis da classificação
    """
    cat_scores: Dict[str, float] = {}
    subcat_scores: Dict[Tuple[str, str], float] = {}
    reasons: List[str] = []
    detected_genre: Optional[str] = None
    detected_origin: Optional[str] = None
    detected_period: Optional[str] = None
    normalized_tags: Set[str] = set()

    norm_title = normalize_text_for_matching(title or "")
    norm_author = normalize_text_for_matching(author or "")
    norm_desc = normalize_text_for_matching(description or "")
    norm_pub = normalize_text_for_matching(publisher or "")

    subj_tokens: List[str] = []
    if raw_subjects:
        for subj in raw_subjects:
            if subj and isinstance(subj, str):
                cleaned_subj = subj.strip()
                if cleaned_subj:
                    normalized_tags.add(cleaned_subj)
                    subj_tokens.append(normalize_text_for_matching(cleaned_subj))
    norm_subjects = " ".join(subj_tokens)

    full_text = " ".join(filter(None, [norm_title, norm_author, norm_subjects, norm_desc, norm_pub]))

    # Detecta se o contexto do livro é claramente tecnológico/exatas
    is_tech_context = any(_has_keyword(full_text, marker) for marker in TECH_CONTEXT_MARKERS)

    def _add_score(cat: str, subcat: Optional[str], pts: float, reason_msg: str) -> None:
        cat_scores[cat] = cat_scores.get(cat, 0.0) + pts
        if subcat:
            key = (cat, subcat)
            subcat_scores[key] = subcat_scores.get(key, 0.0) + pts
        if reason_msg not in reasons:
            reasons.append(reason_msg)

    # 1. Domínio Autoral Conhecido (peso alto: 45 pontos)
    if norm_author:
        for known_author, (dom_cat, dom_subcat, dom_origin) in AUTHOR_DOMAIN_MAP.items():
            if _has_keyword(norm_author, known_author):
                _add_score(dom_cat, dom_subcat, 45.0, f"autor reconhecido ({known_author} -> {dom_cat})")
                if dom_origin and not detected_origin:
                    detected_origin = dom_origin

    # 2. Mapeamento ponderado por campo (Título: 35, Assuntos: 30, Descrição: 15)
    field_sources = [
        ("título", norm_title, 35.0),
        ("assunto", norm_subjects, 30.0),
        ("descrição", norm_desc, 15.0),
    ]

    for cat_name, subcats in TAXONOMY_TREE.items():
        for subcat_name, keywords in subcats.items():
            for kw in keywords:
                for field_label, field_text, weight in field_sources:
                    if not field_text:
                        continue
                    if _has_keyword(field_text, kw):
                        # Regra de proteção contextual: evita classificar livros de Computação/Software
                        # em "Artes e cultura" por causa de palavras como "arquitetura" ou "design"
                        if cat_name == "Artes e cultura" and is_tech_context:
                            continue
                        # Evita classificar romances como "História" apenas pela palavra "historia" na sinopse
                        if (
                            cat_name == "Ciências humanas"
                            and subcat_name == "História"
                            and field_label == "descrição"
                            and kw in ("history", "historia")
                        ):
                            continue
                        bonus = 10.0 if " " in kw else 0.0
                        _add_score(
                            cat_name,
                            subcat_name,
                            weight + bonus,
                            f"{field_label} contém '{kw}' ({cat_name} > {subcat_name})",
                        )
                        break

        # Nome direto da categoria principal em título ou assunto
        norm_cat = normalize_text_for_matching(cat_name)
        if _has_keyword(norm_title, norm_cat):
            _add_score(cat_name, None, 30.0, f"título menciona categoria '{cat_name}'")
        elif _has_keyword(norm_subjects, norm_cat):
            _add_score(cat_name, None, 25.0, f"assunto menciona categoria '{cat_name}'")

    # 3. Heurísticas específicas de alta precisão para títulos em português/inglês sem subjects
    if is_tech_context and (
        _has_keyword(norm_title, "arquitetura") or _has_keyword(norm_title, "design")
    ):
        if _has_keyword(norm_title, "software"):
            _add_score(
                "Ciência e tecnologia",
                "Programação",
                50.0,
                "título de arquitetura/design de software",
            )
        elif _has_keyword(norm_title, "computador") or _has_keyword(norm_title, "computadores"):
            _add_score(
                "Ciência e tecnologia",
                "Computação",
                50.0,
                "título de arquitetura/organização de computadores",
            )

    # 4. Detecção de Gênero Literário (evita ativar Literatura se o livro for técnico/científico)
    if not is_tech_context:
        for kw, g_name in GENRE_KEYWORDS.items():
            if _has_keyword(norm_title, kw) or _has_keyword(norm_subjects, kw):
                detected_genre = g_name
                if g_name in ("Romance", "Conto", "Poesia", "Teatro", "Ficção científica", "Fantasia"):
                    _add_score("Literatura", g_name if g_name in TAXONOMY_TREE["Literatura"] else None, 30.0, f"gênero literário '{g_name}'")
                break

    # 5. Detecção de Período Histórico
    for kw, p_name in PERIOD_KEYWORDS.items():
        if _has_keyword(full_text, kw):
            detected_period = p_name
            break

    # 6. Filtragem de categorias por limiar mínimo e dominância para evitar categorias espúrias
    sorted_cats = sorted(cat_scores.items(), key=lambda x: x[1], reverse=True)
    final_categories: List[str] = []
    if sorted_cats:
        top_cat, top_score = sorted_cats[0]
        if top_score >= 15.0:
            final_categories.append(top_cat)
            # Inclui segunda categoria apenas se tiver pontuação forte (>= 50% da principal e >= 35 pts)
            for other_cat, other_score in sorted_cats[1:]:
                if other_score >= 35.0 and other_score >= (top_score * 0.55):
                    final_categories.append(other_cat)

    # Subcategorias compatíveis com as categorias vencedoras
    final_subcategories: List[Tuple[str, str]] = []
    for (c_name, s_name), s_score in sorted(subcat_scores.items(), key=lambda x: x[1], reverse=True):
        if c_name in final_categories and s_score >= 15.0:
            if (c_name, s_name) not in final_subcategories:
                final_subcategories.append((c_name, s_name))

    top_confidence = min(100.0, round(sorted_cats[0][1], 1)) if sorted_cats else 0.0

    return {
        "categories": final_categories,
        "subcategories": final_subcategories,
        "genre": detected_genre,
        "origin": detected_origin,
        "period": detected_period,
        "tags": sorted(list(normalized_tags)),
        "confidence": top_confidence,
        "reasons": reasons[:5],
    }


# ==============================================================================
# PERSISTÊNCIA E SINCRONIZAÇÃO DE CATEGORIAS E TAXONOMIA
# ==============================================================================

def _ensure_category_record(cursor: Any, category_name: str, is_custom: bool = False) -> int:
    """Garante que a categoria exista na tabela `categories` e retorna seu `id`."""
    clean_name = category_name.strip()
    cursor.execute("SELECT id FROM categories WHERE name = ? COLLATE NOCASE;", (clean_name,))
    row = cursor.fetchone()
    if row:
        return int(row["id"])

    default_order = DEFAULT_CATEGORY_ORDER.get(clean_name)
    if default_order is None:
        cursor.execute("SELECT COALESCE(MAX(display_order), 50) + 10 AS next_ord FROM categories;")
        default_order = int(cursor.fetchone()["next_ord"])
        is_custom = True

    cursor.execute(
        """
        INSERT INTO categories (name, description, display_order, is_custom)
        VALUES (?, '', ?, ?);
        """,
        (clean_name, default_order, 1 if is_custom else 0),
    )
    return int(cursor.lastrowid)


def sync_book_taxonomy(
    book_id: str,
    taxonomy: Dict[str, Any],
    is_manual: bool = False,
    conn: Optional[Any] = None,
) -> None:
    """
    Sincroniza a classificação do livro nas tabelas `book_categories`, `subjects` e `book_subjects`.
    REGRA DE OURO: Se `is_manual=False` e o livro já possuir classificação manual (`is_manual=1` em
    `book_categories` ou `'categories'` em `books.manual_lock_fields`), as categorias manuais
    JAMAIS são sobrescritas ou removidas.
    """
    categories = [c.strip() for c in (taxonomy.get("categories") or []) if c and c.strip()]
    subcategories = taxonomy.get("subcategories") or []
    genre = taxonomy.get("genre")
    origin = taxonomy.get("origin")
    period = taxonomy.get("period")
    tags = taxonomy.get("tags") or []
    confidence = float(taxonomy.get("confidence", 100.0 if is_manual else 80.0))
    reasons = taxonomy.get("reasons") or []
    reason_str = "; ".join(reasons) if reasons else ("manual_override" if is_manual else "auto_taxonomy")

    def _execute_sync(cursor: Any) -> None:
        # Verifica se o livro possui bloqueio manual de categorias
        cursor.execute("SELECT manual_lock_fields FROM books WHERE id = ?;", (book_id,))
        book_row = cursor.fetchone()
        locked_fields: Set[str] = set()
        if book_row and book_row["manual_lock_fields"]:
            try:
                locked_fields = set(json.loads(book_row["manual_lock_fields"]))
            except Exception:
                locked_fields = set()

        cursor.execute(
            "SELECT category_id FROM book_categories WHERE book_id = ? AND is_manual = 1;",
            (book_id,),
        )
        manual_cat_ids = {int(r["category_id"]) for r in cursor.fetchall()}

        has_manual_category_lock = bool(manual_cat_ids) or ("categories" in locked_fields)

        # 1. Atualiza `book_categories`
        if is_manual:
            cursor.execute("DELETE FROM book_categories WHERE book_id = ?;", (book_id,))
            subcat_by_cat = {cat: subcat for cat, subcat in subcategories if cat and subcat}
            for cat_name in categories:
                cat_id = _ensure_category_record(cursor, cat_name, is_custom=(cat_name not in DEFAULT_CATEGORY_ORDER))
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO book_categories (
                        book_id, category_id, subcategory, is_manual, confidence, classification_reason
                    ) VALUES (?, ?, ?, 1, 100.0, ?);
                    """,
                    (book_id, cat_id, subcat_by_cat.get(cat_name), reason_str),
                )
            locked_fields.add("categories")
            cursor.execute(
                "UPDATE books SET manual_lock_fields = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                (json.dumps(sorted(list(locked_fields))), book_id),
            )
        elif not has_manual_category_lock:
            cursor.execute("DELETE FROM book_categories WHERE book_id = ? AND is_manual = 0;", (book_id,))
            subcat_by_cat = {cat: subcat for cat, subcat in subcategories if cat and subcat}
            for cat_name in categories:
                cat_id = _ensure_category_record(cursor, cat_name, is_custom=(cat_name not in DEFAULT_CATEGORY_ORDER))
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO book_categories (
                        book_id, category_id, subcategory, is_manual, confidence, classification_reason
                    ) VALUES (?, ?, ?, 0, ?, ?);
                    """,
                    (book_id, cat_id, subcat_by_cat.get(cat_name), confidence, reason_str),
                )

        # 2. Atualiza `subjects` e `book_subjects` preservando vínculos manuais se is_manual=False
        cursor.execute(
            "SELECT subject_id FROM book_subjects WHERE book_id = ? AND is_manual = 1;",
            (book_id,),
        )
        manual_subject_ids = {int(row["subject_id"]) for row in cursor.fetchall()}

        if is_manual:
            cursor.execute("DELETE FROM book_subjects WHERE book_id = ?;", (book_id,))
            manual_subject_ids.clear()
        elif not has_manual_category_lock:
            cursor.execute("DELETE FROM book_subjects WHERE book_id = ? AND is_manual = 0;", (book_id,))
        else:
            # Se a categoria do livro é bloqueada manualmente, não adiciona subjects automáticos
            # que colidam com outra categoria principal
            return

        subjects_to_link: Set[Tuple[str, str, Optional[str]]] = set()
        if subcategories:
            for cat, subcat in subcategories:
                if cat in categories:
                    subjects_to_link.add((f"{cat}: {subcat}", cat, subcat))
        for cat in categories:
            if not any(item[1] == cat for item in subjects_to_link):
                subjects_to_link.add((cat, cat, None))

        # Assuntos/tags importados: vincula apenas quando houver uma categoria válida
        if categories:
            primary_cat = categories[0]
            for t in tags[:8]:
                t_clean = t.strip()
                if t_clean and not any(t_clean.lower() == item[0].lower() for item in subjects_to_link):
                    subjects_to_link.add((f"{primary_cat} — {t_clean}", primary_cat, None))

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
            subj_id = int(cursor.fetchone()["id"])

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
    """Retorna todas as informações taxonômicas e categorias gerenciadas associadas a um livro."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT c.id AS category_id, c.name AS category, c.is_custom, c.display_order,
                   bc.subcategory, bc.is_manual, bc.confidence, bc.classification_reason
            FROM book_categories bc
            JOIN categories c ON bc.category_id = c.id
            WHERE bc.book_id = ?
            ORDER BY c.display_order ASC, c.name COLLATE NOCASE ASC;
            """,
            (book_id,),
        )
        cat_rows = [dict(r) for r in cursor.fetchall()]

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
        subj_rows = [dict(r) for r in cursor.fetchall()]

    if cat_rows:
        categories = [r["category"] for r in cat_rows if r["category"]]
    else:
        categories = sorted(list({r["category"] for r in subj_rows if r["category"] and r["category"] != "Geral"}))

    subcategories = []
    for r in cat_rows:
        if r.get("subcategory"):
            subcategories.append(f"{r['category']}: {r['subcategory']}")
    for r in subj_rows:
        if ":" in r["name"] and r["name"] not in subcategories:
            subcategories.append(r["name"])

    genres = [r["genre"] for r in subj_rows if r["genre"]]
    origins = [r["origin"] for r in subj_rows if r["origin"]]
    periods = [r["period"] for r in subj_rows if r["period"]]
    is_manual = any(bool(r.get("is_manual")) for r in cat_rows) or any(bool(r.get("is_manual")) for r in subj_rows)

    return {
        "categories": categories,
        "category_details": cat_rows,
        "is_manual": is_manual,
        "subcategories": subcategories,
        "genre": genres[0] if genres else None,
        "origin": origins[0] if origins else None,
        "period": periods[0] if periods else None,
        "subjects": subj_rows,
    }


def get_taxonomy_tree() -> List[Dict[str, Any]]:
    """
    Retorna a árvore taxonômica presente no acervo atual respeitando a ordem configurada
    pelo administrador (`categories.display_order`), exibindo apenas categorias que possuem
    livros ativos associados.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT c.id, c.name AS category, c.description, c.display_order, c.is_custom,
                   bc.subcategory, COUNT(DISTINCT bc.book_id) AS book_count
            FROM categories c
            JOIN book_categories bc ON c.id = bc.category_id
            JOIN books b ON bc.book_id = b.id
            WHERE b.is_missing = 0
            GROUP BY c.id, bc.subcategory
            HAVING book_count > 0
            ORDER BY c.display_order ASC, c.name COLLATE NOCASE ASC, book_count DESC;
            """
        )
        rows = [dict(r) for r in cursor.fetchall()]

        # Conta também livros sem categoria
        cursor.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM books b
            WHERE b.is_missing = 0
              AND NOT EXISTS (SELECT 1 FROM book_categories bc WHERE bc.book_id = b.id);
            """
        )
        uncat_row = cursor.fetchone()
        uncategorized_count = int(uncat_row["cnt"]) if uncat_row else 0

    tree_map: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        cat = r["category"]
        subcat = r["subcategory"]
        count = int(r["book_count"])

        if cat not in tree_map:
            tree_map[cat] = {
                "id": r["id"],
                "category": cat,
                "description": r["description"] or "",
                "display_order": r["display_order"],
                "is_custom": bool(r["is_custom"]),
                "total_books": 0,
                "subcategories": [],
                "uncategorized_books": uncategorized_count,
            }
        tree_map[cat]["total_books"] += count
        if subcat:
            tree_map[cat]["subcategories"].append({
                "name": subcat,
                "book_count": count,
            })

    return sorted(list(tree_map.values()), key=lambda x: (x["display_order"], x["category"].lower()))


# ==============================================================================
# GERENCIAMENTO DE CATEGORIAS PERSONALIZÁVEIS (CRUD, MERGE, REORDER, BATCH)
# ==============================================================================

def list_categories(include_empty: bool = True) -> List[Dict[str, Any]]:
    """Lista todas as categorias cadastradas, com contagem de livros e ordem de exibição."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT c.id, c.name, c.description, c.display_order, c.is_custom,
                   c.created_at, c.updated_at,
                   COUNT(DISTINCT CASE WHEN b.is_missing = 0 THEN bc.book_id END) AS book_count,
                   COUNT(DISTINCT CASE WHEN b.is_missing = 0 AND bc.is_manual = 1 THEN bc.book_id END) AS manual_count
            FROM categories c
            LEFT JOIN book_categories bc ON c.id = bc.category_id
            LEFT JOIN books b ON bc.book_id = b.id
            GROUP BY c.id
            ORDER BY c.display_order ASC, c.name COLLATE NOCASE ASC;
            """
        )
        rows = [dict(r) for r in cursor.fetchall()]

    result = []
    for r in rows:
        r["is_custom"] = bool(r["is_custom"])
        r["book_count"] = int(r["book_count"] or 0)
        r["manual_count"] = int(r["manual_count"] or 0)
        if include_empty or r["book_count"] > 0:
            result.append(r)
    return result


def get_uncategorized_books_count() -> int:
    """Retorna o total de livros ativos sem nenhuma categoria atribuída."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM books b
            WHERE b.is_missing = 0
              AND NOT EXISTS (
                  SELECT 1 FROM book_categories bc WHERE bc.book_id = b.id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM book_subjects bs
                  JOIN subjects s ON bs.subject_id = s.id
                  WHERE bs.book_id = b.id AND s.category IS NOT NULL AND TRIM(s.category) != ''
              );
            """
        )
        row = cursor.fetchone()
        return int(row["cnt"]) if row else 0


def create_category(
    name: str,
    description: str = "",
    display_order: Optional[int] = None,
    is_custom: bool = True,
) -> Dict[str, Any]:
    """Cria uma nova categoria na biblioteca."""
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("O nome da categoria não pode estar vazio.")
    if len(clean_name) > 80:
        raise ValueError("O nome da categoria deve ter no máximo 80 caracteres.")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM categories WHERE name = ? COLLATE NOCASE;", (clean_name,))
        if cursor.fetchone():
            raise ValueError(f"Já existe uma categoria chamada '{clean_name}'.")

        if display_order is None:
            cursor.execute("SELECT COALESCE(MAX(display_order), 0) + 10 AS next_ord FROM categories;")
            display_order = int(cursor.fetchone()["next_ord"])

        cursor.execute(
            """
            INSERT INTO categories (name, description, display_order, is_custom)
            VALUES (?, ?, ?, ?);
            """,
            (clean_name, (description or "").strip(), int(display_order), 1 if is_custom else 0),
        )
        cat_id = int(cursor.lastrowid)

    categories = list_categories(include_empty=True)
    for c in categories:
        if c["id"] == cat_id:
            return c
    return {"id": cat_id, "name": clean_name, "description": description, "display_order": display_order, "is_custom": is_custom, "book_count": 0}


def update_category(
    category_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    display_order: Optional[int] = None,
) -> Dict[str, Any]:
    """Atualiza nome, descrição ou ordem de exibição de uma categoria existente."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM categories WHERE id = ?;", (category_id,))
        existing = cursor.fetchone()
        if not existing:
            raise ValueError("Categoria não encontrada.")

        old_name = existing["name"]
        new_name = (name.strip() if name is not None else old_name)
        if not new_name:
            raise ValueError("O nome da categoria não pode estar vazio.")

        if new_name.lower() != old_name.lower():
            cursor.execute(
                "SELECT id FROM categories WHERE name = ? COLLATE NOCASE AND id != ?;",
                (new_name, category_id),
            )
            if cursor.fetchone():
                raise ValueError(f"Já existe outra categoria chamada '{new_name}'.")

        new_desc = description.strip() if description is not None else existing["description"]
        new_order = int(display_order) if display_order is not None else existing["display_order"]

        cursor.execute(
            """
            UPDATE categories
            SET name = ?, description = ?, display_order = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """,
            (new_name, new_desc, new_order, category_id),
        )

        # Sincroniza subjects legados caso o nome tenha mudado
        if new_name != old_name:
            cursor.execute(
                "UPDATE subjects SET category = ? WHERE category = ? COLLATE NOCASE;",
                (new_name, old_name),
            )
            cursor.execute(
                "UPDATE subjects SET name = ? WHERE name = ? COLLATE NOCASE;",
                (new_name, old_name),
            )

    categories = list_categories(include_empty=True)
    for c in categories:
        if c["id"] == category_id:
            return c
    raise ValueError("Categoria não encontrada após atualização.")


def delete_category(category_id: int) -> Dict[str, Any]:
    """
    Exclui uma categoria sem jamais apagar livros ou dados de leitura.
    Remove apenas os vínculos da categoria excluída.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM categories WHERE id = ?;", (category_id,))
        existing = cursor.fetchone()
        if not existing:
            raise ValueError("Categoria não encontrada.")

        cat_name = existing["name"]
        cursor.execute("SELECT COUNT(DISTINCT book_id) AS cnt FROM book_categories WHERE category_id = ?;", (category_id,))
        affected_books = int(cursor.fetchone()["cnt"] or 0)

        # Remove vínculos em book_categories (ON DELETE CASCADE já garante, mas fazemos explícito)
        cursor.execute("DELETE FROM book_categories WHERE category_id = ?;", (category_id,))

        # Remove subjects associados exclusivamente a essa categoria
        cursor.execute(
            """
            DELETE FROM book_subjects
            WHERE subject_id IN (SELECT id FROM subjects WHERE category = ? COLLATE NOCASE);
            """,
            (cat_name,),
        )
        cursor.execute("DELETE FROM subjects WHERE category = ? COLLATE NOCASE;", (cat_name,))
        cursor.execute("DELETE FROM categories WHERE id = ?;", (category_id,))

    return {
        "deleted_id": category_id,
        "deleted_name": cat_name,
        "affected_books": affected_books,
    }


def merge_categories(source_category_id: int, target_category_id: int) -> Dict[str, Any]:
    """
    Mescla `source_category_id` dentro de `target_category_id`:
    - Todos os livros da categoria de origem passam a pertencer à categoria de destino
      (marcados com is_manual=1 para preservar a decisão do administrador).
    - A categoria de origem é removida sem perda de livros.
    """
    if source_category_id == target_category_id:
        raise ValueError("A categoria de origem e a de destino devem ser diferentes.")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM categories WHERE id = ?;", (source_category_id,))
        src = cursor.fetchone()
        cursor.execute("SELECT * FROM categories WHERE id = ?;", (target_category_id,))
        tgt = cursor.fetchone()
        if not src or not tgt:
            raise ValueError("Categoria de origem ou destino não encontrada.")

        src_name = src["name"]
        tgt_name = tgt["name"]

        cursor.execute("SELECT * FROM book_categories WHERE category_id = ?;", (source_category_id,))
        src_links = [dict(r) for r in cursor.fetchall()]

        for link in src_links:
            cursor.execute(
                """
                INSERT INTO book_categories (book_id, category_id, subcategory, is_manual, confidence, classification_reason)
                VALUES (?, ?, ?, 1, 100.0, 'merged_by_admin')
                ON CONFLICT(book_id, category_id) DO UPDATE SET
                    is_manual = 1,
                    subcategory = COALESCE(book_categories.subcategory, excluded.subcategory);
                """,
                (link["book_id"], target_category_id, link.get("subcategory")),
            )

        cursor.execute("DELETE FROM book_categories WHERE category_id = ?;", (source_category_id,))
        cursor.execute(
            "UPDATE subjects SET category = ? WHERE category = ? COLLATE NOCASE;",
            (tgt_name, src_name),
        )
        cursor.execute("DELETE FROM categories WHERE id = ?;", (source_category_id,))

    return {
        "source_id": source_category_id,
        "source_name": src_name,
        "target_id": target_category_id,
        "target_name": tgt_name,
        "merged_books": len(src_links),
    }


def reorder_categories(ordered_ids: List[int]) -> List[Dict[str, Any]]:
    """Atualiza a ordem de exibição (`display_order`) das categorias conforme a lista de IDs."""
    with get_db() as conn:
        cursor = conn.cursor()
        for idx, cid in enumerate(ordered_ids):
            cursor.execute(
                "UPDATE categories SET display_order = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                ((idx + 1) * 10, int(cid)),
            )
    return list_categories(include_empty=True)


def set_book_categories(
    book_id: str,
    categories: List[str],
    subcategories: Optional[List[Tuple[str, str]]] = None,
    is_manual: bool = True,
) -> Dict[str, Any]:
    """
    Define diretamente as categorias de um livro (ex: via Detalhes do Livro ou Painel Admin).
    Quando `is_manual=True`, bloqueia o campo `categories` contra sobrescrita automática.
    Se `categories` for vazio (`[]`), o livro fica sem categoria (`Sem categoria`).
    """
    clean_cats = []
    seen = set()
    for c in (categories or []):
        if c and isinstance(c, str):
            c_strip = c.strip()
            if c_strip and c_strip.lower() not in seen:
                seen.add(c_strip.lower())
                clean_cats.append(c_strip)

    current_tax = get_book_taxonomy(book_id)
    sync_book_taxonomy(
        book_id,
        {
            "categories": clean_cats,
            "subcategories": subcategories or [],
            "genre": current_tax.get("genre"),
            "origin": current_tax.get("origin"),
            "period": current_tax.get("period"),
            "tags": [],
            "confidence": 100.0,
            "reasons": ["manual_user_assignment"],
        },
        is_manual=is_manual,
    )
    return get_book_taxonomy(book_id)


def batch_update_book_categories(
    book_ids: List[str],
    add_categories: Optional[List[str]] = None,
    remove_categories: Optional[List[str]] = None,
    replace_categories: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Aplica adição, remoção ou substituição de categorias em múltiplos livros simultaneamente.
    Todas as alterações manuais em lote são marcadas com `is_manual = 1` e protegidas em `manual_lock_fields`.
    """
    if not book_ids:
        raise ValueError("Selecione ao menos um livro para atualizar categorias.")

    updated_count = 0
    for bid in book_ids:
        current = get_book_taxonomy(bid)
        current_cats = list(current.get("categories") or [])

        if replace_categories is not None:
            new_cats = [c.strip() for c in replace_categories if c and c.strip()]
        else:
            new_cats = list(current_cats)
            if remove_categories:
                rem_lower = {c.strip().lower() for c in remove_categories if c and c.strip()}
                new_cats = [c for c in new_cats if c.lower() not in rem_lower]
            if add_categories:
                existing_lower = {c.lower() for c in new_cats}
                for c in add_categories:
                    if c and c.strip() and c.strip().lower() not in existing_lower:
                        new_cats.append(c.strip())
                        existing_lower.add(c.strip().lower())

        set_book_categories(bid, new_cats, is_manual=True)
        updated_count += 1

    return {
        "updated_books": updated_count,
        "book_ids": book_ids,
    }


def reclassify_all_auto_books() -> Dict[str, int]:
    """
    Reavalia automaticamente todos os livros do acervo que NÃO possuem bloqueio manual
    de categoria (`is_manual = 1` ou `'categories'` em `manual_lock_fields`),
    corrigindo classificações antigas imprecisas e classificando obras pendentes.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, title, author, description, publisher, language, manual_lock_fields
            FROM books
            WHERE is_missing = 0;
            """
        )
        books = [dict(r) for r in cursor.fetchall()]

    reclassified = 0
    skipped_manual = 0
    for b in books:
        bid = b["id"]
        raw_locked = b.get("manual_lock_fields") or "[]"
        try:
            locked = set(json.loads(raw_locked))
        except Exception:
            locked = set()

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM book_categories WHERE book_id = ? AND is_manual = 1 LIMIT 1;",
                (bid,),
            )
            has_manual_cat = cursor.fetchone() is not None
            cursor.execute(
                "SELECT 1 FROM book_subjects WHERE book_id = ? AND is_manual = 1 LIMIT 1;",
                (bid,),
            )
            has_manual_subj = cursor.fetchone() is not None

        if "categories" in locked or has_manual_cat or has_manual_subj:
            skipped_manual += 1
            continue

        # Coleta assuntos/tags brutos existentes ou candidatos
        raw_subjects: List[str] = []
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT raw_json FROM metadata_candidates
                WHERE book_id = ? ORDER BY score DESC LIMIT 1;
                """,
                (bid,),
            )
            cand_row = cursor.fetchone()
            if cand_row and cand_row["raw_json"]:
                try:
                    c_data = json.loads(cand_row["raw_json"])
                    if isinstance(c_data.get("subjects"), list):
                        raw_subjects.extend(c_data["subjects"])
                except Exception:
                    pass

        tax = map_raw_subjects_to_taxonomy(
            title=b.get("title"),
            author=b.get("author"),
            raw_subjects=raw_subjects,
            language=b.get("language"),
            description=b.get("description"),
            publisher=b.get("publisher"),
        )
        # Limpa vínculos automáticos antigos incorretos antes de aplicar a nova classificação ponderada
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM book_categories WHERE book_id = ? AND is_manual = 0;", (bid,))
            cursor.execute("DELETE FROM book_subjects WHERE book_id = ? AND is_manual = 0;", (bid,))

        if tax.get("categories"):
            sync_book_taxonomy(bid, tax, is_manual=False)
            reclassified += 1

    return {
        "total_evaluated": len(books),
        "reclassified": reclassified,
        "skipped_manual": skipped_manual,
    }
