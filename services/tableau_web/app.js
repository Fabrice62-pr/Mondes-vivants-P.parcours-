// Tableau de bord GUICHET : un parcours de questions sur les décisions de l'IA.
// Les métriques viennent de services/metriques.py : par WebSocket (/ws) en direct, par l'API (/api) pour
// l'historique. Chaque question a deux fonctions : `reponse` (la phrase courte, calculée sur les données) et
// `dessiner` (son graphique, seulement quand la page est affichée).
// Tout texte venant des données (justifications des LLM...) passe par textContent, jamais par innerHTML.
"use strict";

// ================================================================ outils

const nf = (opts) => new Intl.NumberFormat("fr-FR", opts);
const F = {
  int: (v) => (v == null ? "—" : nf({ maximumFractionDigits: 0 }).format(v)),
  pct: (v) => (v == null ? "—" : nf({ style: "percent", maximumFractionDigits: 0 }).format(v)),
  dec2: (v) => (v == null ? "—" : nf({ minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v)),
  duree: (ms) => (ms == null ? "—" : ms >= 1000 ? `${nf({ maximumFractionDigits: 1 }).format(ms / 1000)} s` : `${F.int(ms)} ms`),
  // t_sim est en minutes depuis le début du run : 4695 -> « J4 06:15 »
  moment: (t) => {
    if (t == null) return "—";
    const j = Math.floor(t / 1440) + 1, h = Math.floor((t % 1440) / 60), m = t % 60;
    return `J${j} ${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
  },
};

function el(tag, props = {}, ...enfants) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const c of enfants) if (c != null) e.append(c instanceof Node ? c : String(c));
  return e;
}
const $ = (sel) => document.querySelector(sel);
const somme = (lignes, f) => lignes.reduce((s, r) => s + (f(r) || 0), 0);

const C = {};
function lireCouleurs() {
  const s = getComputedStyle(document.documentElement);
  for (const n of ["texte", "doux", "ligne", "accent", "llm", "s1", "regle", "repli", "annonce", "peur", "bon", "mauvais", "grille"]) {
    C[n] = s.getPropertyValue(`--${n}`).trim();
  }
}

const DECIDEURS = [
  { nom: "LLM", type: "llm", couleur: () => C.llm },
  { nom: "Système 1", type: "systeme1", couleur: () => C.s1 },
  { nom: "Règle", type: "regles", couleur: () => C.regle },
  { nom: "Repli sur règle", type: "repli_regles", couleur: () => C.repli },
];
const decideur = (type) => DECIDEURS.find((d) => d.type === type) || DECIDEURS[3];
const pastille = (type) => { const d = decideur(type); return el("span", { class: "pastille", style: `color:${d.couleur()}`, text: d.nom }); };
const ROLES = { deposant: "Habitants", direction: "Direction", employe: "Employés" };
const ACTIONS = {
  retirer: "Retirer son argent", attendre: "Attendre, vigilant", rien: "Ne rien faire",
  publier_communique: "Publier un communiqué", repondre: "Répondre au client", eluder: "Éluder la question",
};
const action = (a) => ACTIONS[a] || a;

// ================================================================ graphiques (Chart.js)

const graphes = {};
function graphe(id, config) {
  if (graphes[id]) { graphes[id].data = config.data; graphes[id].options = config.options; graphes[id].update("none"); }
  else graphes[id] = new Chart(document.getElementById(id), config);
}
function axe(extra = {}) {
  return { grid: { color: C.grille }, border: { color: C.ligne }, ...extra,
           ticks: { color: C.doux, ...(extra.ticks || {}) },
           title: extra.titre ? { display: true, text: extra.titre, color: C.doux } : undefined };
}
function options(extra = {}) {
  return { responsive: true, maintainAspectRatio: false, animation: false,
           ...extra, plugins: { legend: { labels: { color: C.doux, boxWidth: 12 } }, ...(extra.plugins || {}) } };
}

// ================================================================ les questions

// niveau : ok (rassurant), attention (à surveiller), info (à lire)
const Q = {};

Q.qui = {
  reponse(m) {
    const l = (m.qui_decide || []).filter((r) => r.role === "deposant");
    const total = somme(l, (r) => r.decisions);
    if (!total) return { niveau: "info", texte: "Pas encore de décision d'habitant." };
    const part = (nom) => somme(l.filter((r) => r.decideur === nom), (r) => r.decisions) / total;
    const repli = part("Repli sur règle");
    const texte = `Sur ${F.int(total)} décisions d'habitants, ${F.pct(part("LLM"))} ont été écrites par un LLM, `
      + `${F.pct(part("Système 1"))} par le Système 1 et ${F.pct(repli)} par une règle de secours, faute de réponse du LLM.`;
    return repli > 0.05
      ? { niveau: "attention", texte: `${texte} C'est beaucoup : environ une décision sur ${Math.round(1 / repli)} n'est pas celle de l'IA.` }
      : { niveau: "ok", texte: `${texte} Les replis restent rares : ce qu'on observe est bien le comportement de l'IA.` };
  },
  dessiner(m) {
    const l = m.qui_decide || [];
    const roles = [...new Set(l.map((r) => r.role))];
    const total = (role) => somme(l.filter((r) => r.role === role), (r) => r.decisions);
    const n = (role, nom) => (l.find((r) => r.role === role && r.decideur === nom) || {}).decisions || 0;
    graphe("g-qui", {
      type: "bar",
      data: { labels: roles.map((r) => `${ROLES[r] || r} (${F.int(total(r))} décisions)`),
              datasets: DECIDEURS.map((d) => ({ label: d.nom, backgroundColor: d.couleur(),
                data: roles.map((r) => n(r, d.nom) / (total(r) || 1)), compte: roles.map((r) => n(r, d.nom)) })) },
      options: options({ indexAxis: "y",
        scales: { x: axe({ stacked: true, max: 1, ticks: { callback: F.pct } }), y: axe({ stacked: true }) },
        plugins: { tooltip: { callbacks: { label: (it) => `${it.dataset.label} : ${F.pct(it.parsed.x)} (${F.int(it.dataset.compte[it.dataIndex])} décisions)` } } } }),
    });
  },
};

Q.temps = {
  reponse(m) {
    if (!etat.runAffiche) return { niveau: "info", texte: "Cette question porte sur un seul run : choisissez-en un en haut de la page." };
    const icc = m.serie_icc || [], peur = m.serie_peur || [];
    if (!icc.length) return { niveau: "info", texte: "Pas encore d'ICC calculé pour ce run." };
    const max = icc.reduce((a, b) => (b.valeur > a.valeur ? b : a));
    let texte = `L'ICC a atteint ${F.dec2(max.valeur)} à ${F.moment(max.t_sim)}.`;
    if (peur.length > 1) {
      texte += ` La peur moyenne écrite par les LLM est passée de ${F.dec2(peur[0].peur)} (${F.moment(peur[0].heure)}) `
        + `à ${F.dec2(peur[peur.length - 1].peur)} (${F.moment(peur[peur.length - 1].heure)}).`;
    }
    return max.valeur >= 0.6
      ? { niveau: "attention", texte: `${texte} Au-dessus de 0,6, les habitants partagent la même attente d'une ruée : un contexte commun s'est formé.` }
      : { niveau: "info", texte: `${texte} Sous 0,6, les attentes restent dispersées : pas de croyance collective de ruée.` };
  },
  dessiner(m) {
    graphe("g-temps", {
      type: "line",
      data: { datasets: [
        { label: "ICC (croyance collective)", data: (m.serie_icc || []).map((p) => ({ x: p.t_sim, y: p.valeur })),
          borderColor: C.accent, backgroundColor: C.accent, pointRadius: 0, borderWidth: 2 },
        { label: "Peur écrite par les LLM", data: (m.serie_peur || []).map((p) => ({ x: p.heure, y: p.peur })),
          borderColor: C.peur, backgroundColor: C.peur, pointRadius: 3, borderWidth: 1, showLine: false },
      ] },
      options: options({ parsing: false,
        scales: { x: axe({ type: "linear", ticks: { callback: F.moment, maxTicksLimit: 8 } }),
                  y: axe({ min: 0, max: 1, ticks: { callback: F.dec2 } }) },
        plugins: { tooltip: { callbacks: { title: (it) => F.moment(it[0].parsed.x), label: (it) => `${it.dataset.label} : ${F.dec2(it.parsed.y)}` } } } }),
    });
    const s = m.serie_decisions || [];
    const heures = [...new Set(s.map((r) => r.heure))].sort((a, b) => a - b);
    graphe("g-decisions", {
      type: "bar",
      data: { labels: heures.map(F.moment), datasets: DECIDEURS.map((d) => ({ label: d.nom, backgroundColor: d.couleur(),
        data: heures.map((h) => (s.find((r) => r.heure === h && r.decideur === d.nom) || {}).decisions || 0) }))
        .filter((ds) => ds.data.some((v) => v > 0)) },
      options: options({ scales: { x: axe({ stacked: true, ticks: { maxTicksLimit: 10 } }), y: axe({ stacked: true }) } }),
    });
  },
};

Q.calibration = {
  // Le groupe le plus haut que le LLM a vérifié (le Système 1 n'y a pas décidé seul).
  verifie: (m) => (m.calibration_s1 || []).filter((r) => !r.decide_seul && r.decisions >= 5).pop(),
  reponse(m) {
    const r = this.verifie(m);
    if (!r) return { niveau: "info", texte: "Pas de Système 1 dans ce run, ou trop peu de décisions vérifiées par le LLM pour juger." };
    const texte = `Quand le Système 1 annonce un retrait à ${F.pct(r.p_annoncee)} en moyenne (${F.int(r.decisions)} décisions), `
      + `le LLM consulté ensuite ne retire que dans ${F.pct(r.part_retrait_reelle)} des cas.`;
    return Math.abs(r.p_annoncee - r.part_retrait_reelle) > 0.25
      ? { niveau: "attention", texte: `${texte} Sa confiance ne prédit pas la décision : on ne peut pas lui confier seul les retraits.` }
      : { niveau: "ok", texte: `${texte} Sa confiance correspond à peu près à la réalité.` };
  },
  dessiner(m) {
    const l = m.calibration_s1 || [];
    const nom = (r) => [`${F.pct(r.tranche_min)} à ${F.pct(r.tranche_min + 0.2)}`, r.decide_seul ? "(non vérifié)" : `(${F.int(r.decisions)} déc.)`];
    graphe("g-calibration", {
      type: "bar",
      data: { labels: l.map(nom), datasets: [
        { label: "Annoncé par le Système 1", data: l.map((r) => r.p_annoncee), backgroundColor: C.annonce },
        { label: "Réellement décidé", data: l.map((r) => r.part_retrait_reelle), backgroundColor: C.llm },
      ] },
      options: options({
        scales: { x: axe({ titre: "Groupes de décisions, selon la probabilité de retrait annoncée" }),
                  y: axe({ min: 0, max: 1, ticks: { callback: F.pct }, titre: "Part de retraits" }) },
        plugins: { tooltip: { callbacks: { label: (it) => `${it.dataset.label} : ${F.pct(it.parsed.y)}`,
          afterBody: (it) => { const r = l[it[0].dataIndex]; return r.decide_seul ? `${F.int(r.decide_seul)} décisions prises seules par le Système 1 : non vérifiées` : `${F.int(r.decisions)} décisions vérifiées par le LLM`; } } } } }),
    });
  },
};

Q.peur = {
  reponse(m) {
    const l = m.peur_declaree || [];
    const total = somme(l, (r) => r.reponses);
    if (!total) return { niveau: "info", texte: "Aucune peur écrite par un LLM pour l'instant." };
    const rondes = somme(l.filter((r) => r.multiple_de_005), (r) => r.reponses) / total;
    const texte = `${F.pct(rondes)} des ${F.int(total)} valeurs écrites sont des nombres ronds (multiples de 0,05), contre environ 21 % si le LLM choisissait sans préférence.`;
    return rondes > 0.35
      ? { niveau: "attention", texte: `${texte} Le LLM arrondit : lisez ce nombre comme une tendance, pas comme une mesure précise.` }
      : { niveau: "ok", texte: `${texte} Pas de préférence marquée pour les nombres ronds.` };
  },
  dessiner(m) {
    const par = new Map((m.peur_declaree || []).map((r) => [Math.round(r.peur * 100), r.reponses]));
    const x = Array.from({ length: 101 }, (_, i) => i);
    graphe("g-peur", {
      type: "bar",
      data: { labels: x.map((i) => F.dec2(i / 100)), datasets: [{ label: "Nombre de réponses", data: x.map((i) => par.get(i) || 0),
        backgroundColor: x.map((i) => (i % 5 === 0 ? C.llm : C.annonce)), barPercentage: 1, categoryPercentage: 0.95 }] },
      options: options({ plugins: { legend: { display: false }, tooltip: { callbacks: { title: (it) => `peur = ${it[0].label}`, label: (it) => `écrite ${F.int(it.parsed.y)} fois` } } },
        scales: { x: axe({ titre: "Peur écrite (0 = calme, 1 = panique)", ticks: { maxTicksLimit: 11 } }), y: axe({ titre: "Nombre de réponses" }) } }),
    });
  },
};

Q.ancrage = {
  reponse(m) {
    const l = (m.ancrage || []).filter((r) => r.reponses >= 10 && r.correlation_peur != null);
    if (!l.length) return { niveau: "info", texte: "Pas assez de réflexions de LLM pour juger." };
    const fort = l[0], faible = l[l.length - 1];
    let texte = `${fort.modele} reprend largement la peur qu'on lui rappelle (corrélation ${F.dec2(fort.correlation_peur)} sur 1)`;
    texte += l.length > 1 ? ` ; ${faible.modele} réévalue davantage (${F.dec2(faible.correlation_peur)}).` : ".";
    return fort.correlation_peur >= 0.7
      ? { niveau: "attention", texte: `${texte} Une partie de son comportement vient du prompt, pas d'un raisonnement.` }
      : { niveau: "ok", texte: `${texte} Les modèles réévaluent la situation à chaque réflexion.` };
  },
  dessiner(m) {
    const pts = m.ancrage_points || [];
    const modeles = [...new Set(pts.map((p) => p.modele))];
    const couleurs = [C.llm, C.repli, C.s1, C.regle];
    graphe("g-ancrage", {
      type: "scatter",
      data: { datasets: [
        { type: "line", label: "Recopie exacte", data: [{ x: 0, y: 0 }, { x: 1, y: 1 }], borderColor: C.doux, borderDash: [5, 5], borderWidth: 1, pointRadius: 0 },
        ...modeles.map((mod, i) => ({ label: mod, data: pts.filter((p) => p.modele === mod).map((p) => ({ x: p.peur_avant, y: p.peur })),
          backgroundColor: couleurs[i % couleurs.length] + "99", pointRadius: 3 })),
      ] },
      options: options({ parsing: false,
        scales: { x: axe({ min: 0, max: 1, titre: "Peur rappelée dans le prompt", ticks: { callback: F.dec2 } }),
                  y: axe({ min: 0, max: 1, titre: "Peur renvoyée par le LLM", ticks: { callback: F.dec2 } }) },
        plugins: { tooltip: { callbacks: { label: (it) => `${it.dataset.label} : ${F.dec2(it.parsed.x)} → ${F.dec2(it.parsed.y)}` } } } }),
    });
    $("#t-ancrage").replaceChildren(...(m.ancrage || []).map((r) => el("tr", {},
      el("td", { class: "nom", text: `${r.modele} (${r.tier})` }), el("td", { class: "n", text: F.int(r.reponses) }),
      el("td", { class: "n", text: F.dec2(r.correlation_peur) }),
      el("td", { class: "n", text: r.variation_moyenne == null ? "—" : `${r.variation_moyenne >= 0 ? "+" : ""}${F.dec2(r.variation_moyenne)}` }))));
  },
};

Q.fiabilite = {
  reponse(m) {
    const l = (m.fiabilite_modeles || []).filter((r) => r.appels >= 5);
    if (!l.length) return { niveau: "info", texte: "Pas encore d'appel au LLM." };
    const pire = l.reduce((a, b) => (b.taux_reussite < a.taux_reussite ? b : a));
    if (pire.taux_reussite >= 0.95) return { niveau: "ok", texte: `Les ${l.length} modèles ont répondu à plus de 95 % de leurs appels.` };
    const cause = pire.quota >= pire.erreurs ? `surtout à cause des quotas (${F.int(pire.quota)} refus)` : `surtout à cause d'erreurs (${F.int(pire.erreurs)})`;
    return { niveau: pire.taux_reussite < 0.7 ? "attention" : "info",
      texte: `Le modèle le moins fiable, ${pire.modele}, n'a réussi que ${F.pct(pire.taux_reussite)} de ses ${F.int(pire.appels)} appels, ${cause}. `
        + "Les décisions de ses agents sont souvent prises par la règle de secours." };
  },
  dessiner(m) {
    const l = m.fiabilite_modeles || [];
    const couleur = (t) => (t >= 0.95 ? C.bon : t >= 0.7 ? C.repli : C.mauvais);
    graphe("g-fiabilite", {
      type: "bar",
      data: { labels: l.map((r) => `${r.modele} (${r.tier})`), datasets: [{ label: "Appels réussis", data: l.map((r) => r.taux_reussite),
        backgroundColor: l.map((r) => couleur(r.taux_reussite)) }] },
      options: options({ indexAxis: "y", plugins: { legend: { display: false }, tooltip: { callbacks: {
          label: (it) => { const r = l[it.dataIndex]; return [`${F.pct(r.taux_reussite)} réussis sur ${F.int(r.appels)} appels`, `temps de réponse habituel : ${F.duree(r.latence_mediane_ms)}`]; } } } },
        scales: { x: axe({ min: 0, max: 1, ticks: { callback: F.pct } }), y: axe() } }),
    });
    $("#t-fiabilite").replaceChildren(...l.map((r) => el("tr", {},
      el("td", { class: "nom", text: r.modele }), el("td", { text: r.tier || "—" }), el("td", { class: "n", text: F.int(r.appels) }),
      el("td", { class: `n ${r.taux_reussite >= 0.95 ? "bon" : r.taux_reussite < 0.7 ? "mauvais" : ""}`, text: F.pct(r.taux_reussite) }),
      el("td", { class: "n", text: F.int(r.quota) }), el("td", { class: "n", text: F.int(r.erreurs) }),
      el("td", { class: "n", text: F.duree(r.latence_mediane_ms) }), el("td", { class: "n", text: F.duree(r.latence_p95_ms) }))));
  },
};

Q.pourquoi = {
  // Le fil des dernières décisions, complété par toutes celles des institutions (plus rares, plus importantes).
  decisions(m) {
    const vus = new Set(), out = [];
    for (const r of [...(m.decisions_majeures || []), ...(m.fil_decisions || [])]) if (!vus.has(r.event_id)) { vus.add(r.event_id); out.push(r); }
    return out.sort((a, b) => (a.run_id === b.run_id ? b.t_sim - a.t_sim : a.run_id < b.run_id ? 1 : -1));
  },
  reponse(m) {
    const n = this.decisions(m).length;
    return { niveau: "info", texte: n ? "Cliquez sur une décision pour suivre son histoire : ce qui l'a provoquée, ce que l'agent savait, sa réponse et ce qu'elle a changé."
                                       : "Pas encore de décision." };
  },
  dessiner(m) {
    const toutes = this.decisions(m);
    remplirChoix($("#f-decideur"), DECIDEURS.map((d) => [d.nom, d.nom]), "Tous les décideurs");
    remplirChoix($("#f-action"), [...new Set(toutes.map((r) => r.action))].map((a) => [a, action(a)]), "Toutes les décisions");
    const inst = $("#f-institutions").checked, fd = $("#f-decideur").value, fa = $("#f-action").value;
    const ft = $("#f-texte").value.trim().toLowerCase();
    const garde = toutes.filter((r) => (!inst || r.role !== "deposant") && (!fd || decideur(r.type).nom === fd)
      && (!fa || r.action === fa) && (!ft || (r.justification || "").toLowerCase().includes(ft)));
    const signaler = etat.source === "direct" && etat.vus != null;
    $("#t-fil").replaceChildren(...garde.slice(0, 300).map((r) => el("tr", {
        class: signaler && !etat.vus.has(r.event_id) ? "nouveau" : "", onclick: () => ouvrirFiche(r.event_id), tabindex: "0",
        onkeydown: (e) => { if (e.key === "Enter") ouvrirFiche(r.event_id); } },
      el("td", { class: "nom", text: F.moment(r.t_sim) }), el("td", { text: r.nom || r.agent_id }), el("td", {}, pastille(r.type)),
      el("td", { text: action(r.action) }), el("td", { class: "just", text: r.justification }))));
    $("#f-compte").textContent = `${F.int(Math.min(garde.length, 300))} décisions affichées, les plus récentes en haut.`;
    etat.vus = new Set(toutes.map((r) => r.event_id));
  },
};

function remplirChoix(sel, paires, premier) {
  const actuel = sel.value;
  sel.replaceChildren(el("option", { value: "", text: premier }), ...paires.map(([v, t]) => el("option", { value: v, text: t })));
  sel.value = paires.some(([v]) => v === actuel) ? actuel : "";
}
for (const id of ["#f-institutions", "#f-decideur", "#f-action"]) $(id).addEventListener("change", () => Q.pourquoi.dessiner(etat.dernier || {}));
$("#f-texte").addEventListener("input", () => Q.pourquoi.dessiner(etat.dernier || {}));

// ================================================================ pages et sommaire

const PAGES = [...document.querySelectorAll("section.page")].map((s) => s.id);
const etat = { source: "direct", run: null, runAffiche: null, page: "accueil", ws: null, dernier: null, vus: null, runsArchives: [] };
const verdicts = {};

$("#sommaire").replaceChildren(...PAGES.map((id, i) => el("li", {}, el("button", { "data-page": id, onclick: () => allerA(id) },
  el("span", { class: "num", text: i ? String(i) : "" }), el("span", { text: document.getElementById(id).dataset.court }),
  el("span", { class: "pt", "data-pt": id })))));

function allerA(page) {
  etat.page = page;
  for (const id of PAGES) document.getElementById(id).hidden = id !== page;
  for (const b of document.querySelectorAll("#sommaire button")) {
    if (b.dataset.page === page) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  }
  const i = PAGES.indexOf(page);
  $("#precedent").hidden = i === 0;
  $("#suivant").hidden = i === PAGES.length - 1;
  $("#suivant").textContent = i === 0 ? "Commencer : question 1 →" : "Question suivante →";
  majAdresse();
  dessinerPage();
  window.scrollTo({ top: 0 });
}
$("#precedent").addEventListener("click", () => allerA(PAGES[PAGES.indexOf(etat.page) - 1]));
$("#suivant").addEventListener("click", () => allerA(PAGES[PAGES.indexOf(etat.page) + 1]));

// ================================================================ rendu

function rendre(m) {
  etat.dernier = m || {};
  m = etat.dernier;
  const s = (m.synthese || [])[0] || {};
  const msg = $("#message");
  msg.hidden = !!s.decisions;
  msg.textContent = etat.source === "direct"
    ? "Aucune décision reçue pour l'instant. Lancez un run depuis Godot : les réponses se rempliront en direct, chaque seconde."
    : "Aucun run archivé. Un run s'archive tout seul à sa fin (ou avec python -m services.archiveur).";
  for (const id of PAGES.slice(1)) {
    const v = Q[id].reponse(m);
    verdicts[id] = v;
    const boite = document.getElementById(`v-${id}`);
    boite.className = `verdict ${v.niveau}`;
    boite.replaceChildren(el("span", { class: `pt ${v.niveau}` }),
      el("div", {}, el("strong", { text: { ok: "Réponse courte : rassurant", attention: "Réponse courte : à surveiller", info: "Réponse courte" }[v.niveau] }),
        el("p", { text: v.texte })));
    document.querySelector(`[data-pt="${id}"]`).className = `pt ${v.niveau}`;
  }
  rendreAccueil(s);
  dessinerPage();
}

function dessinerPage() {
  // Chart.js mesure son conteneur : on ne dessine que la page visible, et on la redessine quand on y arrive.
  if (etat.page !== "accueil" && etat.dernier) Q[etat.page].dessiner(etat.dernier);
}

function rendreAccueil(s) {
  $("#a-decisions").textContent = F.int(s.decisions);
  $("#a-llm").textContent = s.decisions ? F.pct(s.decisions_llm / s.decisions) : "—";
  $("#a-retraits").textContent = F.int(s.retraits);
  $("#a-icc").textContent = F.dec2(s.icc_max);
  $("#cartes").replaceChildren(...PAGES.slice(1).map((id, i) => {
    const v = verdicts[id] || { niveau: "info", texte: "" };
    return el("button", { class: "carte", onclick: () => allerA(id) },
      el("span", { class: "titre" }, el("span", { class: `pt ${v.niveau}` }), `${i + 1}. ${document.querySelector(`#${id} h1`).textContent}`),
      el("span", { class: "reponse", text: v.texte }));
  }));
  const archives = etat.source === "historique" && !etat.run && etat.runsArchives.length;
  $("#runs-archives").hidden = !archives;
  if (archives) {
    $("#t-runs").replaceChildren(...etat.runsArchives.map((r) => el("tr", { onclick: () => choisirRun(r.run_id) },
      el("td", { class: "nom", text: r.run_id }), el("td", { text: r.systeme1 || "—" }),
      el("td", { class: r.defaut ? "mauvais" : "", text: r.defaut ? "la Caisse a fait défaut" : r.interrompu ? "interrompu" : "pas de défaut" }),
      el("td", { class: "n", text: F.pct(r.part_retiree) }), el("td", { class: "n", text: F.dec2(r.icc_max) }),
      el("td", { class: "n", text: F.int(r.appels_llm) }))));
  }
}

// ================================================================ l'histoire d'une décision

function resume(e) {
  const p = e.payload || {};
  switch (e.type) {
    case "belief.updated": return `Peur ${F.dec2(p.peur_avant)} → ${F.dec2(p.peur)} ; part des autres qui vont retirer, selon lui : ${F.dec2(p.estime_autres_avant)} → ${F.dec2(p.estime_autres)}.`;
    case "decision.made": return `${p.agent_id} : ${action(p.action)} (${decideur(p.type).nom}).`;
    case "communique.published": return `Communiqué ${p.ton} : « ${p.texte} »`;
    case "conversation.held": return `${p.locuteur_id} dit à ${p.auditeur_id} : « ${p.phrase} »`;
    case "memory.written": return `Nouveau souvenir : ${p.texte}`;
    case "withdrawal.served": return `Retrait servi : ${F.int(p.paye)} payés, ${F.int(p.perdu)} perdus.`;
    default: { const t = p.titre || p.texte || p.phrase; return t ? String(t) : JSON.stringify(p).slice(0, 220); }
  }
}
const NOMS_EVTS = { "belief.updated": "Croyance mise à jour", "decision.made": "Décision", "article.published": "Article de presse",
  "communique.published": "Communiqué", "conversation.held": "Conversation", "memory.written": "Souvenir", "queue.changed": "File d'attente",
  "withdrawal.served": "Retrait", "caisse.updated": "État de la Caisse", "clock.tick": "Horloge" };

function listeEvts(evts, vide) {
  if (!evts.length) return el("p", { class: "aide", text: vide });
  return el("ul", { class: "evts" }, ...evts.map((e) => el("li", {},
    el("div", { class: "quand", text: `${F.moment(e.t_sim)} · ${NOMS_EVTS[e.type] || e.type}` }), resume(e))));
}

function etape(titre, pourquoi, ...contenu) {
  return el("li", {}, el("h3", { text: titre }), el("p", { class: "pourquoi", text: pourquoi }), ...contenu);
}

async function ouvrirFiche(eventId) {
  const corps = $("#fiche-corps");
  corps.replaceChildren(el("p", { class: "aide", text: "Chargement…" }));
  $("#fiche").hidden = false;
  $(".fiche .fermer").focus();
  let f;
  try {
    const r = await fetch(`/api/decisions/${encodeURIComponent(eventId)}?source=${etat.source}`);
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    f = await r.json();
  } catch (err) {
    corps.replaceChildren(el("p", { class: "mauvais", text: `Fiche indisponible : ${err.message}` }));
    return;
  }
  const d = f.decision;
  const s1 = d.systeme1 && typeof d.systeme1 === "object" ? d.systeme1 : null;
  const docs = Array.isArray(d.documents) ? d.documents : [];
  const souvenirs = d.souvenirs || [];
  const dernierAppel = f.appels[f.appels.length - 1];
  const retard = d.t_sim_contexte != null ? d.t_sim - d.t_sim_contexte : 0;
  $("#fiche-titre").textContent = `${d.nom || d.agent_id} : ${action(d.action)}`;

  let tri;
  if (s1) {
    const pourquoiLLM = s1.escalade === "retrait" ? "Il a vu un retrait possible : un retrait est toujours confirmé par le LLM."
      : s1.escalade === "ambigu" ? "Il n'était pas assez sûr de lui : il a passé la main au LLM." : "Il était assez sûr de lui pour décider seul, sans appeler le LLM.";
    tri = el("p", { text: `Il a estimé le retrait probable à ${F.pct(s1.p_retirer)} (confiance ${F.pct(s1.confiance)}). ${pourquoiLLM}` });
  } else {
    tri = el("p", { class: "aide", text: d.type === "llm" || d.type === "repli_regles" ? "Pas de Système 1 dans ce run : la décision est allée directement au LLM." : "Pas de tri : cet agent décide par règle." });
  }

  let decision;
  if (d.type === "llm") {
    decision = [el("p", { text: `Le modèle ${d.modele} a répondu en ${F.duree(dernierAppel && dernierAppel.latence_ms)}${dernierAppel && dernierAppel.essai ? `, au ${dernierAppel.essai + 1}e essai` : ""}. Il a écrit :` }),
      el("blockquote", { text: d.justification }),
      el("p", { class: "aide", text: "Ce texte est écrit en même temps que la décision : c'est l'explication que donne l'IA, pas forcément la vraie cause." })];
  } else if (d.type === "repli_regles") {
    decision = [el("p", { text: `Le LLM n'a pas pu répondre (${f.appels.map((a) => a.statut).join(", ") || "pas d'appel"}). La règle de secours a décidé :` }),
      el("blockquote", { text: d.justification })];
  } else {
    decision = [el("p", { text: d.type === "systeme1" ? "Le Système 1 a décidé seul :" : "La règle de l'agent a décidé :" }), el("blockquote", { text: d.justification })];
  }
  if (retard > 0) decision.push(el("p", { class: "aide", text: `La réponse est arrivée en retard : appliquée ${F.int(retard)} minutes simulées après le moment de la réflexion (${F.moment(d.t_sim_contexte)}).` }));

  corps.replaceChildren(
    el("p", { class: "aide" }, `${F.moment(d.t_sim)} · ${ROLES[d.role] || d.role || ""}${d.profil ? ` (${d.profil})` : ""} · `, pastille(d.type)),
    el("ol", { class: "histoire" },
      etape("Ce qui s'est passé juste avant", "Les événements que le moteur a enregistrés comme causes de cette décision.",
        listeEvts(f.causes, "Aucune cause enregistrée : l'agent a réfléchi parce que c'était son tour.")),
      etape("Ce que l'agent avait en tête", "Le RAG a retrouvé ces documents et souvenirs dans sa mémoire ; ils ont été écrits dans son prompt.",
        docs.length ? el("ul", { class: "evts" }, ...docs.map((x) => el("li", { text: `${x.doc_id} (proximité ${F.dec2(x.score)}${x.t_sim_doc != null ? `, publié ${F.moment(x.t_sim_doc)}` : ""})` }))) : null,
        souvenirs.length ? el("p", { text: `${souvenirs.length} souvenir(s) personnel(s) rappelé(s).` }) : null,
        !docs.length && !souvenirs.length ? el("p", { class: "aide", text: "Aucun document ni souvenir : RAG désactivé, ou rien de pertinent." }) : null),
      etape("Le tri du Système 1", "Avant d'appeler le LLM, le Système 1 juge s'il faut vraiment le faire (question 3).", tri),
      etape("La décision", "Qui a décidé, et ce qu'il en dit.", ...decision),
      etape("Ce que la décision a changé", "Les événements qui citent cette décision comme cause.",
        listeEvts(f.effets, "Aucun effet enregistré (par exemple : attendre ne change rien)."))),
  );
}
function fermerFiche() { $("#fiche").hidden = true; }
document.querySelectorAll("[data-fermer]").forEach((b) => b.addEventListener("click", fermerFiche));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") fermerFiche(); });

// ================================================================ choix de la source et du run

// L'adresse garde la source, la page et le run : #historique/calibration/<run_id>
function majAdresse() {
  history.replaceState(null, "", `#${etat.source}/${etat.page}${etat.run ? `/${encodeURIComponent(etat.run)}` : ""}`);
}
function afficherEtat(classe, texte) { $("#etat").className = `etat ${classe}`; $("#etat span").textContent = texte; }
function remplirRuns(options, choisi) {
  $("#choix-run").replaceChildren(...options.map(([v, t]) => el("option", { value: v, text: t })));
  $("#choix-run").value = choisi ?? "";
}
$("#choix-run").addEventListener("change", (e) => {
  etat.run = e.target.value || null;
  etat.vus = null;
  majAdresse();
  if (etat.source === "direct") envoyerChoix(); else chargerHistorique();
});
function choisirRun(runId) { etat.run = runId; majAdresse(); chargerHistorique(); }

// En direct : le serveur pousse les métriques dès que de nouveaux événements arrivent.
function envoyerChoix() { if (etat.ws && etat.ws.readyState === WebSocket.OPEN) etat.ws.send(JSON.stringify({ run: etat.run })); }
function ouvrirDirect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  etat.ws = ws;
  ws.onopen = envoyerChoix;
  ws.onmessage = (ev) => {
    if (etat.source !== "direct") return;
    const msg = JSON.parse(ev.data);
    etat.runAffiche = msg.run;
    const runs = msg.runs || [];
    remplirRuns([["", "Dernier run reçu"], ...runs.map((r) => [r.run_id, `${r.run_id} · ${r.termine ? "terminé" : "en cours"}`])], etat.run);
    const r = runs.find((x) => x.run_id === msg.run);
    if (msg.kafka_erreur) afficherEtat("erreur", `Kafka : ${msg.kafka_erreur}`);
    else if (!r) afficherEtat("attente", "En attente d'un run");
    else afficherEtat(r.termine ? "attente" : "ok", r.termine ? `Run terminé à ${F.moment(r.t_sim)}` : `En direct · ${F.moment(r.t_sim)}`);
    rendre(msg.metriques);
  };
  ws.onclose = () => {
    if (etat.ws !== ws || etat.source !== "direct") return;
    afficherEtat("erreur", "Service injoignable, nouvelle tentative…");
    setTimeout(() => { if (etat.source === "direct" && etat.ws === ws) ouvrirDirect(); }, 2000);
  };
}
setInterval(async () => {
  if (etat.source !== "direct") return;
  try { if (!(await (await fetch("/api/sante")).json()).kafka) afficherEtat("erreur", "Kafka injoignable : démarrez Docker puis lancer.bat"); } catch { /* signalé par le WebSocket */ }
}, 5000);

// Historique : une requête à chaque changement de run.
async function chargerHistorique() {
  afficherEtat("", "Historique");
  try {
    etat.runsArchives = (await (await fetch("/api/runs")).json()).historique || [];
    remplirRuns([["", `Tous les runs archivés (${etat.runsArchives.length})`],
      ...etat.runsArchives.map((r) => [r.run_id, `${r.run_id}${r.etiquette ? ` · ${r.etiquette}` : ""}${r.defaut ? " · défaut" : ""}`])], etat.run);
    const q = new URLSearchParams({ source: "historique", ...(etat.run ? { run: etat.run } : {}) });
    etat.runAffiche = etat.run;
    rendre((await (await fetch(`/api/metriques?${q}`)).json()).metriques);
  } catch (err) {
    afficherEtat("erreur", `Historique indisponible : ${err.message}`);
  }
}

function changerSource(source, run = null) {
  etat.source = source; etat.run = run; etat.vus = null;
  for (const b of document.querySelectorAll(".onglets button")) b.setAttribute("aria-pressed", String(b.dataset.source === source));
  if (etat.ws) { const ws = etat.ws; etat.ws = null; ws.close(); }
  majAdresse();
  if (source === "direct") { afficherEtat("attente", "Connexion…"); ouvrirDirect(); } else chargerHistorique();
}
document.querySelectorAll(".onglets button").forEach((b) => b.addEventListener("click", () => changerSource(b.dataset.source)));

// Le calcul SQL de chaque métrique, affiché dans « Comprendre en profondeur ».
fetch("/api/requetes").then((r) => r.json()).then((sql) => {
  for (const pre of document.querySelectorAll("pre[data-sql]")) pre.textContent = sql[pre.dataset.sql] || "";
}).catch(() => {});

// ================================================================ démarrage

lireCouleurs();
{
  const [source, page, run] = location.hash.slice(1).split("/");
  etat.page = PAGES.includes(page) ? page : "accueil";
  allerA(etat.page);
  changerSource(source === "historique" ? "historique" : "direct", run ? decodeURIComponent(run) : null);
}
