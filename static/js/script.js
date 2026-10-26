// =============================================================================
// script.js — noyau de l'interface : conversation, historique, exports, erreurs
// Les modules sources.js (documents) et panels.js (données, performances)
// s'appuient sur l'objet global `App` défini ici.
// =============================================================================
(() => {
    "use strict";

    // ---------------------------------------------------------------------
    // Utilitaires partagés
    // ---------------------------------------------------------------------
    class ApiError extends Error {
        constructor(info, status = 0, payload = null) {
            super((info && info.message) || "Erreur");
            this.info = info || {};
            this.status = status;
            this.payload = payload;
        }
    }

    const $ = (selector, root = document) => root.querySelector(selector);
    const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

    const App = (window.App = {
        conversationId: null,
        datasetId: null,
        datasetName: null,
        busy: false,
        ApiError, $, $$,
    });

    const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
    App.esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ESCAPES[c]);
    App.fmtMs = (ms) => (ms == null ? "—" : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`);
    App.fmtInt = (n) => (n == null ? "—" : new Intl.NumberFormat("fr-FR").format(Math.round(n)));

    // Code couleur de l'évaluation : 4-5 satisfaisante, 3 moyenne, 1-2 insuffisante
    App.qualityClass = (rating) => (!rating ? "q-none" : rating >= 4 ? "q-good" : rating >= 3 ? "q-mid" : "q-bad");
    const QUALITY_LABEL = { "q-good": "Satisfaisante", "q-mid": "Moyenne", "q-bad": "Insuffisante", "q-none": "Non évaluée" };

    App.toast = (message, type = "info", title = "") => {
        const box = document.createElement("div");
        box.className = `toast ${type}`;
        if (title) {
            const strong = document.createElement("b");
            strong.textContent = title;
            box.append(strong);
        }
        box.append(document.createTextNode(message));
        $("#toasts").append(box);
        setTimeout(() => box.remove(), type === "error" ? 8000 : 3500);
    };

    App.toastError = (err) => {
        const info = err && err.info ? err.info : { title: "Erreur", message: err && err.message };
        const text = [info.message, info.hint].filter(Boolean).join(" ");
        App.toast(text || "Une erreur est survenue.", "error", info.title || "Erreur");
    };

    // Appel HTTP avec erreurs lisibles (réseau coupé, erreur serveur, etc.)
    App.api = async (url, options = {}) => {
        let response;
        try {
            response = await fetch(url, options);
        } catch (networkError) {
            throw new ApiError({
                code: "network",
                title: "Serveur injoignable",
                message: "Impossible de contacter le serveur. Vérifiez votre connexion et que l'application Flask est bien lancée.",
            });
        }
        if (!response.ok) {
            let payload = null;
            try { payload = await response.json(); } catch (_) { /* corps non JSON */ }
            throw new ApiError(
                (payload && payload.error) || { code: `http_${response.status}`, title: "Erreur serveur", message: `Le serveur a répondu avec l'erreur ${response.status}.` },
                response.status, payload,
            );
        }
        return response;
    };

    App.postJson = (url, body, method = "POST") =>
        App.api(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

    // ---------------------------------------------------------------------
    // Rendu Markdown sécurisé + citations cliquables [S1]
    // ---------------------------------------------------------------------
    let converter = null;
    function renderMarkdown(text, sources) {
        converter ??= new showdown.Converter({ tables: true, strikethrough: true, simplifiedAutoLink: true });
        let html = converter.makeHtml(text);
        if (sources && sources.length) {
            const labels = new Set(sources.map((s) => s.label));
            html = html.replace(/\[(S\d+)\]/g, (match, label) =>
                labels.has(label) ? `<a href="#" class="cite" data-label="${label}" title="Voir la source">[${label}]</a>` : match);
        }
        return DOMPurify.sanitize(html);
    }

    // ---------------------------------------------------------------------
    // Messages
    // ---------------------------------------------------------------------
    const output = $("#gpt-output");
    const emptyState = $("#empty-state");

    const scrollToBottom = () => { output.scrollTop = output.scrollHeight; };

    function createMessage(role) {
        const el = $("#message-template").content.firstElementChild.cloneNode(true);
        el.classList.add(role === "user" ? "msg-user" : "msg-assistant");
        $(".msg-role", el).textContent = role === "user" ? "Vous" : "Assistant";
        if (role === "user") {
            $(".sources", el).remove();
            $(".msg-footer", el).remove();
        } else {
            el.classList.add("q-none");
        }
        emptyState.hidden = true;
        output.append(el);
        scrollToBottom();
        return el;
    }

    function clearMessages() {
        $$(".msg", output).forEach((el) => el.remove());
        emptyState.hidden = false;
    }

    function applyRating(el, rating) {
        el.dataset.rating = rating || "";
        const cls = App.qualityClass(rating);
        el.classList.remove("q-good", "q-mid", "q-bad", "q-none");
        el.classList.add(cls);
        $$(".star", el).forEach((star, index) => star.classList.toggle("is-on", index < (rating || 0)));
        $(".rating-label", el).textContent = QUALITY_LABEL[cls];
    }

    async function rate(el, messageId, value) {
        const current = Number(el.dataset.rating) || 0;
        const next = current === value ? null : value; // recliquer sur la même note l'efface
        try {
            await App.postJson(`/api/messages/${messageId}/rating`, { rating: next });
            applyRating(el, next);
        } catch (err) {
            App.toastError(err);
        }
    }

    function buildRating(el, messageId, rating) {
        const box = $(".rating", el);
        box.innerHTML = "";
        for (let i = 1; i <= 5; i++) {
            const star = document.createElement("button");
            star.type = "button";
            star.className = "star";
            star.textContent = "★";
            star.title = `${i}/5`;
            star.setAttribute("aria-label", `Noter ${i} sur 5`);
            star.addEventListener("click", () => rate(el, messageId, i));
            box.append(star);
        }
        applyRating(el, rating);
    }

    function setMetrics(el, m) {
        const approx = m.tokens_estimated ? "≈ " : "";
        const node = $(".metrics", el);
        node.textContent =
            `${App.fmtMs(m.response_time_ms)} · ${approx}${App.fmtInt(m.total_tokens)} tokens ` +
            `(${App.fmtInt(m.prompt_tokens)} + ${App.fmtInt(m.completion_tokens)})`;
        node.title = "Temps de réponse · tokens au total (requête + réponse)" + (m.tokens_estimated ? " — estimation" : "");
    }

    function finalizeAssistant(el, message) {
        const body = $(".message", el);
        body.classList.remove("typing");
        body.innerHTML = renderMarkdown(message.content, message.sources);
        if (window.hljs) $$("pre code", body).forEach((block) => hljs.highlightElement(block));
        el.dataset.id = message.id;
        $(".msg-footer", el).hidden = false;
        setMetrics(el, message);
        buildRating(el, message.id, message.rating);
        if (App.renderSources) App.renderSources(el, message.sources);
    }

    function showError(el, err) {
        const info = (err && err.info && err.info.title) ? err.info : {
            title: "Connexion interrompue",
            message: "La connexion avec le serveur a été interrompue pendant la réponse.",
            hint: "Réessayez dans un instant.",
        };
        const body = $(".message", el);
        body.classList.remove("typing");
        if (!body.textContent.trim()) body.hidden = true;
        el.classList.add("msg-error");
        $(".msg-role", el).textContent = "Erreur";

        const box = document.createElement("div");
        const title = document.createElement("div");
        title.className = "error-title";
        title.textContent = info.title;
        const message = document.createElement("div");
        message.textContent = info.message;
        box.append(title, message);
        if (info.hint) {
            const hint = document.createElement("div");
            hint.className = "error-hint";
            hint.textContent = info.hint;
            box.append(hint);
        }
        el.insertBefore(box, $(".sources", el));
        scrollToBottom();
    }

    // ---------------------------------------------------------------------
    // Lecture du flux NDJSON renvoyé par /api/chat
    // ---------------------------------------------------------------------
    async function readNdjson(response, onEvent) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";
        try {
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                let newline;
                while ((newline = buffer.indexOf("\n")) >= 0) {
                    const line = buffer.slice(0, newline).trim();
                    buffer = buffer.slice(newline + 1);
                    if (line) onEvent(JSON.parse(line));
                }
            }
            buffer += decoder.decode();
            if (buffer.trim()) onEvent(JSON.parse(buffer));
        } finally {
            reader.releaseLock();
        }
    }

    // ---------------------------------------------------------------------
    // Envoi d'un message
    // ---------------------------------------------------------------------
    const form = $("#prompt-form");
    const promptInput = $("#prompt");
    const sendButton = $("button[type='submit']", form);
    const domainSelect = $("#domain-select");

    function setBusy(value) {
        App.busy = value;
        sendButton.disabled = value;
    }

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const prompt = promptInput.value.trim();
        if (!prompt || App.busy) return;

        promptInput.value = "";
        setBusy(true);
        $(".message", createMessage("user")).textContent = prompt;

        const assistantEl = createMessage("assistant");
        const body = $(".message", assistantEl);
        body.classList.add("typing");

        let text = "";
        let sources = [];
        try {
            const response = await App.postJson("/api/chat", {
                prompt,
                conversation_id: App.conversationId,
                domain: domainSelect.value,
                dataset_id: App.datasetId,
                source_ids: App.selectedDocIds ? App.selectedDocIds() : [],
                per_source: App.perSource ? App.perSource() : 2,
            });
            await readNdjson(response, (ev) => {
                if (ev.type === "start") {
                    App.conversationId = ev.conversation_id;
                    sources = ev.sources || [];
                } else if (ev.type === "token") {
                    text += ev.text;
                    body.innerHTML = renderMarkdown(text, sources);
                    scrollToBottom();
                } else if (ev.type === "done") {
                    finalizeAssistant(assistantEl, ev.message);
                    scrollToBottom();
                } else if (ev.type === "error") {
                    throw new ApiError(ev.error);
                }
            });
        } catch (err) {
            // Le message utilisateur est déjà enregistré : on garde l'identifiant de conversation
            if (err.payload && err.payload.conversation_id) App.conversationId = err.payload.conversation_id;
            showError(assistantEl, err);
            if (!(err instanceof ApiError)) console.error(err);
        } finally {
            setBusy(false);
            promptInput.focus();
            App.refreshConversations();
        }
    });

    // Clic sur une citation [S1] : met l'extrait en évidence
    output.addEventListener("click", (event) => {
        const link = event.target.closest("a.cite");
        if (!link) return;
        event.preventDefault();
        const article = link.closest(".msg");
        if (article && article._focusSource) article._focusSource(link.dataset.label);
    });

    // ---------------------------------------------------------------------
    // Historique des conversations
    // ---------------------------------------------------------------------
    const listEl = $("#conversation-list");
    const domainLabels = Object.fromEntries($$("#domain-select option").map((o) => [o.value, o.textContent]));
    App.domainLabel = (key) => domainLabels[key] || key || "Général";

    App.refreshConversations = async () => {
        try {
            const { conversations } = await (await App.api("/api/conversations")).json();
            listEl.innerHTML = "";
            if (!conversations.length) {
                const empty = document.createElement("li");
                empty.className = "hint";
                empty.textContent = "Aucune conversation enregistrée.";
                listEl.append(empty);
                return;
            }
            conversations.forEach((conv) => {
                const li = document.createElement("li");
                li.className = "conv-item" + (conv.id === App.conversationId ? " is-active" : "");
                const info = document.createElement("div");
                info.className = "conv-title";
                info.textContent = conv.title;
                const sub = document.createElement("small");
                sub.className = "conv-domain";
                sub.textContent = `${App.domainLabel(conv.domain)} · ${conv.n_messages} msg`;
                info.append(sub);
                const del = document.createElement("button");
                del.type = "button";
                del.className = "icon-btn";
                del.title = "Supprimer";
                del.textContent = "×";
                del.addEventListener("click", (e) => { e.stopPropagation(); removeConversation(conv.id); });
                li.append(info, del);
                li.addEventListener("click", () => loadConversation(conv.id));
                listEl.append(li);
            });
        } catch (err) {
            console.error(err);
        }
    };

    function renderStored(message) {
        if (message.role === "user") {
            $(".message", createMessage("user")).textContent = message.content;
        } else {
            finalizeAssistant(createMessage("assistant"), message);
        }
    }

    async function loadConversation(id) {
        if (App.busy) return;
        try {
            const { conversation, messages } = await (await App.api(`/api/conversations/${id}`)).json();
            App.conversationId = conversation.id;
            domainSelect.value = conversation.domain;
            if (App.setDataset) App.setDataset(conversation.dataset_id, conversation.dataset_name);
            clearMessages();
            messages.forEach(renderStored);
            App.showTab("chat");
            scrollToBottom();
            App.refreshConversations();
        } catch (err) {
            App.toastError(err);
            App.refreshConversations();
        }
    }

    function newConversation() {
        if (App.busy) return;
        App.conversationId = null;
        if (App.setDataset) App.setDataset(null, null);
        clearMessages();
        App.showTab("chat");
        App.refreshConversations();
        promptInput.focus();
    }

    async function removeConversation(id) {
        if (!confirm("Supprimer définitivement cette conversation ?")) return;
        try {
            await App.api(`/api/conversations/${id}`, { method: "DELETE" });
            if (id === App.conversationId) newConversation();
            else App.refreshConversations();
        } catch (err) {
            App.toastError(err);
        }
    }

    $("#btn-new").addEventListener("click", newConversation);

    // 1. Effacer la conversation en cours et en démarrer une nouvelle
    $("#btn-clear").addEventListener("click", async () => {
        if (App.busy) return;
        if (App.conversationId) {
            if (!confirm("Effacer cette conversation et en démarrer une nouvelle ?")) return;
            try {
                await App.api(`/api/conversations/${App.conversationId}`, { method: "DELETE" });
            } catch (err) {
                App.toastError(err);
                return;
            }
        }
        newConversation();
        App.toast("Conversation effacée. Nouvelle conversation prête.", "success");
    });

    // 2. Domaine de la conversation
    domainSelect.addEventListener("change", async () => {
        if (!App.conversationId) return;
        try {
            await App.postJson(`/api/conversations/${App.conversationId}`, { domain: domainSelect.value }, "PATCH");
            App.refreshConversations();
        } catch (err) {
            App.toastError(err);
        }
    });

    // ---------------------------------------------------------------------
    // Exports (TXT, JSON, PDF) et résumé PDF
    // ---------------------------------------------------------------------
    async function downloadFile(url, fallbackName) {
        const response = await App.api(url);
        const blob = await response.blob();
        const match = /filename="?([^";]+)"?/i.exec(response.headers.get("Content-Disposition") || "");
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = match ? match[1] : fallbackName;
        document.body.append(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(link.href), 3000);
    }

    const exportMenu = $("#export-menu");
    $$("[data-export]").forEach((button) => {
        button.addEventListener("click", async () => {
            exportMenu.open = false;
            if (!App.conversationId) return App.toast("Aucune conversation à exporter pour le moment.", "info");
            const format = button.dataset.export;
            try {
                await downloadFile(`/api/conversations/${App.conversationId}/export?format=${format}`, `conversation.${format}`);
            } catch (err) {
                App.toastError(err);
            }
        });
    });

    $("#btn-summary").addEventListener("click", async (event) => {
        const button = event.currentTarget;
        exportMenu.open = false;
        if (!App.conversationId) return App.toast("Aucune conversation à résumer pour le moment.", "info");
        button.disabled = true;
        App.toast("Génération du résumé en cours…");
        try {
            await downloadFile(`/api/conversations/${App.conversationId}/summary`, "resume.pdf");
            App.toast("Résumé PDF téléchargé.", "success");
        } catch (err) {
            App.toastError(err);
        } finally {
            button.disabled = false;
        }
    });

    // ---------------------------------------------------------------------
    // Onglets
    // ---------------------------------------------------------------------
    App.showTab = (name) => {
        $$(".tab").forEach((tab) => tab.classList.toggle("is-active", tab.dataset.tab === name));
        $$(".tab-panel").forEach((panel) => { panel.hidden = panel.id !== `tab-${name}`; });
        document.dispatchEvent(new CustomEvent("tabchange", { detail: name }));
    };
    $$(".tab").forEach((tab) => tab.addEventListener("click", () => App.showTab(tab.dataset.tab)));

    document.addEventListener("click", (event) => {
        if (!exportMenu.contains(event.target)) exportMenu.open = false;
    });

    document.addEventListener("DOMContentLoaded", () => {
        App.refreshConversations();
        promptInput.focus();
    });
})();
