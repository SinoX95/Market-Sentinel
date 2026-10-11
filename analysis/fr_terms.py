"""Traduction en français des termes renvoyés en anglais par les sources (Yahoo, SEC) :
recommandations d'analystes, secteurs et industries, fonctions des dirigeants, libellés des opérations d'initiés.
Chaque fonction renvoie le terme d'origine en anglais (interface anglaise) ou s'il est inconnu."""
import re

from analysis.lang import lang

GRADES = {
    "buy": "Achat", "strong buy": "Achat fort", "moderate buy": "Achat modéré", "speculative buy": "Achat spéculatif",
    "long-term buy": "Achat long terme", "conviction buy": "Achat de conviction", "outperform": "Surperformance",
    "market outperform": "Surperformance du marché", "sector outperform": "Surperformance du secteur", "overweight": "Surpondérer",
    "accumulate": "Accumuler", "add": "Renforcer", "positive": "Positif", "top pick": "Valeur favorite",
    "hold": "Conserver", "neutral": "Neutre", "equal-weight": "Pondération neutre", "equal weight": "Pondération neutre",
    "market perform": "En ligne avec le marché", "sector perform": "En ligne avec le secteur", "peer perform": "En ligne avec les pairs",
    "in-line": "En ligne", "in line": "En ligne", "perform": "En ligne", "market weight": "Pondération marché",
    "sector weight": "Pondération secteur", "mixed": "Mitigé", "fair value": "Juste valeur", "sector neutral": "Neutre (secteur)",
    "underperform": "Sous-performance", "market underperform": "Sous-performance du marché",
    "sector underperform": "Sous-performance du secteur", "underweight": "Sous-pondérer", "reduce": "Alléger",
    "negative": "Négatif", "sell": "Vente", "strong sell": "Vente forte", "tender": "Apporter à l'offre", "not rated": "Non noté",
}
ACTIONS = {"main": "maintenu", "reit": "réitéré", "up": "relevé", "down": "abaissé", "init": "initiation", "target": "objectif"}

