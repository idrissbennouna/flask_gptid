"""Analyse de la qualité et de la gouvernance d'un jeu de données importé.

Formats : CSV/TSV, Excel (.xlsx), JSON, base SQLite (.db/.sqlite/.sqlite3).
Indicateurs : complétude, unicité, cohérence, validité, anomalies + score global (0-100).
"""
import io
import json
import os
import re
import sqlite3
import tempfile
import warnings

import pandas as pd

ALLOWED_EXTENSIONS = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".json", ".db", ".sqlite", ".sqlite3"}
MAX_ROWS = 100_000
MAX_TABLES = 25

PLACEHOLDERS = {"", "-", "--", "?", "n/a", "na", "null", "none", "nan", "nd", "n.d.", "#n/a"}
WEIGHTS = {"completeness": 0.30, "uniqueness": 0.20, "consistency": 0.20, "validity": 0.20, "anomalies": 0.10}


# ----------------------------------------------------------------------------
# Chargement
# ----------------------------------------------------------------------------
def load_tables(data: bytes, filename: str) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Retourne ({nom_table: DataFrame}, notes)."""
    ext = os.path.splitext(filename)[1].lower()
    notes: list[str] = []

    if ext in (".csv", ".tsv", ".txt"):
        df = _read_csv(data, tab=(ext == ".tsv"))
        tables = {os.path.splitext(os.path.basename(filename))[0] or "données": df}
    elif ext in (".xlsx", ".xlsm"):
        try:
            sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, nrows=MAX_ROWS)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("Impossible de lire ce fichier Excel.") from exc
        tables = dict(list(sheets.items())[:MAX_TABLES])
    elif ext == ".json":
        tables = _read_json(data)
    elif ext in (".db", ".sqlite", ".sqlite3"):
        tables = _read_sqlite(data)
    else:
        raise ValueError("Format non pris en charge. Utilisez CSV, Excel, JSON ou SQLite.")

    tables = {str(k): v for k, v in tables.items() if v is not None and len(v.columns) > 0}
    if not tables:
        raise ValueError("Aucune table exploitable n'a été trouvée dans ce fichier.")
    for name, df in tables.items():
        if len(df) >= MAX_ROWS:
            notes.append(f"« {name} » tronquée aux {MAX_ROWS:,} premières lignes.".replace(",", " "))
    return tables, notes


def _read_csv(data: bytes, tab: bool = False) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return pd.read_csv(
                io.BytesIO(data), sep="\t" if tab else None, engine="python",
                encoding=encoding, nrows=MAX_ROWS,
            )
        except UnicodeDecodeError:
            continue
        except pd.errors.EmptyDataError as exc:
            raise ValueError("Le fichier est vide.") from exc
        except Exception as exc:  # noqa: BLE001
            raise ValueError("Impossible de lire ce fichier CSV (séparateur ou format invalide).") from exc
    raise ValueError("Encodage du fichier non reconnu (utilisez UTF-8).")


def _read_json(data: bytes) -> dict[str, pd.DataFrame]:
    try:
        obj = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Fichier JSON invalide.") from exc
    if isinstance(obj, list):
        return {"données": pd.json_normalize(obj[:MAX_ROWS])}
    if isinstance(obj, dict):
        tables = {k: pd.json_normalize(v[:MAX_ROWS]) for k, v in obj.items()
                  if isinstance(v, list) and v and all(isinstance(x, dict) for x in v)}
        return tables or {"données": pd.json_normalize(obj)}
    raise ValueError("Le JSON doit contenir une liste d'objets ou un objet.")


def _read_sqlite(data: bytes) -> dict[str, pd.DataFrame]:
    if not data.startswith(b"SQLite format 3\x00"):
        raise ValueError("Ce fichier n'est pas une base SQLite valide.")
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )]
            tables = {}
            for name in names[:MAX_TABLES]:
                quoted = '"' + name.replace('"', '""') + '"'
                tables[name] = pd.read_sql_query(f"SELECT * FROM {quoted} LIMIT {MAX_ROWS}", conn)
            return tables
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise ValueError("Impossible de lire cette base SQLite.") from exc
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# ----------------------------------------------------------------------------
# Détection de règles
# ----------------------------------------------------------------------------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$")
KEY_NAME_RE = re.compile(r"(^|_)(id|code|ref|reference|num|numero|cin|email|mail)($|_)|_id$|^id", re.I)
NON_NEGATIVE_RE = re.compile(r"age|prix|price|montant|amount|quantite|quantity|qte|salaire|salary|total|solde_?initial", re.I)
DATE_PATTERNS = {
    "AAAA-MM-JJ": re.compile(r"^\d{4}-\d{2}-\d{2}"),
    "JJ/MM/AAAA": re.compile(r"^\d{1,2}/\d{1,2}/\d{4}"),
    "JJ-MM-AAAA": re.compile(r"^\d{1,2}-\d{1,2}-\d{4}"),
    "AAAA/MM/JJ": re.compile(r"^\d{4}/\d{2}/\d{2}"),
}


def _name_has(name: str, pattern: str) -> bool:
    """Teste un mot dans un nom de colonne sans confondre 'nom' avec 'denomination'."""
    return re.search(rf"(?<![a-z]){pattern}(?![a-z])", name.lower()) is not None


PII_NAME_RULES = [
    ("(e_?mail|mail|courriel)", "Adresse e-mail"),
    ("(phone|tel|telephone|mobile|gsm)", "Numéro de téléphone"),
    ("(cin|cnie|passport|passeport|ssn|nss)", "Pièce d'identité"),
    ("(iban|rib)", "Compte bancaire"),
    ("(card|carte|pan|cc_?num)", "Carte bancaire"),
    ("(nom|prenom|name|lastname|firstname)", "Nom / prénom"),
    ("(adresse|address)", "Adresse postale"),
    ("(naissance|birth|dob)", "Date de naissance"),
    ("(salaire|salary)", "Donnée financière personnelle"),
]


def luhn_ok(number: str) -> bool:
    digits = [int(c) for c in number][::-1]
    total = sum(d if i % 2 == 0 else (d * 2 - 9 if d * 2 > 9 else d * 2) for i, d in enumerate(digits))
    return total % 10 == 0


def _is_text(s: pd.Series) -> bool:
    """Colonne de texte (object, string, ou 'str' avec pandas 3)."""
    return s.dtype == object or pd.api.types.is_string_dtype(s.dtype)


def _missing_mask(s: pd.Series) -> pd.Series:
    mask = s.isna()
    if _is_text(s):
        text = s.astype("string").str.strip().str.lower()
        mask = mask | text.isin(PLACEHOLDERS).fillna(False)
    return mask.astype(bool)


def _pct(part: float, whole: float) -> float:
    return round(100 * part / whole, 2) if whole else 0.0


def _severity(share: float, high: float, medium: float) -> str:
    return "high" if share >= high else "medium" if share >= medium else "low"


# ----------------------------------------------------------------------------
# Analyse d'une colonne
# ----------------------------------------------------------------------------
def _analyze_column(name: str, s: pd.Series, n_rows: int) -> dict:
    missing = _missing_mask(s)
    values = s[~missing]
    n_valid = int(len(values))
    issues: list[dict] = []
    inconsistent = invalid = outliers = 0
    kind = "numérique" if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s) else (
        "date" if pd.api.types.is_datetime64_any_dtype(s) else "texte")
    is_text = _is_text(s)
    str_values = values.astype(str).str.strip() if is_text else None

    n_missing = int(missing.sum())
    if n_missing:
        share = n_missing / max(n_rows, 1)
        issues.append({"type": "valeurs_manquantes", "column": name, "count": n_missing,
                       "severity": _severity(share, 0.30, 0.10),
                       "message": f"{n_missing} valeur(s) manquante(s) ({_pct(n_missing, n_rows)} %)."})

    if is_text and n_valid:
        # Types mélangés (nombres et texte dans la même colonne)
        numeric_like = pd.to_numeric(str_values.str.replace(",", ".", regex=False), errors="coerce").notna()
        share_num = float(numeric_like.mean())
        if 0 < share_num < 1 and share_num >= 0.02:
            kind = "numérique (texte parasite)" if share_num > 0.5 else "texte"
            minority = int(min(numeric_like.sum(), (~numeric_like).sum()))
            inconsistent += minority
            issues.append({"type": "types_mélangés", "column": name, "count": minority,
                           "severity": _severity(minority / n_valid, 0.10, 0.03),
                           "message": f"Types mélangés : {minority} valeur(s) ne respectent pas le type dominant."})
        elif share_num == 1:
            kind = "numérique (stocké en texte)"

        # Variantes de casse / d'espaces dans les colonnes catégorielles
        if values.nunique() <= max(50, 0.5 * n_valid) and not numeric_like.all():
            counts = str_values.value_counts()
            normalized = counts.index.to_series().str.lower().str.replace(r"\s+", " ", regex=True)
            variants = 0
            for _, group in counts.groupby(normalized.values):
                if len(group) > 1:
                    variants += int(group.sum() - group.max())
            if variants:
                inconsistent += variants
                issues.append({"type": "variantes_casse", "column": name, "count": variants,
                               "severity": _severity(variants / n_valid, 0.10, 0.02),
                               "message": f"{variants} valeur(s) ne diffèrent que par la casse ou les espaces."})

        # Dates : formats multiples et valeurs invalides
        if _name_has(name, "(date|dt|jour)") or "date" in name.lower():
            found = {label: int(str_values.str.match(p).sum()) for label, p in DATE_PATTERNS.items()}
            found = {k: v for k, v in found.items() if v}
            if len(found) > 1:
                minority = sum(found.values()) - max(found.values())
                inconsistent += minority
                issues.append({"type": "formats_date", "column": name, "count": minority,
                               "severity": "medium",
                               "message": "Formats de date multiples : " + ", ".join(found) + "."})
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    parsed = pd.to_datetime(str_values, errors="coerce", dayfirst=True, format="mixed")
                except (TypeError, ValueError):  # pandas < 2.0
                    parsed = pd.to_datetime(str_values, errors="coerce", dayfirst=True)
            bad = int(parsed.isna().sum())
            if bad:
                invalid += bad
                issues.append({"type": "dates_invalides", "column": name, "count": bad,
                               "severity": _severity(bad / n_valid, 0.05, 0.0),
                               "message": f"{bad} date(s) illisible(s) ou impossible(s)."})
            kind = "date"

        # E-mails
        if _name_has(name, "(e_?mail|mail|courriel)"):
            bad = int((~str_values.str.match(EMAIL_RE)).sum())
            if bad:
                invalid += bad
                issues.append({"type": "email_invalide", "column": name, "count": bad,
                               "severity": _severity(bad / n_valid, 0.05, 0.0),
                               "message": f"{bad} adresse(s) e-mail au format invalide."})

    # Colonnes numériques : règles métier + valeurs extrêmes
    numeric = None
    if kind.startswith("numérique") and n_valid:
        numeric = (values if pd.api.types.is_numeric_dtype(s) else
                   pd.to_numeric(str_values.str.replace(",", ".", regex=False), errors="coerce").dropna())
    if numeric is not None and len(numeric):
        if NON_NEGATIVE_RE.search(name):
            neg = int((numeric < 0).sum())
            if neg:
                invalid += neg
                issues.append({"type": "valeurs_négatives", "column": name, "count": neg,
                               "severity": _severity(neg / n_valid, 0.05, 0.0),
                               "message": f"{neg} valeur(s) négative(s) alors que la colonne ne devrait pas en contenir."})
        if _name_has(name, "age"):
            too_old = int((numeric > 120).sum())
            if too_old:
                invalid += too_old
                issues.append({"type": "âge_invalide", "column": name, "count": too_old,
                               "severity": "high", "message": f"{too_old} âge(s) supérieur(s) à 120 ans."})
        if len(numeric) >= 8:
            q1, q3 = numeric.quantile(0.25), numeric.quantile(0.75)
            iqr = q3 - q1
            if iqr > 0:
                n_out = int(((numeric < q1 - 1.5 * iqr) | (numeric > q3 + 1.5 * iqr)).sum())
                if n_out:
                    outliers += n_out
                    issues.append({"type": "anomalies", "column": name, "count": n_out,
                                   "severity": "medium" if n_out / len(numeric) >= 0.05 else "low",
                                   "message": f"{n_out} valeur(s) aberrante(s) détectée(s) (méthode IQR)."})

    if n_valid and values.nunique() == 1 and n_rows > 1:
        issues.append({"type": "colonne_constante", "column": name, "count": n_rows, "severity": "low",
                       "message": "Colonne constante (une seule valeur) : peu informative."})

    inconsistent, invalid = min(inconsistent, n_valid), min(invalid, n_valid)
    return {
        "name": str(name),
        "type": kind,
        "missing": n_missing,
        "missing_pct": _pct(n_missing, n_rows),
        "unique": int(values.nunique()) if n_valid else 0,
        "inconsistent": int(inconsistent),
        "invalid": int(invalid),
        "outliers": int(outliers),
        "n_valid": n_valid,
        "numeric_valid": int(len(numeric)) if numeric is not None else 0,
        "issues": issues,
    }


# ----------------------------------------------------------------------------
# Gouvernance : données personnelles / sensibles
# ----------------------------------------------------------------------------
def _detect_pii(df: pd.DataFrame) -> list[dict]:
    found = []
    for col in df.columns:
        reasons = [label for pattern, label in PII_NAME_RULES if _name_has(str(col), pattern)]
        sample = df[col].dropna().astype(str).str.strip().head(500)
        if len(sample):
            if sample.str.match(EMAIL_RE).mean() > 0.5:
                reasons.append("Adresse e-mail")
            if sample.str.match(IBAN_RE).mean() > 0.5:
                reasons.append("IBAN")
            digits = sample.str.replace(r"[\s-]", "", regex=True)
            card_like = digits[digits.str.fullmatch(r"\d{13,19}")]
            if len(card_like) / len(sample) > 0.5 and card_like.map(luhn_ok).mean() > 0.8:
                reasons.append("Numéro de carte bancaire (Luhn valide)")
        if reasons:
            found.append({"column": str(col), "reason": ", ".join(dict.fromkeys(reasons))})
    return found


RECOMMENDATIONS = {
    "valeurs_manquantes": "Compléter les valeurs manquantes à la source ou définir des règles d'imputation documentées.",
    "types_mélangés": "Imposer un type par colonne (contraintes de saisie, validation à l'import).",
    "variantes_casse": "Normaliser les libellés (casse, espaces) ou utiliser des référentiels / listes de valeurs.",
    "formats_date": "Uniformiser les dates au format ISO 8601 (AAAA-MM-JJ).",
    "dates_invalides": "Corriger ou rejeter les dates invalides lors de la saisie.",
    "email_invalide": "Ajouter une validation de format des adresses e-mail.",
    "valeurs_négatives": "Ajouter des contraintes de domaine (CHECK ≥ 0) sur les colonnes concernées.",
    "âge_invalide": "Borner l'âge (0-120) et vérifier les dates de naissance.",
    "anomalies": "Examiner les valeurs aberrantes : erreur de saisie, unité différente ou cas réel à conserver.",
    "doublons": "Dédoublonner et définir une clé primaire / contrainte d'unicité.",
    "clé_dupliquée": "Garantir l'unicité des identifiants (contrainte UNIQUE) et fusionner les doublons.",
}


# ----------------------------------------------------------------------------
# Analyse d'une table
# ----------------------------------------------------------------------------
def analyze_table(name: str, df: pd.DataFrame) -> dict:
    df = df.copy()
    df.columns = [str(c) for c in df.columns]
    n_rows, n_cols = df.shape
    columns = [_analyze_column(c, df[c], n_rows) for c in df.columns]
    issues = [i for col in columns for i in col["issues"]]

    # Doublons de lignes
    try:
        dup_rows = int(df.astype(str).duplicated().sum())
    except Exception:  # noqa: BLE001
        dup_rows = 0
    if dup_rows:
        issues.append({"type": "doublons", "column": None, "count": dup_rows,
                       "severity": _severity(dup_rows / max(n_rows, 1), 0.05, 0.0),
                       "message": f"{dup_rows} ligne(s) entièrement dupliquée(s)."})

    # Doublons sur colonnes clés
    key_dups = 0
    for col in df.columns:
        if KEY_NAME_RE.search(col):
            present = df[col][~_missing_mask(df[col])]
            dups = int(present.duplicated().sum())
            if dups:
                key_dups = max(key_dups, dups)
                issues.append({"type": "clé_dupliquée", "column": col, "count": dups,
                               "severity": _severity(dups / max(n_rows, 1), 0.02, 0.0),
                               "message": f"{dups} valeur(s) en double dans la colonne clé « {col} »."})

    total_cells = n_rows * n_cols
    missing = sum(c["missing"] for c in columns)
    valid_cells = total_cells - missing
    numeric_cells = sum(c["numeric_valid"] for c in columns)
    duplicates = max(dup_rows, key_dups)

    indicators = {
        "completeness": 1 - missing / total_cells if total_cells else 1,
        "uniqueness": 1 - duplicates / n_rows if n_rows else 1,
        "consistency": 1 - sum(c["inconsistent"] for c in columns) / valid_cells if valid_cells else 1,
        "validity": 1 - sum(c["invalid"] for c in columns) / valid_cells if valid_cells else 1,
        "anomalies": 1 - sum(c["outliers"] for c in columns) / numeric_cells if numeric_cells else 1,
    }
    indicators = {k: round(100 * max(0.0, min(1.0, v)), 2) for k, v in indicators.items()}
    n_high = sum(1 for i in issues if i["severity"] == "high")
    score = _final_score(indicators, n_high)

    order = {"high": 0, "medium": 1, "low": 2}
    issues.sort(key=lambda i: (order[i["severity"]], -i["count"]))
    preview = [["" if pd.isna(v) else str(v) for v in row]
               for row in df.head(5).itertuples(index=False, name=None)]

    return {
        "name": name,
        "n_rows": int(n_rows),
        "n_cols": int(n_cols),
        "total_cells": int(total_cells),
        "missing_cells": int(missing),
        "duplicate_rows": dup_rows,
        "indicators": indicators,
        "score": score,
        "columns": [{k: v for k, v in c.items() if k not in ("issues", "numeric_valid", "n_valid")} for c in columns],
        "issues": issues,
        "pii": _detect_pii(df),
        "preview": {"columns": list(df.columns), "rows": preview},
    }


def _final_score(indicators: dict, n_high: int) -> float:
    """Moyenne pondérée des indicateurs, moins 3 points par problème critique (maximum 15)."""
    base = sum(indicators[k] * w for k, w in WEIGHTS.items())
    return round(max(0.0, base - min(15, 3 * n_high)), 1)


def level_for(score: float) -> str:
    return "bon" if score >= 85 else "moyen" if score >= 65 else "insuffisant"


def analyze(data: bytes, filename: str) -> dict:
    """Point d'entrée : charge le fichier et produit le rapport complet."""
    tables, notes = load_tables(data, filename)
    results = [analyze_table(name, df) for name, df in tables.items()]

    weights = [max(t["total_cells"], 1) for t in results]
    wsum = sum(weights)
    indicators = {
        k: round(sum(t["indicators"][k] * w for t, w in zip(results, weights)) / wsum, 2)
        for k in WEIGHTS
    }
    all_issues = [dict(i, table=t["name"]) for t in results for i in t["issues"]]
    all_issues.sort(key=lambda i: ({"high": 0, "medium": 1, "low": 2}[i["severity"]], -i["count"]))
    n_high_total = sum(1 for i in all_issues if i["severity"] == "high")
    score = _final_score(indicators, n_high_total)
    pii = [dict(p, table=t["name"]) for t in results for p in t["pii"]]

    recommendations = []
    for issue in all_issues:
        text = RECOMMENDATIONS.get(issue["type"])
        if text and text not in recommendations:
            recommendations.append(text)
    if pii:
        recommendations.append(
            "Données personnelles détectées : minimiser, pseudonymiser ou chiffrer ces colonnes, restreindre "
            "les accès et documenter la base légale (RGPD / loi 09-08 ; PCI DSS pour les numéros de carte)."
        )
    if not recommendations:
        recommendations.append("Aucune anomalie majeure : poursuivre la surveillance régulière de la qualité.")

    return _json_safe({
        "source": filename,
        "notes": notes,
        "summary": {
            "n_tables": len(results),
            "n_rows": sum(t["n_rows"] for t in results),
            "n_cols": sum(t["n_cols"] for t in results),
            "indicators": indicators,
            "score": score,
            "level": level_for(score),
            "n_issues": len(all_issues),
            "n_high": n_high_total,
            "penalty": min(15, 3 * n_high_total),
            "n_pii_columns": len(pii),
            "duplicate_rows": sum(t["duplicate_rows"] for t in results),
            "missing_cells": sum(t["missing_cells"] for t in results),
        },
        "tables": results,
        "issues": all_issues[:100],
        "governance": {"pii_columns": pii},
        "recommendations": recommendations,
    })


