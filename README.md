# FlaskGPT

Application web Flask qui associe un assistant conversationnel à des outils d'analyse et de gouvernance des données. Elle permet de poser des questions par domaine, d'utiliser des documents comme sources, d'analyser des jeux de données et de suivre les performances des conversations.

## Fonctionnalités

- **Assistant conversationnel multi-domaines** : général, informatique, finance, analyse de données, gouvernance des données, juridique et conformité, enseignement et pédagogie.
- **Réponses en streaming** et historique des conversations stocké dans SQLite.
- **Documents sources** : import de fichiers TXT, Markdown, CSV, JSON, PDF et DOCX. Le contenu est extrait, découpé en passages et recherché pour fournir des extraits pertinents avec leurs références.
- **Analyse de qualité des données** : import de fichiers CSV, TSV, Excel (`.xlsx`, `.xlsm`), JSON et SQLite (`.db`, `.sqlite`, `.sqlite3`). Le rapport évalue notamment la complétude, l'unicité, la cohérence, la validité, les anomalies et les données personnelles détectées.
- **Performances** : statistiques de conversation, temps de réponse et évaluation des réponses.
- **Exports** des conversations en TXT, JSON ou PDF, ainsi qu'export PDF d'un résumé.

## Prérequis

- Python 3.10 ou plus récent
- Une clé API auprès d'un fournisseur compatible avec l'API OpenAI (Groq est configuré par défaut) pour utiliser les fonctions de chat

L'analyse de fichiers et les autres fonctions locales ne nécessitent pas de clé API.

## Installation et lancement

### Windows (PowerShell)

Depuis le dossier du projet :

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Ouvrez ensuite <http://127.0.0.1:5000> dans votre navigateur. Arrêtez le serveur avec `Ctrl+C`.

### macOS ou Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

## Configuration du fournisseur IA

Créez un fichier `.env` à la racine du projet. Exemple pour Groq :

```dotenv
GROQ_API_KEY=remplacer_par_votre_cle
```

La configuration par défaut utilise `https://api.groq.com/openai/v1` et le modèle `openai/gpt-oss-20b`. Pour un autre fournisseur compatible avec l'API OpenAI, renseignez les variables suivantes :

```dotenv
OPENAI_API_KEY=remplacer_par_votre_cle
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=nom-du-modele
```

`GROQ_API_KEY` est prioritaire si les deux clés sont définies. Les valeurs par défaut et options sont centralisées dans [`config.py`](config.py). Ne publiez jamais votre fichier `.env` ni une clé API. Le fichier est exclu du dépôt par `.gitignore`.

## Utilisation

1. Choisissez un domaine dans l'application et démarrez une conversation.
2. Facultativement, ajoutez des documents dans le panneau des sources, puis sélectionnez ceux à consulter pour une réponse.
3. Pour analyser un jeu de données, ouvrez l'onglet **Gouvernance des données**, importez un fichier pris en charge et cliquez sur **Analyser**.
4. Consultez les statistiques dans l'onglet **Performances** et exportez une conversation depuis son menu d'export.

Les fichiers importés sont soumis à une limite de taille configurable de **20 Mo** par défaut. L'analyse des données traite jusqu'à **100 000 lignes par table** et **25 tables** au maximum. Ces limites peuvent tronquer les données analysées.

## Configuration

| Variable | Valeur par défaut | Description |
|---|---|---|
| `GROQ_API_KEY` | — | Clé API Groq, prioritaire sur `OPENAI_API_KEY` |
| `OPENAI_API_KEY` | — | Clé API d'un fournisseur compatible |
| `LLM_BASE_URL` | `https://api.groq.com/openai/v1` | URL de base de l'API du modèle |
| `LLM_MODEL` | `openai/gpt-oss-20b` | Identifiant du modèle |
| `LLM_TIMEOUT` | `60` | Délai d'attente des appels au modèle, en secondes |
| `MAX_HISTORY_MESSAGES` | `20` | Nombre maximal de messages d'historique configuré |
| `MAX_UPLOAD_MB` | `20` | Taille maximale d'un téléversement, en Mo |
| `DATABASE_PATH` | `instance/flaskgpt.db` | Chemin de la base de données SQLite |

## Structure du projet

```text
.
├── app.py                 # Application Flask et routes HTTP/API
├── config.py              # Configuration et variables d'environnement
├── database.py            # Persistance SQLite
├── data_quality.py        # Chargement et analyse des jeux de données
├── sources_service.py     # Extraction et recherche dans les documents
├── llm_service.py         # Domaines, prompts et appels au modèle
├── export_service.py      # Exports TXT, JSON et PDF
├── templates/
│   └── index.html         # Interface web
└── static/
    ├── css/               # Styles
    └── js/                # Scripts de l'interface
```

La base de données est créée automatiquement au démarrage dans `instance/` par défaut. Ce dossier contient des données locales et n'est pas destiné à être versionné.

## API

L'application expose notamment les routes suivantes :

| Méthode | Route | Description |
|---|---|---|
| `GET` | `/` | Interface web |
| `GET`, `POST` | `/api/conversations` | Lister ou créer des conversations |
| `POST` | `/api/chat` | Envoyer un message et recevoir une réponse |
| `GET`, `POST` | `/api/documents` | Lister ou ajouter des documents sources |
| `GET`, `POST` | `/api/datasets` | Lister ou analyser des jeux de données |
| `GET` | `/api/stats` | Statistiques globales |

L'interface web utilise également d'autres routes pour consulter, noter, supprimer et exporter les éléments. Les routes et leurs formats de requête sont définis dans [`app.py`](app.py).

## Confidentialité et sécurité

- Gardez les clés API dans `.env` et vérifiez que ce fichier n'est pas inclus dans les commits.
- Les messages et les extraits de documents fournis au chat sont envoyés au fournisseur IA configuré. N'envoyez pas de données confidentielles ou personnelles sans autorisation et sans vérifier les règles applicables.
- L'analyse locale de la qualité des données ne requiert pas d'appel au modèle.
- L'application démarre en mode développement avec le serveur intégré de Flask ; ne l'utilisez pas tel quel comme serveur de production exposé à Internet.

## Dépendances

Les bibliothèques nécessaires sont listées dans [`requirements.txt`](requirements.txt) : Flask, OpenAI Python SDK, pandas, python-dotenv et bibliothèques de lecture/export de documents.