SECTORS = {
    "technology": "Technologie", "healthcare": "Santé", "financial services": "Services financiers",
    "consumer cyclical": "Consommation cyclique", "consumer defensive": "Consommation de base", "industrials": "Industrie",
    "energy": "Énergie", "utilities": "Services publics", "real estate": "Immobilier", "basic materials": "Matériaux de base",
    "communication services": "Communication",
}
INDUSTRIES = {
    "semiconductors": "Semi-conducteurs", "semiconductor equipment & materials": "Équipements et matériaux pour semi-conducteurs",
    "software - infrastructure": "Logiciels d'infrastructure", "software - application": "Logiciels applicatifs",
    "information technology services": "Services informatiques", "communication equipment": "Équipements de communication",
    "computer hardware": "Matériel informatique", "electronic components": "Composants électroniques",
    "consumer electronics": "Électronique grand public", "scientific & technical instruments": "Instruments scientifiques et techniques",
    "electronics & computer distribution": "Distribution d'électronique et d'informatique", "solar": "Solaire",
    "internet content & information": "Contenus et services Internet", "internet retail": "Commerce en ligne",
    "entertainment": "Divertissement", "telecom services": "Télécommunications", "electronic gaming & multimedia": "Jeux vidéo et multimédia",
    "advertising agencies": "Publicité", "publishing": "Édition", "broadcasting": "Audiovisuel",
    "electrical equipment & parts": "Équipements électriques", "aerospace & defense": "Aéronautique et défense",
    "specialty industrial machinery": "Machines industrielles spécialisées", "farm & heavy construction machinery": "Machines agricoles et de chantier",
    "engineering & construction": "Ingénierie et construction", "industrial distribution": "Distribution industrielle",
    "building products & equipment": "Produits et équipements de construction", "railroads": "Transport ferroviaire",
    "airlines": "Compagnies aériennes", "integrated freight & logistics": "Fret et logistique", "trucking": "Transport routier",
    "waste management": "Gestion des déchets", "conglomerates": "Conglomérats", "security & protection services": "Sécurité et protection",
    "specialty business services": "Services aux entreprises spécialisés", "consulting services": "Conseil",
    "staffing & employment services": "Recrutement et intérim", "rental & leasing services": "Location et crédit-bail",
    "pollution & treatment controls": "Contrôle de la pollution", "tools & accessories": "Outillage",
    "banks - diversified": "Banques diversifiées", "banks - regional": "Banques régionales", "asset management": "Gestion d'actifs",
    "capital markets": "Marchés de capitaux", "credit services": "Services de crédit", "insurance - diversified": "Assurance diversifiée",
    "insurance - life": "Assurance vie", "insurance - property & casualty": "Assurance dommages", "insurance brokers": "Courtage d'assurance",
    "insurance - reinsurance": "Réassurance", "insurance - specialty": "Assurance spécialisée", "financial data & stock exchanges": "Données financières et bourses",
    "financial conglomerates": "Conglomérats financiers", "mortgage finance": "Crédit immobilier",
    "drug manufacturers - general": "Laboratoires pharmaceutiques", "drug manufacturers - specialty & generic": "Pharmacie spécialisée et génériques",
    "biotechnology": "Biotechnologies", "medical devices": "Dispositifs médicaux", "medical instruments & supplies": "Instruments et fournitures médicaux",
    "diagnostics & research": "Diagnostic et recherche", "healthcare plans": "Assurance santé", "medical care facilities": "Établissements de soins",
    "health information services": "Services d'information de santé", "medical distribution": "Distribution médicale", "pharmaceutical retailers": "Pharmacies",
    "auto manufacturers": "Constructeurs automobiles", "auto parts": "Équipementiers automobiles", "auto & truck dealerships": "Concessionnaires",
    "specialty retail": "Commerce spécialisé", "apparel retail": "Prêt-à-porter", "home improvement retail": "Bricolage",
    "restaurants": "Restauration", "lodging": "Hôtellerie", "resorts & casinos": "Complexes de loisirs et casinos", "travel services": "Services de voyage",
    "leisure": "Loisirs", "footwear & accessories": "Chaussures et accessoires", "apparel manufacturing": "Habillement",
    "luxury goods": "Luxe", "residential construction": "Construction résidentielle", "furnishings, fixtures & appliances": "Ameublement et électroménager",
    "packaging & containers": "Emballage", "personal services": "Services à la personne", "department stores": "Grands magasins",
    "discount stores": "Magasins discount", "grocery stores": "Supermarchés", "household & personal products": "Produits ménagers et d'hygiène",
    "packaged foods": "Alimentation transformée", "beverages - non-alcoholic": "Boissons sans alcool", "beverages - brewers": "Brasseurs",
    "beverages - wineries & distilleries": "Vins et spiritueux", "confectioners": "Confiserie", "farm products": "Produits agricoles",
    "tobacco": "Tabac", "food distribution": "Distribution alimentaire", "education & training services": "Éducation et formation",
    "oil & gas integrated": "Pétrole et gaz intégrés", "oil & gas e&p": "Exploration-production pétrolière",
    "oil & gas midstream": "Transport pétrolier et gazier", "oil & gas refining & marketing": "Raffinage et distribution pétrolière",
    "oil & gas equipment & services": "Équipements et services pétroliers", "uranium": "Uranium", "thermal coal": "Charbon",
    "utilities - regulated electric": "Électricité (régulée)", "utilities - renewable": "Énergies renouvelables",
    "utilities - diversified": "Services publics diversifiés", "utilities - regulated gas": "Gaz (régulé)",
    "utilities - independent power producers": "Producteurs d'électricité indépendants", "utilities - regulated water": "Eau (régulée)",
    "reit - specialty": "Foncières spécialisées", "reit - industrial": "Foncières industrielles", "reit - retail": "Foncières commerciales",
    "reit - residential": "Foncières résidentielles", "reit - office": "Foncières de bureaux", "reit - healthcare facilities": "Foncières santé",
    "reit - diversified": "Foncières diversifiées", "real estate services": "Services immobiliers",
    "specialty chemicals": "Chimie de spécialité", "chemicals": "Chimie", "gold": "Or", "silver": "Argent", "copper": "Cuivre",
    "steel": "Acier", "aluminum": "Aluminium", "other industrial metals & mining": "Métaux industriels et mines",
    "agricultural inputs": "Intrants agricoles", "building materials": "Matériaux de construction", "lumber & wood production": "Bois",
}
POSITIONS = [  # du plus précis au plus général
    (r"beneficial owner of more than 10% of a class of security", "Actionnaire de plus de 10 %"),
    (r"chief executive officer", "Directeur général"), (r"chief financial officer", "Directeur financier"),
    (r"chief operating officer", "Directeur des opérations"), (r"chief technology officer", "Directeur technique"),
    (r"chief accounting officer", "Directeur comptable"), (r"chief marketing officer", "Directeur marketing"),
    (r"chief legal officer", "Directeur juridique"), (r"chief people officer", "Directeur des ressources humaines"),
    (r"chief information officer", "Directeur des systèmes d'information"), (r"chief revenue officer", "Directeur commercial"),
    (r"chief security officer", "Directeur de la sécurité"), (r"chief product officer", "Directeur produit"),
    (r"chief strategy officer", "Directeur de la stratégie"), (r"chief scientific officer", "Directeur scientifique"),
    (r"general counsel", "Directeur juridique"), (r"chairman of the board", "Président du conseil"), (r"chairman", "Président du conseil"),
    (r"executive vice president", "Vice-président exécutif"), (r"senior vice president", "Vice-président senior"),
    (r"vice president", "Vice-président"), (r"president", "Président"), (r"\bceo\b", "Directeur général"), (r"\bcfo\b", "Directeur financier"),
    (r"\bcoo\b", "Directeur des opérations"), (r"\bcto\b", "Directeur technique"), (r"\bevp\b", "Vice-président exécutif"),
    (r"\bsvp\b", "Vice-président senior"), (r"secretary", "Secrétaire"), (r"treasurer", "Trésorier"), (r"director", "Administrateur"),
    (r"officer", "Cadre dirigeant"), (r"founder", "Fondateur"), (r"\band\b", "et"),
]


