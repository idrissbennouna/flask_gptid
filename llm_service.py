"""Accès au LLM : domaines, prompts système, streaming, résumé et gestion des erreurs."""
from collections.abc import Iterator

import openai
from openai import OpenAI

import config

# ----------------------------------------------------------------------------
# Domaines de spécialisation
# ----------------------------------------------------------------------------
DOMAINS = {
    "general": {
        "label": "Général",
        "prompt": "Tu es un assistant polyvalent, clair et fiable.",
    },
    "informatique": {
        "label": "Informatique",
        "prompt": (
            "Tu es un ingénieur logiciel senior. Tu expliques les concepts techniques avec précision, "
            "fournis du code propre et commenté dans des blocs Markdown avec le langage indiqué, "
            "mentionnes les bonnes pratiques (tests, sécurité, performance) et signales les pièges courants."
        ),
    },
    "finance": {
        "label": "Finance",
        "prompt": (
            "Tu es un analyste financier. Tu expliques les notions de finance, de marchés, de comptabilité "
            "et de paiement avec rigueur, tu détailles les formules et hypothèses, et tu rappelles que tes "
            "réponses sont informatives et ne constituent pas un conseil en investissement personnalisé."
        ),
    },
    "analyse_donnees": {
        "label": "Analyse de données",
        "prompt": (
            "Tu es un data scientist. Tu aides à explorer, nettoyer, visualiser et modéliser des données "
            "(statistiques, SQL, Python/pandas, machine learning). Tu justifies le choix des méthodes, "
            "précises les hypothèses et les limites, et proposes du code reproductible."
        ),
    },
    "gouvernance_donnees": {
        "label": "Gouvernance des données",
        "prompt": (
            "Tu es un expert en gouvernance et qualité des données : catalogage, lignage, rôles (data owner, "
            "data steward), indicateurs de qualité (complétude, unicité, cohérence, validité), "
            "conformité (RGPD, loi 09-08, PCI DSS). Tu proposes des actions concrètes et priorisées."
        ),
    },
    "juridique": {
        "label": "Juridique & conformité",
        "prompt": (
            "Tu es un assistant en conformité réglementaire. Tu expliques les textes et obligations de façon "
            "structurée, tu cites les articles ou référentiels quand tu les connais, tu indiques les "
            "incertitudes et rappelles que tes réponses ne remplacent pas l'avis d'un juriste."
        ),
    },
    "pedagogie": {
        "label": "Enseignement & pédagogie",
        "prompt": (
            "Tu es un tuteur pédagogue. Tu expliques pas à pas, avec des analogies et des exemples simples, "
            "tu vérifies la compréhension et proposes de courts exercices."
        ),
    },
}

BASE_RULES = (
    "Réponds dans la langue de l'utilisateur (français par défaut). "
    "Utilise le format Markdown (titres, listes, tableaux, blocs de code) quand cela améliore la lisibilité. "
    "Si tu n'es pas sûr d'une information, dis-le explicitement plutôt que d'inventer."
)


def public_domains() -> dict:
    return {key: {"label": d["label"]} for key, d in DOMAINS.items()}


def build_system_prompt(domain: str, passages: list[dict] | None = None, dataset_context: str = "") -> str:
    spec = DOMAINS.get(domain, DOMAINS["general"])
    parts = [spec["prompt"], BASE_RULES]
    if dataset_context:
        parts.append(
            "L'utilisateur a importé un jeu de données et a obtenu le rapport de qualité suivant. "
            "Appuie-toi dessus quand il pose des questions sur ses données :\n" + dataset_context
        )
    if passages:
        blocks = [
            f"[{p['label']}] (document « {p['document_name']} », lignes {p['start_line']}-{p['end_line']})\n{p['text']}"
            for p in passages
        ]
        parts.append(
            "Voici des extraits de documents fournis par l'utilisateur. Utilise-les s'ils sont pertinents et "
            "cite la source entre crochets après l'affirmation correspondante, par exemple [S1] ou [S2]. "
            "Ne cite que les extraits réellement utilisés. Si les extraits ne contiennent pas la réponse, "
            "dis-le clairement puis réponds avec tes connaissances générales en le précisant.\n\n"
            + "\n\n".join(blocks)
        )
    return "\n\n".join(parts)


