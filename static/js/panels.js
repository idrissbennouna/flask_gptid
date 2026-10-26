// =============================================================================
// panels.js — onglets « Gouvernance des données » et « Performances »
// =============================================================================
(() => {
    "use strict";
    const { $, $$, esc, fmtMs, fmtInt } = App;

    // =====================================================================
    // Gouvernance des données
    // =====================================================================
    const chip = $("#dataset-chip");
    const datasetList = $("#dataset-list");
    const reportBox = $("#dataset-report");

    const INDICATORS = {
        completeness: ["Complétude", "Part des cellules renseignées (hors valeurs vides ou « N/A »)."],
        uniqueness: ["Unicité", "Absence de lignes dupliquées et d'identifiants en double."],
        consistency: ["Cohérence", "Types, casse et formats homogènes dans chaque colonne."],
        validity: ["Validité", "Valeurs respectant les règles métier (e-mails, dates, âges, montants positifs…)."],
        anomalies: ["Absence d'anomalies", "Part des valeurs numériques non aberrantes (méthode IQR)."],
    };
    const SEVERITY = { high: "Critique", medium: "Moyen", low: "Faible" };

    // Jeu de données joint à la conversation en cours
    App.setDataset = (id, name) => {
        App.datasetId = id || null;
        App.datasetName = name || null;
        chip.innerHTML = "";
        if (!App.datasetId) {
            chip.hidden = true;
            return;
        }
        chip.hidden = false;
        chip.append(document.createTextNode(`Données jointes : ${App.datasetName || "#" + App.datasetId}`));
        const remove = document.createElement("button");
        remove.type = "button";
        remove.title = "Détacher ces données";
        remove.textContent = "×";
        remove.addEventListener("click", () => App.setDataset(null, null));
        chip.append(remove);
    };

    const tone = (value) => (value >= 90 ? "" : value >= 70 ? "mid" : "bad");

    async function loadDatasets() {
        try {
            const { datasets } = await (await App.api("/api/datasets")).json();
            datasetList.innerHTML = "";
            datasets.forEach((ds) => {
                const li = document.createElement("li");
                li.className = "dataset-item";
                li.innerHTML = `
                    <div class="grow"><b>${esc(ds.name)}</b>
                        <small>${ds.n_tables} table(s) · ${fmtInt(ds.n_rows)} lignes · score ${ds.score ?? "—"}/100</small></div>`;
                const actions = [
                    ["Voir le rapport", () => showReport(ds.id)],
                    ["Joindre au chat", () => { App.setDataset(ds.id, ds.name); App.toast("Données jointes : l'assistant connaît maintenant ce rapport.", "success"); }],
                ];
                actions.forEach(([text, handler]) => {
                    const button = document.createElement("button");
                    button.type = "button";
                    button.className = "btn btn-ghost btn-small";
                    button.textContent = text;
                    button.addEventListener("click", handler);
                    li.append(button);
                });
                const del = document.createElement("button");
                del.type = "button";
                del.className = "icon-btn";
                del.title = "Supprimer";
                del.textContent = "×";
                del.addEventListener("click", async () => {
                    if (!confirm(`Supprimer l'analyse « ${ds.name} » ?`)) return;
                    try {
                        await App.api(`/api/datasets/${ds.id}`, { method: "DELETE" });
                        if (App.datasetId === ds.id) App.setDataset(null, null);
                        reportBox.innerHTML = "";
                        loadDatasets();
                    } catch (err) { App.toastError(err); }
                });
                li.append(del);
                datasetList.append(li);
            });
        } catch (err) {
            console.error(err);
        }
    }

    async function showReport(id) {
        try {
            const { dataset } = await (await App.api(`/api/datasets/${id}`)).json();
            renderReport(dataset);
            reportBox.scrollIntoView({ behavior: "smooth" });
        } catch (err) {
            App.toastError(err);
        }
    }

    function bars(indicators) {
        return Object.entries(INDICATORS).map(([key, [label, help]]) => {
            const value = indicators[key];
            return `<div class="bar-row" title="${esc(help)}">
                <span>${label}</span>
                <div class="bar"><i class="${tone(value)}" style="width:${value}%"></i></div>
                <b>${value} %</b></div>`;
        }).join("");
    }

    function renderReport(dataset) {
        const r = dataset.report;
        const s = r.summary;
        const issues = r.issues.map((i) => `
            <li><span class="badge ${i.severity}">${SEVERITY[i.severity]}</span>
                <span><b>${esc(i.table)}${i.column ? "." + esc(i.column) : ""}</b> — ${esc(i.message)}</span></li>`).join("");
        const pii = r.governance.pii_columns.map((p) =>
            `<li><b>${esc(p.table)}.${esc(p.column)}</b> — ${esc(p.reason)}</li>`).join("");
        const tables = r.tables.map((t) => `
            <div class="card">
                <h3>Table « ${esc(t.name)} » — ${fmtInt(t.n_rows)} lignes × ${t.n_cols} colonnes · score ${t.score}/100</h3>
                <div class="table-wrap"><table class="grid">
                    <thead><tr><th>Colonne</th><th>Type</th><th class="num">Manquantes</th><th class="num">Distinctes</th>
                        <th class="num">Incohérentes</th><th class="num">Invalides</th><th class="num">Aberrantes</th></tr></thead>
                    <tbody>${t.columns.map((c) => `
                        <tr><td><b>${esc(c.name)}</b></td><td>${esc(c.type)}</td>
                            <td class="num">${c.missing} (${c.missing_pct} %)</td><td class="num">${fmtInt(c.unique)}</td>
                            <td class="num">${c.inconsistent}</td><td class="num">${c.invalid}</td><td class="num">${c.outliers}</td></tr>`).join("")}
                    </tbody></table></div>
                <div class="section-title">Aperçu (5 premières lignes)</div>
                <div class="table-wrap"><table class="grid">
                    <thead><tr>${t.preview.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
                    <tbody>${t.preview.rows.map((row) => `<tr>${row.map((v) => `<td>${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody>
                </table></div>
            </div>`).join("");

        reportBox.innerHTML = `
            <div class="card">
                <h2>Rapport de qualité — ${esc(r.source)}</h2>
                <div class="score-row">
                    <div class="ring level-${s.level}" style="--pct:${s.score}"><div><div><b>${s.score}</b><span>/ 100 · ${s.level}</span></div></div></div>
                    <div class="bars">${bars(s.indicators)}</div>
                </div>
                ${s.penalty ? `<p class="hint">Pénalité appliquée : −${s.penalty} point(s) pour ${s.n_high} problème(s) critique(s).</p>` : ""}
                <div class="kpis" style="margin-top:14px">
                    <div class="kpi"><div class="v">${s.n_tables}</div><div class="l">table(s)</div></div>
                    <div class="kpi"><div class="v">${fmtInt(s.n_rows)}</div><div class="l">lignes</div></div>
                    <div class="kpi"><div class="v">${fmtInt(s.missing_cells)}</div><div class="l">cellules manquantes</div></div>
                    <div class="kpi"><div class="v">${fmtInt(s.duplicate_rows)}</div><div class="l">lignes dupliquées</div></div>
                    <div class="kpi"><div class="v">${s.n_issues}</div><div class="l">problèmes (${s.n_high} critiques)</div></div>
                    <div class="kpi"><div class="v">${s.n_pii_columns}</div><div class="l">colonnes sensibles</div></div>
                </div>
                ${(r.notes || []).map((n) => `<p class="hint">${esc(n)}</p>`).join("")}
                <button id="attach-report" class="btn btn-primary" type="button">Joindre au chat pour en discuter</button>
            </div>
            <div class="card"><h3>Problèmes détectés</h3>
                ${issues ? `<ul class="issue-list">${issues}</ul>` : "<p class='hint'>Aucun problème détecté.</p>"}</div>
            <div class="card"><h3>Conformité et gouvernance</h3>
                ${pii ? `<p class="hint">Colonnes pouvant contenir des données personnelles ou sensibles :</p><ul class="plain">${pii}</ul>`
                      : "<p class='hint'>Aucune donnée personnelle évidente détectée.</p>"}
                <div class="section-title">Recommandations</div>
                <ul class="plain">${r.recommendations.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>
            ${tables}`;

        $("#attach-report").addEventListener("click", () => {
            App.setDataset(dataset.id, dataset.name);
            App.showTab("chat");
            App.toast("Données jointes : posez vos questions sur ce rapport.", "success");
        });
    }

    $("#dataset-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const input = $("#dataset-file");
        const button = $("button", event.currentTarget);
        if (!input.files[0]) return;
        const data = new FormData();
        data.append("file", input.files[0]);
        button.disabled = true;
        button.textContent = "Analyse en cours…";
        try {
            const { dataset } = await (await App.api("/api/datasets", { method: "POST", body: data })).json();
            renderReport(dataset);
            await loadDatasets();
            App.toast("Analyse terminée.", "success");
        } catch (err) {
            App.toastError(err);
        } finally {
            button.disabled = false;
            button.textContent = "Analyser";
            input.value = "";
        }
    });

    // =====================================================================
    // Performances
    // =====================================================================
    const statsView = $("#stats-view");
    const ratingTone = (value) => App.qualityClass(value);
    const ratingBadge = (value) =>
        `<span class="badge ${ratingTone(value)}">${value ? Number(value).toFixed(1) + "/5" : "—"}</span>`;
    const kpi = (value, label, extra = "") =>
        `<div class="kpi"><div class="v">${value}</div><div class="l">${label}</div>${extra ? `<small>${extra}</small>` : ""}</div>`;

    async function loadStats() {
        try {
            const global = (await (await App.api("/api/stats")).json()).stats;
            let current = null;
            if (App.conversationId) current = (await (await App.api(`/api/conversations/${App.conversationId}/stats`)).json()).stats;
            renderStats(global, current);
        } catch (err) {
            statsView.innerHTML = "";
            App.toastError(err);
        }
    }

    function renderStats(g, c) {
        let html = "";
        if (c && c.n_answers) {
            const maxTime = Math.max(...c.items.map((i) => i.response_time_ms || 0), 1);
            const maxTokens = Math.max(...c.items.map((i) => i.total_tokens || 0), 1);
            html += `
                <div class="section-title">Conversation en cours</div>
                <div class="kpis">
                    ${kpi(c.n_answers, "réponses")}
                    ${kpi(fmtMs(c.avg_response_ms), "temps de réponse moyen", `max ${fmtMs(c.max_response_ms)}`)}
                    ${kpi(fmtInt(c.total_tokens), "tokens consommés", `${fmtInt(c.prompt_tokens)} requêtes + ${fmtInt(c.completion_tokens)} réponses`)}
                    ${kpi(ratingBadge(c.avg_rating), "note moyenne", `${c.n_rated}/${c.n_answers} évaluée(s)`)}
                </div>
                <div class="card"><div class="table-wrap"><table class="grid">
                    <thead><tr><th>#</th><th>Question</th><th>Domaine</th><th>Temps de réponse</th><th>1er token</th>
                        <th>Tokens (req + rép = total)</th><th>Sources</th><th>Note</th></tr></thead>
                    <tbody>${c.items.map((i, n) => `
                        <tr><td>${n + 1}</td><td title="${esc(i.question)}">${esc(i.question.slice(0, 48))}${i.question.length > 48 ? "…" : ""}</td>
                            <td>${esc(App.domainLabel(i.domain))}</td>
                            <td><span class="time-bar" style="width:${Math.round(90 * (i.response_time_ms || 0) / maxTime)}px"></span> ${fmtMs(i.response_time_ms)}</td>
                            <td>${fmtMs(i.first_token_ms)}</td>
                            <td><span class="time-bar" style="width:${Math.round(90 * (i.total_tokens || 0) / maxTokens)}px;background:#0ea5e9"></span>
                                ${i.tokens_estimated ? "≈ " : ""}${fmtInt(i.prompt_tokens)} + ${fmtInt(i.completion_tokens)} = <b>${fmtInt(i.total_tokens)}</b></td>
                            <td>${i.n_sources || "—"}</td><td>${ratingBadge(i.rating)}</td></tr>`).join("")}
                    </tbody></table></div></div>`;
        } else {
            html += `<p class="hint">Aucune réponse dans la conversation en cours. Envoyez un message pour voir ses indicateurs.</p>`;
        }

        html += `
            <div class="section-title">Ensemble des conversations</div>
            <div class="kpis">
                ${kpi(fmtInt(g.n_conversations), "conversations")}
                ${kpi(fmtInt(g.n_answers), "réponses générées")}
                ${kpi(fmtMs(g.avg_response_ms), "temps de réponse moyen")}
                ${kpi(fmtInt(g.total_tokens), "tokens consommés", `${fmtInt(g.prompt_tokens)} requêtes + ${fmtInt(g.completion_tokens)} réponses`)}
                ${kpi(ratingBadge(g.avg_rating), "note moyenne", `${g.n_rated}/${g.n_answers} évaluée(s)`)}
            </div>
            <div class="card"><h3>Comparaison par domaine</h3>
                ${g.by_domain.length ? `<div class="table-wrap"><table class="grid">
                    <thead><tr><th>Domaine</th><th class="num">Réponses</th><th class="num">Temps moyen</th>
                        <th class="num">Tokens moyens</th><th class="num">Tokens totaux</th><th>Note moyenne</th></tr></thead>
                    <tbody>${g.by_domain.map((d) => `
                        <tr><td><b>${esc(App.domainLabel(d.domain))}</b></td><td class="num">${d.n_answers}</td>
                            <td class="num">${fmtMs(d.avg_response_ms)}</td><td class="num">${fmtInt(d.avg_tokens)}</td>
                            <td class="num">${fmtInt(d.total_tokens)}</td><td>${ratingBadge(d.avg_rating)}</td></tr>`).join("")}
                    </tbody></table></div>` : "<p class='hint'>Pas encore de données.</p>"}
            </div>`;
        statsView.innerHTML = html;
    }

    $("#btn-refresh-stats").addEventListener("click", loadStats);

    document.addEventListener("tabchange", (event) => {
        if (event.detail === "stats") loadStats();
        if (event.detail === "data") loadDatasets();
    });
})();