def _fr():
    return lang() == "fr"


def grade(x):
    if not x or not _fr():
        return x
    return GRADES.get(x.strip().lower(), x)


def action(x):
    if not x or not _fr():
        return x
    return ACTIONS.get(x.strip().lower(), x)


def sector(x):
    if not x or not _fr():
        return x
    return SECTORS.get(x.strip().lower(), x)


def industry(x):
    if not x or not _fr():
        return x
    return INDUSTRIES.get(x.strip().lower(), x)


def position(x):
    if not x or not _fr():
        return x
    out = x
    for pat, fr in POSITIONS:
        out = re.sub(pat, fr, out, flags=re.I)
    return out


def _n(v):
    return v.replace(",", "").replace(".", ",")


def insider_text(x):
    """« Sale at price 246.08 - 262.27 per share. » -> « Vente entre 246,08 et 262,27 par action »."""
    if not x or not _fr():
        return x
    s = x.strip().rstrip(".")
    m = re.match(r"(?i)^(sale|purchase|stock award\s*\(grant\)|stock gift|conversion of exercise of derivative security|option exercise)"
                 r"(?: at price ([\d.,]+)(?:\s*-\s*([\d.,]+))? per share)?$", s)
    if not m:
        low = s.lower()
        if low.startswith("sale"):
            return "Vente"
        if low.startswith("purchase"):
            return "Achat"
        return x
    kind = m.group(1).lower()
    word = ("Vente" if kind == "sale" else "Achat" if kind == "purchase" else "Attribution gratuite d'actions" if kind.startswith("stock award")
            else "Don d'actions" if kind == "stock gift" else "Exercice d'options")
    a, b = m.group(2), m.group(3)
    if not a or kind.startswith("stock award") or kind == "stock gift":
        return word
    return f"{word} entre {_n(a)} et {_n(b)} par action" if b else f"{word} à {_n(a)} par action"


def analyst_row(a):
    return {**a, "code": (a.get("code") or a.get("action") or "").lower(), "from": grade(a.get("from")), "to": grade(a.get("to")), "action": action(a.get("action"))}


def events(ev):
    if not ev or not _fr():
        return ev
    return {**ev, "analyst": [analyst_row(a) for a in ev.get("analyst") or []]}


def insiders(ins):
    if not ins or not _fr():
        return ins
    return {**ins, "recent": [{**r, "position": position(r.get("position")), "text": insider_text(r.get("text"))} for r in ins.get("recent") or []]}