# ----------------------------------------------------------------------------
# Erreurs compréhensibles
# ----------------------------------------------------------------------------
class LLMError(Exception):
    """Erreur convertie en message clair pour l'utilisateur."""

    def __init__(self, code: str, title: str, message: str, status: int = 500, hint: str = ""):
        super().__init__(message)
        self.code, self.title, self.message, self.status, self.hint = code, title, message, status, hint

    def as_dict(self) -> dict:
        return {"code": self.code, "title": self.title, "message": self.message, "hint": self.hint}


QUOTA_MARKERS = ("quota", "billing", "insufficient", "per day", "tpd", "credit", "exceeded your current")


def translate_error(exc: Exception) -> LLMError:
    """Transforme une exception du SDK (ou autre) en LLMError lisible."""
    if isinstance(exc, LLMError):
        return exc
    text = str(exc).lower()

    if isinstance(exc, openai.AuthenticationError):
        return LLMError(
            "invalid_api_key", "Clé API invalide",
            "Le fournisseur a refusé votre clé API : elle est incorrecte, expirée ou révoquée.",
            401, "Vérifiez GROQ_API_KEY dans votre fichier .env puis redémarrez le serveur.",
        )
    if isinstance(exc, openai.PermissionDeniedError):
        return LLMError(
            "permission_denied", "Accès refusé",
            "Votre clé API n'a pas le droit d'utiliser ce modèle ou cette fonctionnalité.",
            403, "Vérifiez les autorisations de la clé et le nom du modèle (LLM_MODEL).",
        )
    if isinstance(exc, openai.RateLimitError):
        code = getattr(exc, "code", None)
        if code == "insufficient_quota" or any(m in text for m in QUOTA_MARKERS):
            return LLMError(
                "quota_exceeded", "Quota dépassé",
                "Le quota de votre compte est épuisé (jetons ou crédits).",
                429, "Attendez la réinitialisation du quota, rechargez votre compte ou changez de clé API.",
            )
        return LLMError(
            "rate_limited", "Trop de requêtes",
            "Le fournisseur limite temporairement le nombre de requêtes.",
            429, "Patientez quelques secondes puis renvoyez votre message.",
        )
    if isinstance(exc, openai.APITimeoutError):
        return LLMError(
            "timeout", "Délai dépassé",
            "Le fournisseur n'a pas répondu à temps.",
            504, "Réessayez dans un instant ou augmentez LLM_TIMEOUT.",
        )
    if isinstance(exc, openai.APIConnectionError):
        return LLMError(
            "connection_error", "Problème de connexion",
            "Impossible de joindre le service d'IA. Votre connexion Internet est peut-être coupée.",
            503, "Vérifiez votre connexion (proxy, pare-feu) puis réessayez.",
        )
    if isinstance(exc, openai.NotFoundError):
        return LLMError(
            "model_not_found", "Modèle introuvable",
            f"Le modèle « {config.LLM_MODEL} » n'existe pas ou n'est pas accessible avec cette clé.",
            404, "Corrigez LLM_MODEL dans le fichier .env.",
        )
    if isinstance(exc, openai.BadRequestError):
        if "context" in text or "too long" in text or "maximum" in text:
            return LLMError(
                "context_too_long", "Conversation trop longue",
                "La conversation dépasse la taille maximale acceptée par le modèle.",
                400, "Démarrez une nouvelle conversation ou réduisez la taille de votre message.",
            )
        return LLMError(
            "bad_request", "Requête refusée",
            "Le fournisseur a refusé la requête (paramètres invalides).", 400, "",
        )
    if isinstance(exc, openai.APIStatusError):
        if exc.status_code >= 500:
            return LLMError(
                "provider_unavailable", "Service indisponible",
                "Le fournisseur d'IA rencontre un problème temporaire.",
                502, "Réessayez dans quelques minutes.",
            )
        return LLMError("api_error", "Erreur du fournisseur", f"Erreur {exc.status_code} renvoyée par l'API.", 502)
    return LLMError(
        "unknown", "Erreur inattendue",
        "Une erreur inattendue est survenue pendant la génération de la réponse.", 500,
        "Consultez la console du serveur pour plus de détails.",
    )


