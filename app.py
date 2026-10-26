"""FlaskGPT — assistant IA multi-domaines avec SQLite, sources, gouvernance des données et exports.

Lancer :  python app.py   (puis ouvrir http://127.0.0.1:5000)
"""
import json
import logging
import os
import time

from flask import Flask, Response, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

import config
import data_quality as dq
import database as db
import export_service as export
import llm_service as llm
import sources_service as sources

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("flaskgpt")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_MB * 1024 * 1024
db.init_db()


# ----------------------------------------------------------------------------
# Utilitaires
# ----------------------------------------------------------------------------
def json_error(code: str, title: str, message: str, status: int, hint: str = "", **extra):
    return jsonify(error={"code": code, "title": title, "message": message, "hint": hint}, **extra), status


def ndjson(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


def get_conversation_or_404(cid: int):
    conv = db.get_conversation(cid)
    if not conv:
        return None, json_error("not_found", "Introuvable", "Cette conversation n'existe pas (ou a été supprimée).", 404)
    return conv, None


def download(data: bytes | str, mimetype: str, filename: str) -> Response:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return Response(data, mimetype=mimetype, headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Access-Control-Expose-Headers": "Content-Disposition",
    })


# ----------------------------------------------------------------------------
# Pages
# ----------------------------------------------------------------------------
@app.get("/")
def home():
    return render_template("index.html", domains=llm.public_domains())


# ----------------------------------------------------------------------------
# Conversations
# ----------------------------------------------------------------------------
@app.get("/api/conversations")
def list_conversations():
    return jsonify(conversations=db.list_conversations())


@app.post("/api/conversations")
def create_conversation():
    data = request.get_json(silent=True) or {}
    domain = data.get("domain") if data.get("domain") in llm.DOMAINS else "general"
    return jsonify(conversation=db.create_conversation(domain)), 201


@app.get("/api/conversations/<int:cid>")
def get_conversation(cid):
    conv, err = get_conversation_or_404(cid)
    if err:
        return err
    return jsonify(conversation=conv, messages=db.get_messages(cid))


@app.patch("/api/conversations/<int:cid>")
def patch_conversation(cid):
    conv, err = get_conversation_or_404(cid)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    fields = {}
    if "domain" in data:
        if data["domain"] not in llm.DOMAINS:
            return json_error("bad_request", "Domaine invalide", "Ce domaine n'existe pas.", 400)
        fields["domain"] = data["domain"]
    if "title" in data and isinstance(data["title"], str) and data["title"].strip():
        fields["title"] = data["title"].strip()[:120]
    if "dataset_id" in data:
        ds_id = data["dataset_id"]
        if ds_id is not None and not db.get_dataset(int(ds_id)):
            return json_error("not_found", "Introuvable", "Ce jeu de données n'existe pas.", 404)
        fields["dataset_id"] = ds_id
    db.update_conversation(cid, **fields)
    return jsonify(conversation=db.get_conversation(cid))


@app.delete("/api/conversations/<int:cid>")
def delete_conversation(cid):
    db.delete_conversation(cid)
    return jsonify(ok=True)