def _json_safe(obj):
    """Remplace NaN / infini par None pour garantir un JSON valide."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
        return None
    return obj


def to_prompt_context(report: dict, max_issues: int = 8) -> str:
    """Version condensée du rapport, injectée dans le prompt système du chat."""
    s = report["summary"]
    ind = s["indicators"]
    lines = [
        f"Fichier : {report['source']} — {s['n_tables']} table(s), {s['n_rows']} lignes.",
        f"Score global : {s['score']}/100 ({s['level']}). Complétude {ind['completeness']} %, unicité "
        f"{ind['uniqueness']} %, cohérence {ind['consistency']} %, validité {ind['validity']} %, "
        f"absence d'anomalies {ind['anomalies']} %.",
    ]
    for table in report["tables"][:5]:
        cols = ", ".join(f"{c['name']} ({c['type']})" for c in table["columns"][:25])
        lines.append(f"Table {table['name']} : {cols}")
    for issue in report["issues"][:max_issues]:
        where = f"{issue['table']}.{issue['column']}" if issue.get("column") else issue["table"]
        lines.append(f"- [{issue['severity']}] {where} : {issue['message']}")
    pii = report["governance"]["pii_columns"]
    if pii:
        lines.append("Colonnes sensibles : " + ", ".join(f"{p['table']}.{p['column']}" for p in pii[:10]))
    return "\n".join(lines)