# ----------------------------------------------------------------------------
# Client et appels
# ----------------------------------------------------------------------------
_client = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        if not config.LLM_API_KEY:
            raise LLMError(
                "missing_api_key", "Clé API manquante",
                "Aucune clé API n'est configurée sur le serveur.",
                401, "Ajoutez GROQ_API_KEY=... dans le fichier .env puis redémarrez le serveur.",
            )
        _client = OpenAI(
            api_key=config.LLM_API_KEY,
            base_url=config.LLM_BASE_URL,
            timeout=config.LLM_TIMEOUT,
            max_retries=1,
        )
    return _client


def estimate_tokens(text: str) -> int:
    """Estimation grossière (≈ 4 caractères par token) quand l'API ne renvoie pas l'usage."""
    return max(1, len(text) // 4)


def open_stream(messages: list[dict]):
    """Ouvre le flux. Les erreurs de clé, de quota ou de connexion sont levées ICI,
    avant le début de la réponse HTTP, ce qui permet de renvoyer un vrai code d'erreur."""
    client = get_client()
    kwargs = {"model": config.LLM_MODEL, "messages": messages, "stream": True}
    try:
        try:
            return client.chat.completions.create(**kwargs, stream_options={"include_usage": True})
        except openai.BadRequestError as exc:
            if "stream_options" in str(exc):
                return client.chat.completions.create(**kwargs)
            raise
    except Exception as exc:  # noqa: BLE001
        raise translate_error(exc) from exc


def _extract_usage(chunk) -> dict | None:
    usage = getattr(chunk, "usage", None)
    if usage is None:
        x_groq = getattr(chunk, "x_groq", None)
        usage = getattr(x_groq, "usage", None) if x_groq else None
    if usage is None:
        return None
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    total = getattr(usage, "total_tokens", None)
    if prompt is None and completion is None and total is None:
        return None
    return {
        "prompt_tokens": prompt or 0,
        "completion_tokens": completion or 0,
        "total_tokens": total or ((prompt or 0) + (completion or 0)),
    }


def iter_stream(stream) -> Iterator[tuple[str, object]]:
    """Produit ("token", texte) puis éventuellement ("usage", dict)."""
    try:
        for chunk in stream:
            usage = _extract_usage(chunk)
            if usage:
                yield "usage", usage
            if chunk.choices:
                delta = chunk.choices[0].delta
                text = getattr(delta, "content", None) or ""
                if text:
                    yield "token", text
    except Exception as exc:  # noqa: BLE001
        raise translate_error(exc) from exc


def complete(messages: list[dict]) -> tuple[str, dict]:
    """Appel non-streamé (utilisé pour les résumés)."""
    client = get_client()
    try:
        resp = client.chat.completions.create(model=config.LLM_MODEL, messages=messages)
    except Exception as exc:  # noqa: BLE001
        raise translate_error(exc) from exc
    text = (resp.choices[0].message.content or "").strip()
    usage = _extract_usage(resp) or {}
    return text, usage


def summarize(conversation: dict, messages: list[dict]) -> tuple[str, dict]:
    """Génère un résumé structuré de la conversation."""
    transcript = "\n\n".join(
        f"{'UTILISATEUR' if m['role'] == 'user' else 'ASSISTANT'} : {m['content']}" for m in messages
    )
    if len(transcript) > 14000:  # on garde le début et la fin
        transcript = transcript[:5000] + "\n[...]\n" + transcript[-9000:]
    domain = DOMAINS.get(conversation.get("domain"), DOMAINS["general"])["label"]
    system = (
        "Tu rédiges des résumés de conversation clairs et fidèles, dans la langue de la conversation. "
        "N'invente rien. Structure en Markdown avec exactement ces sections : "
        "## Objectif, ## Points clés, ## Conclusions, ## Actions proposées. "
        "Utilise des listes à puces courtes."
    )
    user = f"Domaine : {domain}\n\nConversation à résumer :\n\n{transcript}"
    return complete([{"role": "system", "content": system}, {"role": "user", "content": user}])