# ----------------------------------------------------------------------------
# Chat (réponse streamée en NDJSON)
# ----------------------------------------------------------------------------
@app.post("/api/chat")
def chat():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return json_error("bad_request", "Requête invalide", "Le corps de la requête doit être un JSON valide.", 400)

    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return json_error("bad_request", "Message vide", "Veuillez saisir une question.", 400)
    prompt = prompt.strip()
    if len(prompt) > config.MAX_PROMPT_CHARS:
        return json_error("prompt_too_long", "Message trop long",
                          f"Votre message dépasse {config.MAX_PROMPT_CHARS} caractères.", 400)

    requested_domain = data.get("domain") if data.get("domain") in llm.DOMAINS else None
    conv_id = data.get("conversation_id")
    if conv_id is None:
        conv = db.create_conversation(requested_domain or "general")
    else:
        conv = db.get_conversation(int(conv_id))
        if not conv:
            return json_error("not_found", "Conversation introuvable",
                              "Cette conversation n'existe plus. Démarrez-en une nouvelle.", 404)
    conv_id = conv["id"]
    domain = requested_domain or conv["domain"]

    updates = {}
    if domain != conv["domain"]:
        updates["domain"] = domain
    if "dataset_id" in data:
        ds_id = data["dataset_id"]
        updates["dataset_id"] = ds_id if ds_id and db.get_dataset(int(ds_id)) else None
    if conv["title"] == db.DEFAULT_TITLE:
        updates["title"] = prompt[:60] + ("…" if len(prompt) > 60 else "")
    if updates:
        db.update_conversation(conv_id, **updates)
    conv = db.get_conversation(conv_id)

    try:
        source_ids = [int(i) for i in data.get("source_ids", [])]
        per_source = max(1, min(5, int(data.get("per_source", 2))))
    except (TypeError, ValueError):
        return json_error("bad_request", "Paramètres invalides", "Les paramètres de sources sont invalides.", 400)

    user_msg_id = db.add_message(conv_id, "user", prompt, domain=domain)

    passages = sources.retrieve(prompt, source_ids, per_source) if source_ids else []
    dataset_context = ""
    if conv["dataset_id"]:
        dataset = db.get_dataset(conv["dataset_id"])
        if dataset:
            dataset_context = dq.to_prompt_context(dataset["report"])

    history = db.get_messages(conv_id)[-config.MAX_HISTORY_MESSAGES:]
    while history and history[0]["role"] != "user":
        history.pop(0)
    llm_messages = [{"role": "system", "content": llm.build_system_prompt(domain, passages, dataset_context)}]
    llm_messages += [{"role": m["role"], "content": m["content"]} for m in history]

    started = time.perf_counter()
    try:
        stream = llm.open_stream(llm_messages)
    except llm.LLMError as exc:
        return jsonify(error=exc.as_dict(), conversation_id=conv_id, user_message_id=user_msg_id), exc.status

    def generate():
        yield ndjson({
            "type": "start", "conversation_id": conv_id, "user_message_id": user_msg_id,
            "title": conv["title"], "dataset_id": conv["dataset_id"],
            "sources": passages,
        })
        parts, usage, first_token_at = [], None, None
        try:
            for kind, value in llm.iter_stream(stream):
                if kind == "token":
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
                    parts.append(value)
                    yield ndjson({"type": "token", "text": value})
                else:
                    usage = value
        except llm.LLMError as exc:
            yield ndjson({"type": "error", "error": exc.as_dict()})
            return
        except GeneratorExit:
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass
            raise
        except Exception:  # noqa: BLE001
            log.exception("Erreur pendant le streaming")
            yield ndjson({"type": "error", "error": llm.translate_error(RuntimeError()).as_dict()})
            return

        text = "".join(parts)
        if not text.strip():
            yield ndjson({"type": "error", "error": {
                "code": "empty_response", "title": "Réponse vide",
                "message": "Le modèle n'a renvoyé aucun texte.", "hint": "Reformulez votre question et réessayez."}})
            return

        end = time.perf_counter()
        estimated = 0
        if not usage:
            estimated = 1
            prompt_tokens = sum(llm.estimate_tokens(m["content"]) for m in llm_messages)
            completion_tokens = llm.estimate_tokens(text)
            usage = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                     "total_tokens": prompt_tokens + completion_tokens}
        msg_id = db.add_message(
            conv_id, "assistant", text, domain=domain, model=config.LLM_MODEL,
            response_time_ms=int((end - started) * 1000),
            first_token_ms=int(((first_token_at or end) - started) * 1000),
            prompt_tokens=usage["prompt_tokens"], completion_tokens=usage["completion_tokens"],
            total_tokens=usage["total_tokens"], tokens_estimated=estimated, sources=passages,
        )
        yield ndjson({"type": "done", "message": db.get_message(msg_id)})

    return Response(generate(), mimetype="application/x-ndjson",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ----------------------------------------------------------------------------
# Évaluation des réponses
# ----------------------------------------------------------------------------
@app.post("/api/messages/<int:mid>/rating")
def rate_message(mid):
    message = db.get_message(mid)
    if not message or message["role"] != "assistant":
        return json_error("not_found", "Introuvable", "Ce message n'existe pas.", 404)
    data = request.get_json(silent=True) or {}
    rating = data.get("rating")
    if rating is not None:
        if not isinstance(rating, int) or isinstance(rating, bool) or not 1 <= rating <= 5:
            return json_error("bad_request", "Note invalide", "La note doit être comprise entre 1 et 5.", 400)
    db.set_rating(mid, rating)
    return jsonify(id=mid, rating=rating)


# ----------------------------------------------------------------------------
# Exports et résumé PDF
# ----------------------------------------------------------------------------
@app.get("/api/conversations/<int:cid>/export")
def export_conversation(cid):
    conv, err = get_conversation_or_404(cid)
    if err:
        return err
    messages = db.get_messages(cid)
    fmt = request.args.get("format", "txt").lower()
    if fmt == "txt":
        return download(export.to_txt(conv, messages), "text/plain; charset=utf-8",
                        export.safe_filename(conv["title"], "txt"))
    if fmt == "json":
        return download(export.to_json(conv, messages), "application/json",
                        export.safe_filename(conv["title"], "json"))
    if fmt == "pdf":
        return download(export.to_pdf(conv, messages), "application/pdf",
                        export.safe_filename(conv["title"], "pdf"))
    return json_error("bad_request", "Format inconnu", "Formats disponibles : txt, json, pdf.", 400)


@app.get("/api/conversations/<int:cid>/summary")
def summary_pdf(cid):
    conv, err = get_conversation_or_404(cid)
    if err:
        return err
    messages = db.get_messages(cid)
    if not any(m["role"] == "assistant" for m in messages):
        return json_error("empty_conversation", "Rien à résumer",
                          "La conversation ne contient pas encore de réponse de l'assistant.", 400)
    try:
        text, _ = llm.summarize(conv, messages)
    except llm.LLMError as exc:
        return jsonify(error=exc.as_dict()), exc.status
    pdf = export.summary_pdf(conv, messages, text or "Résumé indisponible.", db.conversation_stats(cid))
    return download(pdf, "application/pdf", export.safe_filename("resume_" + conv["title"], "pdf"))


# ----------------------------------------------------------------------------
# Statistiques de performance
# ----------------------------------------------------------------------------
@app.get("/api/stats")
def stats():
    return jsonify(stats=db.global_stats())


@app.get("/api/conversations/<int:cid>/stats")
def conversation_stats(cid):
    _, err = get_conversation_or_404(cid)
    if err:
        return err
    return jsonify(stats=db.conversation_stats(cid))


# ----------------------------------------------------------------------------
# Sources documentaires
# ----------------------------------------------------------------------------
@app.get("/api/documents")
def list_documents():
    return jsonify(documents=db.list_documents())


@app.post("/api/documents")
def upload_document():
    file = request.files.get("file")
    if not file or not file.filename:
        return json_error("bad_request", "Aucun fichier", "Sélectionnez un fichier à importer.", 400)
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in sources.ALLOWED_EXTENSIONS:
        return json_error("unsupported_format", "Format non pris en charge",
                          "Formats acceptés : " + ", ".join(sorted(sources.ALLOWED_EXTENSIONS)) + ".", 415)
    data = file.read()
    try:
        text = sources.extract_text(data, ext)
    except ValueError as exc:
        return json_error("unreadable_document", "Document illisible", str(exc), 422)
    chunks = sources.chunk_text(text)
    if not chunks:
        return json_error("empty_document", "Document vide", "Aucun texte exploitable dans ce fichier.", 422)
    doc_id = db.add_document(os.path.basename(file.filename), ext, len(data), chunks)
    return jsonify(document=db.get_document(doc_id)), 201


@app.delete("/api/documents/<int:doc_id>")
def delete_document(doc_id):
    db.delete_document(doc_id)
    return jsonify(ok=True)


@app.get("/api/documents/<int:doc_id>/chunks/<int:position>")
def get_passage(doc_id, position):
    """Retourne un passage et ses voisins, pour vérifier une citation dans son contexte."""
    document = db.get_document(doc_id)
    chunk = db.get_chunk(doc_id, position)
    if not document or not chunk:
        return json_error("not_found", "Passage introuvable",
                          "Ce document ou ce passage a été supprimé depuis la réponse.", 404)
    return jsonify(
        document=document, chunk=chunk,
        previous=db.get_chunk(doc_id, position - 1), next=db.get_chunk(doc_id, position + 1),
    )


# ----------------------------------------------------------------------------
# Analyse et gouvernance des données
# ----------------------------------------------------------------------------
@app.get("/api/datasets")
def list_datasets():
    return jsonify(datasets=db.list_datasets())


@app.post("/api/datasets")
def upload_dataset():
    file = request.files.get("file")
    if not file or not file.filename:
        return json_error("bad_request", "Aucun fichier", "Sélectionnez une base de données à analyser.", 400)
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in dq.ALLOWED_EXTENSIONS:
        return json_error("unsupported_format", "Format non pris en charge",
                          "Formats acceptés : CSV, Excel (.xlsx), JSON, SQLite (.db, .sqlite).", 415)
    try:
        report = dq.analyze(file.read(), os.path.basename(file.filename))
    except ValueError as exc:
        return json_error("unreadable_dataset", "Fichier illisible", str(exc), 422)
    ds_id = db.add_dataset(os.path.basename(file.filename), report)
    return jsonify(dataset=db.get_dataset(ds_id)), 201


@app.get("/api/datasets/<int:ds_id>")
def get_dataset(ds_id):
    dataset = db.get_dataset(ds_id)
    if not dataset:
        return json_error("not_found", "Introuvable", "Ce jeu de données n'existe pas.", 404)
    return jsonify(dataset=dataset)


@app.delete("/api/datasets/<int:ds_id>")
def delete_dataset(ds_id):
    db.delete_dataset(ds_id)
    return jsonify(ok=True)


# ----------------------------------------------------------------------------
# Gestion globale des erreurs (réponses JSON pour /api)
# ----------------------------------------------------------------------------
@app.errorhandler(413)
def too_large(_):
    return json_error("file_too_large", "Fichier trop volumineux",
                      f"La taille maximale autorisée est de {config.MAX_UPLOAD_MB} Mo.", 413)


@app.errorhandler(Exception)
def unhandled(exc):
    if isinstance(exc, HTTPException):
        if request.path.startswith("/api/"):
            return json_error("http_error", exc.name, exc.description or exc.name, exc.code or 500)
        return exc
    log.exception("Erreur non gérée")
    if request.path.startswith("/api/"):
        return json_error("server_error", "Erreur serveur",
                          "Une erreur interne est survenue.", 500, "Consultez la console du serveur.")
    return "Erreur interne", 500


if __name__ == "__main__":
    app.run(debug=True, threaded=True)
