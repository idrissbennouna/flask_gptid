// =============================================================================
// sources.js — documents sources, extraits affichés pour chaque réponse,
//              vérification des citations dans le document d'origine
// =============================================================================
(() => {
    "use strict";
    const { $, $$ } = App;

    const listEl = $("#source-list");
    const fileInput = $("#source-file");
    const perSourceInput = $("#per-source");

    App.selectedDocs = new Set();
    App.selectedDocIds = () => Array.from(App.selectedDocs);
    App.perSource = () => Math.max(1, Math.min(5, parseInt(perSourceInput.value, 10) || 2));

    // ---------------------------------------------------------------------
    // Liste des documents (cases à cocher = sources consultées)
    // ---------------------------------------------------------------------
    const known = new Set();

    async function loadDocuments() {
        try {
            const { documents } = await (await App.api("/api/documents")).json();
            const ids = new Set(documents.map((d) => d.id));
            documents.forEach((d) => { if (!known.has(d.id)) { known.add(d.id); App.selectedDocs.add(d.id); } });
            Array.from(App.selectedDocs).forEach((id) => { if (!ids.has(id)) App.selectedDocs.delete(id); });

            listEl.innerHTML = "";
            documents.forEach((doc) => {
                const li = document.createElement("li");
                li.className = "source-item";

                const label = document.createElement("label");
                const checkbox = document.createElement("input");
                checkbox.type = "checkbox";
                checkbox.checked = App.selectedDocs.has(doc.id);
                checkbox.addEventListener("change", () =>
                    checkbox.checked ? App.selectedDocs.add(doc.id) : App.selectedDocs.delete(doc.id));
                const name = document.createElement("span");
                name.className = "name";
                name.textContent = doc.name;
                name.title = doc.name;
                const small = document.createElement("small");
                small.textContent = `${doc.n_chunks} p.`;
                label.append(checkbox, name, small);

                const del = document.createElement("button");
                del.type = "button";
                del.className = "icon-btn";
                del.title = "Supprimer ce document";
                del.textContent = "×";
                del.addEventListener("click", async () => {
                    if (!confirm(`Supprimer « ${doc.name} » ?`)) return;
                    try {
                        await App.api(`/api/documents/${doc.id}`, { method: "DELETE" });
                        await loadDocuments();
                    } catch (err) { App.toastError(err); }
                });
                li.append(label, del);
                listEl.append(li);
            });
        } catch (err) {
            console.error(err);
        }
    }

    fileInput.addEventListener("change", async () => {
        const file = fileInput.files[0];
        if (!file) return;
        const data = new FormData();
        data.append("file", file);
        try {
            App.toast(`Indexation de « ${file.name} »…`);
            await App.api("/api/documents", { method: "POST", body: data });
            await loadDocuments();
            App.toast("Document ajouté aux sources.", "success");
        } catch (err) {
            App.toastError(err);
        } finally {
            fileInput.value = "";
        }
    });

    // ---------------------------------------------------------------------
    // Vérification d'une citation : passage + contexte
    // ---------------------------------------------------------------------
    const dialog = $("#passage-dialog");

    async function openPassage(source) {
        try {
            const data = await (await App.api(`/api/documents/${source.document_id}/chunks/${source.position}`)).json();
            $("#passage-title").textContent =
                `${data.document.name} — lignes ${data.chunk.start_line} à ${data.chunk.end_line}`;
            const body = $("#passage-body");
            body.innerHTML = "";
            const add = (cls, text, label) => {
                if (!text) return;
                if (label) {
                    const lbl = document.createElement("div");
                    lbl.className = "lbl";
                    lbl.textContent = label;
                    body.append(lbl);
                }
                const div = document.createElement("div");
                div.className = cls;
                div.textContent = text;
                body.append(div);
            };
            add("ctx", data.previous && data.previous.content, "Contexte précédent");
            add("hit", data.chunk.content, `Passage cité [${source.label}]`);
            add("ctx", data.next && data.next.content, "Contexte suivant");
            dialog.showModal();
        } catch (err) {
            App.toastError(err);
        }
    }

    // ---------------------------------------------------------------------
    // Panneau de sources sous une réponse
    // ---------------------------------------------------------------------
    function clip(text, unit, amount) {
        if (unit === "tout") return text;
        const separator = unit === "lignes" ? "\n" : "\n\n";
        const parts = (unit === "lignes" ? text.split("\n") : text.split(/\n\s*\n/)).filter((p) => p.trim());
        const shown = parts.slice(0, amount).join(separator);
        return parts.length > amount ? `${shown} […]` : shown;
    }

    App.renderSources = (article, sources) => {
        const box = $(".sources", article);
        if (!box) return;
        if (!sources || !sources.length) {
            box.hidden = true;
            box.innerHTML = "";
            return;
        }

        const groups = new Map();
        sources.forEach((s) => {
            if (!groups.has(s.document_id)) groups.set(s.document_id, { name: s.document_name, items: [] });
            groups.get(s.document_id).items.push(s);
        });
        const maxPerDoc = Math.max(...Array.from(groups.values(), (g) => g.items.length));
        const state = { perDoc: maxPerDoc, unit: "lignes", amount: 8, hidden: new Set() };

        box.hidden = false;
        box.innerHTML = `
            <div class="sources-head">
                <strong>Sources (${groups.size})</strong>
                <label>Extraits par source
                    <input type="number" data-k="perDoc" min="1" max="${maxPerDoc}" value="${maxPerDoc}"></label>
                <label>Afficher
                    <input type="number" data-k="amount" min="1" max="100" value="${state.amount}">
                    <select data-k="unit">
                        <option value="lignes">lignes</option>
                        <option value="paragraphes">paragraphes</option>
                        <option value="tout">extrait entier</option>
                    </select>
                </label>
            </div>
            <div class="source-body"></div>`;
        const body = $(".source-body", box);

        const excerpt = (source) => {
            const div = document.createElement("div");
            div.className = "excerpt";
            div.dataset.label = source.label;

            const meta = document.createElement("div");
            meta.className = "excerpt-meta";
            const tag = document.createElement("b");
            tag.textContent = `[${source.label}]`;
            const where = document.createElement("span");
            where.textContent = `lignes ${source.start_line}–${source.end_line} · pertinence ${source.score}`;
            const open = document.createElement("button");
            open.type = "button";
            open.className = "btn btn-ghost btn-small";
            open.textContent = "Voir dans le document";
            open.addEventListener("click", () => openPassage(source));
            meta.append(tag, where, open);

            const text = document.createElement("p");
            text.className = "excerpt-text";
            text.textContent = clip(source.text, state.unit, state.amount);
            div.append(meta, text);
            return div;
        };

        const draw = () => {
            body.innerHTML = "";
            groups.forEach((group, docId) => {
                const wrap = document.createElement("div");
                wrap.className = "source-group";

                const label = document.createElement("label");
                const checkbox = document.createElement("input");
                checkbox.type = "checkbox";
                checkbox.checked = !state.hidden.has(docId);
                checkbox.addEventListener("change", () => {
                    checkbox.checked ? state.hidden.delete(docId) : state.hidden.add(docId);
                    draw();
                });
                const title = document.createElement("span");
                title.textContent = `${group.name} (${group.items.length} extrait${group.items.length > 1 ? "s" : ""})`;
                label.append(checkbox, title);
                wrap.append(label);

                if (checkbox.checked) group.items.slice(0, state.perDoc).forEach((s) => wrap.append(excerpt(s)));
                body.append(wrap);
            });
        };

        $$("[data-k]", box).forEach((control) => {
            control.addEventListener("input", () => {
                const key = control.dataset.k;
                state[key] = key === "unit" ? control.value : Math.max(1, parseInt(control.value, 10) || 1);
                if (key === "unit") $("[data-k='amount']", box).disabled = state.unit === "tout";
                draw();
            });
        });

        // Appelé quand l'utilisateur clique sur une citation [S1] dans la réponse
        article._focusSource = (label) => {
            const source = sources.find((s) => s.label === label);
            if (!source) return;
            state.hidden.delete(source.document_id);
            const index = groups.get(source.document_id).items.indexOf(source);
            if (index >= state.perDoc) {
                state.perDoc = index + 1;
                $("[data-k='perDoc']", box).value = state.perDoc;
            }
            draw();
            const target = $(`.excerpt[data-label="${label}"]`, box);
            if (target) {
                target.scrollIntoView({ behavior: "smooth", block: "center" });
                target.classList.add("flash");
                setTimeout(() => target.classList.remove("flash"), 1800);
            }
        };

        draw();
    };

    document.addEventListener("DOMContentLoaded", loadDocuments);
})();
