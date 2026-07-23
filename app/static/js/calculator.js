/* Athena Business Model — logique du calculateur (frontend).
   La saisie est envoyée au moteur serveur (/api/calculer), source de vérité,
   qui renvoie l'analyse de viabilité. Les résultats sont recalculés à chaque
   modification (avec un léger debounce).
   Les fonctions Pro (IS, projection, scénarios, trésorerie, CAC/LTV) sont
   activées uniquement pour les utilisateurs connectés (window.MODEL_ID != null). */

const FREQUENCES = ["mensuel", "trimestriel", "semestriel", "annuel", "hebdomadaire", "quotidien", "unique"];
const NOMS_MOIS  = ["Jan","Fév","Mar","Avr","Mai","Jun","Jul","Aoû","Sep","Oct","Nov","Déc"];

/* Échappe le texte saisi avant de l'injecter dans du HTML (un guillemet dans
   une description casserait l'attribut value ; on neutralise aussi < > & '). */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const eur = (n) => (n == null || isNaN(n)) ? "—" :
  new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(n);
const pct = (n) => (n == null || isNaN(n)) ? "—" : (n * 100).toFixed(1) + " %";
const eurK = (v) => {
  if (v == null || isNaN(v)) return "—";
  return Math.abs(v) >= 1000 ? (v / 1000).toFixed(0) + " k€" : Math.round(v) + " €";
};

let chart     = null;
let chartTreso = null;
let timer     = null;

/* ---------- Construction des lignes ---------- */
function charge(d = {}) {
  return `<div class="row charges">
    <div><div class="col-label">Description</div><input class="c-desc" type="text" value="${esc(d.description)}" placeholder="Loyer"></div>
    <div><div class="col-label">Fréquence</div><select class="c-freq">${FREQUENCES.map(f => `<option ${d.frequence === f ? "selected" : ""}>${f}</option>`).join("")}</select></div>
    <div><div class="col-label">Montant (HT)</div><input class="c-mont" type="number" step="10" value="${d.montant_unitaire ?? ""}"></div>
    <button class="mini-btn" onclick="this.parentElement.remove();schedule()">✕</button></div>`;
}
function role(d = {}) {
  return `<div class="row equipe">
    <div><div class="col-label">Rôle</div><input class="e-desc" type="text" value="${esc(d.description)}" placeholder="Direction"></div>
    <div><div class="col-label">ETP <span class="tip" data-tip="Équivalent Temps Plein. 1 = plein temps, 0,5 = mi-temps. Toi à 80% = 0,8.">?</span></div><input class="e-etp" type="number" step="0.1" value="${d.etp ?? ""}"></div>
    <div><div class="col-label">Net mensuel/ETP</div><input class="e-net" type="number" step="50" value="${d.remuneration_nette_mensuelle ?? ""}"></div>
    <button class="mini-btn" onclick="this.parentElement.remove();schedule()">✕</button></div>`;
}
function invest(d = {}) {
  return `<div class="row invest">
    <div><div class="col-label">Description</div><input class="i-desc" type="text" value="${esc(d.description)}" placeholder="Ordinateur"></div>
    <div><div class="col-label">Durée (ans)</div><input class="i-duree" type="number" step="1" value="${d.duree_amortissement ?? ""}"></div>
    <div><div class="col-label">Montant (HT)</div><input class="i-mont" type="number" step="50" value="${d.montant_investi ?? ""}"></div>
    <button class="mini-btn" onclick="this.parentElement.remove();schedule()">✕</button></div>`;
}
function offre(d = {}) {
  /* Volume(s) : nouveau champ `volumes` par année, avec repli sur l'ancien
     `quantite` unique (rétro-compatibilité des modèles sauvegardés). */
  const vols = Array.isArray(d.volumes) ? d.volumes
             : (d.quantite != null ? [d.quantite] : []);

  if (window.IS_PREMIUM) {
    /* Une case vide = « suit la croissance » (auto-remplie côté serveur) ;
       un 0 explicite = offre absente cette année-là. */
    const v = (i) => (vols[i] != null && vols[i] !== "" ? vols[i] : "");
    return `<div class="row offres pro">
      <div><div class="col-label">Offre</div><input class="o-desc" type="text" value="${esc(d.description)}" placeholder="Atelier"></div>
      <div><div class="col-label">Prix (HT)</div><input class="o-prix" type="number" step="10" value="${d.prix ?? ""}"></div>
      <div><div class="col-label">Coût var.</div><input class="o-cvar" type="number" step="10" value="${d.cout_variable ?? ""}"></div>
      <div>
        <div class="col-label">Volumes / an <span class="tip" data-tip="Ton volume prévu chaque année. Mets 0 pour une offre qui n'existe pas encore (ou plus) cette année-là — ex. des pilotes en An 1, tes licences à partir de l'An 2. Laisse vide pour suivre automatiquement ton taux de croissance.">?</span></div>
        <div class="vol-group">
          <input class="o-vol" data-an="0" type="number" step="1" min="0" value="${v(0)}" aria-label="Volume An 1">
          <input class="o-vol" data-an="1" type="number" step="1" min="0" value="${v(1)}" aria-label="Volume An 2">
          <input class="o-vol" data-an="2" type="number" step="1" min="0" value="${v(2)}" aria-label="Volume An 3">
        </div>
        <div class="vol-sub"><span>An 1</span><span>An 2</span><span>An 3</span></div>
      </div>
      <button class="mini-btn" onclick="this.parentElement.remove();schedule()">✕</button></div>`;
  }

  /* Gratuit — une seule colonne « Nb / an » (comportement inchangé). */
  const q = vols.length ? vols[0] : (d.quantite ?? "");
  return `<div class="row offres">
    <div><div class="col-label">Offre</div><input class="o-desc" type="text" value="${esc(d.description)}" placeholder="Atelier"></div>
    <div><div class="col-label">Prix (HT)</div><input class="o-prix" type="number" step="10" value="${d.prix ?? ""}"></div>
    <div><div class="col-label">Coût var. (HT)</div><input class="o-cvar" type="number" step="10" value="${d.cout_variable ?? ""}"></div>
    <div><div class="col-label">Nb / an</div><input class="o-qte" type="number" step="1" value="${q ?? ""}"></div>
    <button class="mini-btn" onclick="this.parentElement.remove();schedule()">✕</button></div>`;
}

function addCharge(d) { document.getElementById("charges").insertAdjacentHTML("beforeend", charge(d)); schedule(); }
function addRole(d)   { document.getElementById("equipe").insertAdjacentHTML("beforeend", role(d)); schedule(); }
function addInvest(d) { document.getElementById("invest").insertAdjacentHTML("beforeend", invest(d)); schedule(); }
function addOffre(d)  { document.getElementById("offres").insertAdjacentHTML("beforeend", offre(d)); schedule(); }

/* ---------- Lecture des données ---------- */
function collect() {
  const val = (el, sel) => el.querySelector(sel).value;
  const num = (el, sel) => parseFloat(el.querySelector(sel).value) || 0;
  const data = {
    parametres: {
      collecte_tva:                   document.getElementById("collecte_tva").checked,
      cotisations_patronales:         document.getElementById("cotisations_patronales").checked,
      cotisation_ca_pct:              (parseFloat(document.getElementById("cotisation_ca_pct").value) || 0) / 100,
      taux_cotisations_patronales:    (parseFloat(document.getElementById("taux_cotisations_patronales").value) || 0) / 100,
      taux_taxe_salaires:             (parseFloat(document.getElementById("taux_taxe_salaires").value) || 0) / 100,
      heures_par_etp:                 parseFloat(document.getElementById("heures_par_etp").value) || 1582,
      dividendes_cibles:              parseFloat(document.getElementById("dividendes_cibles").value) || 0,
      profil_statut:                  window.PROFIL_STATUT || null,
    },
    charges_externes: [...document.querySelectorAll("#charges .row")].map(el => ({
      description: val(el, ".c-desc"), frequence: val(el, ".c-freq"), montant_unitaire: num(el, ".c-mont"),
    })),
    equipe: [...document.querySelectorAll("#equipe .row")].map(el => ({
      description: val(el, ".e-desc"), etp: num(el, ".e-etp"), remuneration_nette_mensuelle: num(el, ".e-net"),
    })),
    investissements: [...document.querySelectorAll("#invest .row")].map(el => ({
      description: val(el, ".i-desc"), duree_amortissement: num(el, ".i-duree"), montant_investi: num(el, ".i-mont"),
    })),
    offres: [...document.querySelectorAll("#offres .row")].map(el => {
      const o = { description: val(el, ".o-desc"), prix: num(el, ".o-prix"), cout_variable: num(el, ".o-cvar") };
      const volEls = el.querySelectorAll(".o-vol");
      if (volEls.length) {
        /* Pro : un volume par année. Case vide → null (le serveur auto-remplit
           depuis la croissance) ; un 0 explicite reste 0. */
        const volumes = [...volEls].map(i => {
          const t = i.value.trim();
          return t === "" ? null : (parseFloat(t) || 0);
        });
        o.volumes = volumes;
        o.quantite = volumes[0] ?? 0;   // rétro-compat : lecture mono-année
      } else {
        const q = num(el, ".o-qte");
        o.quantite = q;
        o.volumes = [q];
      }
      return o;
    }),
  };

  /* Champs Pro — uniquement pour les comptes connectés */
  if (window.MODEL_ID) {
    const g = (id) => document.getElementById(id);
    data.pro = {
      inclure_is:         g("inclure_is")?.checked         || false,
      croissance_ca:      parseFloat(g("croissance_ca")?.value)      ?? 10,
      croissance_charges: parseFloat(g("croissance_charges")?.value) ?? 3,
      sc_pessimiste:      parseFloat(g("sc_pessimiste")?.value)      ?? 0.6,
      sc_optimiste:       parseFloat(g("sc_optimiste")?.value)       ?? 1.5,
      acq_cac:            parseFloat(g("acq_cac")?.value)            || 0,
      acq_prix_mensuel:   parseFloat(g("acq_prix_mensuel")?.value)   || 0,
      acq_churn:          parseFloat(g("acq_churn")?.value)          || 0,
      acq_budget:         parseFloat(g("acq_budget")?.value)         || 0,
    };
  }
  return data;
}

/* ---------- Calcul & rendu ---------- */
function schedule() { clearTimeout(timer); timer = setTimeout(recalc, 250); }

/* Grise le taux de cotisations patronales quand la case n'est pas cochée :
   il ne s'applique pas (ex. micro-entrepreneuse seule), autant le montrer. */
function syncPatronalesUI() {
  const on   = document.getElementById("cotisations_patronales").checked;
  const taux = document.getElementById("taux_cotisations_patronales");
  if (taux) {
    taux.disabled = !on;
    taux.style.opacity = on ? "" : ".45";
  }
}

async function recalc() {
  syncPatronalesUI();
  const data = collect();
  let r;
  try {
    const res = await fetch("/api/calculer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    r = await res.json();
  } catch (e) { return; }
  const t = r.totaux, cr = r.compte_resultat;

  /* Verdict — en Pro, on lit la TRAJECTOIRE (un projet peut être déficitaire
     en An 1 d'amorçage et viable dès l'An 2). En gratuit, verdict de l'An 1. */
  const verdict = document.getElementById("verdict");
  const verdictTxt = document.getElementById("verdict-txt");
  const traj = (window.IS_PREMIUM && r.projection) ? r.projection.trajectoire : null;
  let positif;
  if (traj && traj.etat === "amorcage") {
    verdict.className = "verdict amorcage";
    verdictTxt.textContent = "🌱 En amorçage — viable dès l'An " + traj.premiere_annee_viable;
    positif = true;
  } else if (traj && traj.etat === "non_viable") {
    verdict.className = "verdict ko";
    verdictTxt.textContent = "Non viable ✕";
    positif = false;
  } else if (traj && traj.etat === "viable") {
    verdict.className = "verdict ok";
    verdictTxt.textContent = "Viable ✓";
    positif = true;
  } else {
    verdict.className = "verdict " + (t.viable ? "ok" : "ko");
    verdictTxt.textContent = t.viable ? "Viable ✓" : "Non viable ✕";
    positif = t.viable;
  }

  /* Bloc partage — visible dès que la trajectoire est positive */
  const partage = document.getElementById("partage");
  if (partage) partage.style.display = positif ? "" : "none";

  /* KPIs */
  document.getElementById("k-ca").textContent  = eur(t.ca_total);
  document.getElementById("k-mb").textContent  = eur(t.marge_brute_total);
  document.getElementById("k-cf").textContent  = eur(t.couts_fixes);
  const mn = document.getElementById("k-mn");
  mn.textContent = eur(t.marge_nette);
  mn.className   = "val " + (t.marge_nette >= 0 ? "pos" : "neg");
  document.getElementById("k-seuil").textContent = eur(t.seuil_ca);
  const divRow = document.getElementById("kpi-div-row");
  if (t.dividendes_cibles > 0) {
    divRow.style.display = "";
    document.getElementById("k-seuil-div").textContent = eur(t.seuil_ca_avec_dividendes);
  } else {
    divRow.style.display = "none";
  }
  document.getElementById("k-couv").textContent = pct(t.taux_couverture);

  /* Compte de résultat (+ IS si activé) */
  renderPL(cr, data.pro);

  /* Graphique revenus vs coûts */
  drawChart(t);

  /* Fonctions Pro — la projection et les scénarios sont désormais calculés
     côté serveur (source de vérité) à partir des volumes par année. */
  if (window.MODEL_ID && data.pro) {
    const p = data.pro;
    renderProjection(r.projection);
    renderScenarios(r.projection ? r.projection.scenarios : null);
    renderTresorerie(data, t);
    renderAcquisition(p.acq_cac, p.acq_prix_mensuel, p.acq_churn, p.acq_budget);
    syncVolumePlaceholders(p.croissance_ca);
  }
}

/* Suggestions grisées (placeholder) pour les volumes An 2 / An 3 laissés vides :
   ce que donnerait l'auto-remplissage au taux de croissance. Purement visuel —
   ces valeurs ne sont pas envoyées tant que l'utilisatrice ne les saisit pas. */
function syncVolumePlaceholders(croissanceCaPct) {
  const g = 1 + ((parseFloat(croissanceCaPct) || 0) / 100);
  document.querySelectorAll("#offres .row.offres.pro").forEach(row => {
    const inputs = row.querySelectorAll(".o-vol");
    if (inputs.length < 3) return;
    const valAt = (i) => { const t = inputs[i].value.trim(); return t === "" ? null : (parseFloat(t) || 0); };
    let prev = valAt(0) ?? 0;
    for (let i = 1; i < 3; i++) {
      const suggestion = Math.round(prev * g);
      if (valAt(i) === null) {
        inputs[i].placeholder = String(suggestion);
        prev = suggestion;                 // l'An 3 s'appuie sur l'An 2 suggéré
      } else {
        inputs[i].placeholder = "";
        prev = valAt(i);
      }
    }
  });
}

/* ---------- Graphique revenus vs coûts ---------- */
function drawChart(t) {
  const ctx = document.getElementById("chart");
  const cfg = {
    type: "bar",
    data: {
      labels: ["Revenus", "Coûts"],
      datasets: [
        { label: "CA / Coûts variables", data: [t.ca_total, t.couts_variables_total + t.cotisation_ca], backgroundColor: ["#1f9e78", "#e9b949"] },
        { label: "Coûts fixes",          data: [0,          t.couts_fixes],                             backgroundColor: ["#1f9e78", "#a06cd5"] },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: { x: { stacked: true }, y: { stacked: true, ticks: { callback: v => (v / 1000) + "k" } } },
      plugins: { legend: { display: true, position: "bottom" } },
    },
  };
  if (chart) { chart.data = cfg.data; chart.update(); } else { chart = new Chart(ctx, cfg); }
}

/* ================================================================
   FONCTIONS PRO
   ================================================================ */

/* ---------- IS (Impôt sur les bénéfices) ---------- */
function calcIS(profit) {
  if (profit <= 0) return 0;
  return Math.min(profit, 42500) * 0.15 + Math.max(0, profit - 42500) * 0.25;
}

function renderPL(cr, pro) {
  const is = (pro?.inclure_is) ? calcIS(cr.marge_nette) : 0;
  const resultatNet = cr.marge_nette - is;
  const labelAvantIS = pro?.inclure_is ? "= Résultat avant IS" : "= Marge nette";
  document.getElementById("pl").innerHTML = `
    <tr><td>Chiffre d'affaires</td><td>${eur(cr.chiffre_affaires)}</td></tr>
    <tr><td>− Coûts variables</td><td>${eur(-cr.couts_variables)}</td></tr>
    ${cr.cotisation_ca ? `<tr><td>− Cotisation sur CA</td><td>${eur(-cr.cotisation_ca)}</td></tr>` : ""}
    <tr class="total"><td>= Marge brute</td><td>${eur(cr.marge_brute)}</td></tr>
    <tr><td>− Charges externes</td><td>${eur(-cr.charges_externes)}</td></tr>
    <tr><td>− Rémunérations</td><td>${eur(-cr.remunerations)}</td></tr>
    <tr><td>− Amortissements</td><td>${eur(-cr.amortissements)}</td></tr>
    <tr class="total"><td>${labelAvantIS}</td><td>${eur(cr.marge_nette)}</td></tr>
    ${is > 0 ? `
      <tr><td>− IS estimé <small style="color:var(--gris)">(15 % / 25 %)</small></td><td>${eur(-is)}</td></tr>
      <tr class="total"><td>= Résultat net</td>
        <td style="color:${resultatNet >= 0 ? "var(--vert)" : "var(--rouge)"}">${eur(resultatNet)}</td>
      </tr>` : ""}`;
}

/* ---------- Projection 3 ans (données serveur) ---------- */
const _thL = `text-align:left;padding:5px 4px;border-bottom:2px solid var(--bord);font-size:.8rem;color:var(--gris)`;
const _thR = `text-align:right;padding:5px 4px;border-bottom:2px solid var(--bord);font-size:.8rem;color:var(--gris)`;
const _tdR = (v, colored) => {
  const style = colored
    ? `text-align:right;padding:6px 4px;color:${v >= 0 ? "var(--vert)" : "var(--rouge)"}`
    : "text-align:right;padding:6px 4px";
  return `<td style="${style}">${eur(v)}</td>`;
};

function renderProjection(projection) {
  const el = document.getElementById("proj-table");
  if (!el || !projection) return;
  const years = projection.annees || [];

  const badge = (v) => v
    ? `<span style="color:var(--vert);font-weight:700">✓</span>`
    : `<span style="color:var(--rouge);font-weight:700">✕</span>`;

  el.innerHTML = `
    <tr>
      <th style="${_thL}">Indicateur</th>
      ${years.map(y => `<th style="${_thR}">An ${y.annee}</th>`).join("")}
    </tr>
    <tr><td style="padding:6px 4px">CA annuel</td>${years.map(y => _tdR(y.ca)).join("")}</tr>
    <tr><td style="padding:6px 4px">Marge brute</td>${years.map(y => _tdR(y.marge_brute)).join("")}</tr>
    <tr><td style="padding:6px 4px">Charges</td>${years.map(y => _tdR(y.charges)).join("")}</tr>
    <tr class="total"><td style="padding:6px 4px">Marge nette</td>${years.map(y => _tdR(y.marge_nette, true)).join("")}</tr>
    <tr><td style="padding:6px 4px">Viable ?</td>${years.map(y =>
      `<td style="text-align:right;padding:6px 4px">${badge(y.viable)}</td>`
    ).join("")}</tr>`;

  const trEl = document.getElementById("proj-trajectoire");
  if (trEl) trEl.innerHTML = trajectoireHTML(projection.trajectoire);
}

/* Bandeau de verdict de trajectoire sous la projection */
function trajectoireHTML(tr) {
  if (!tr) return "";
  if (tr.etat === "viable")
    return `<div class="proj-traj ok">✓ <b>Viable dès l'An 1.</b></div>`;
  if (tr.etat === "amorcage")
    return `<div class="proj-traj amorcage">🌱 <b>En amorçage</b> — viable à partir de l'An ${tr.premiere_annee_viable}.
      Prévois <b>~${eur(tr.besoin_tresorerie)}</b> pour tenir le démarrage (apport, ARE/ARCE, prêt d'honneur…).
      C'est un profil de départ normal, pas un échec.</div>`;
  return `<div class="proj-traj ko">✕ <b>Non viable sur les 3 ans</b> — revois tes prix, tes volumes ou tes charges.</div>`;
}

/* ---------- Comparaison scénarios (données serveur) ---------- */
function renderScenarios(scenarios) {
  const el = document.getElementById("sc-table");
  if (!el || !scenarios) return;

  const order = [
    { key: "pessimiste", label: "Pessimiste", icon: "📉" },
    { key: "realiste",   label: "Réaliste",   icon: "⚖️" },
    { key: "optimiste",  label: "Optimiste",  icon: "📈" },
  ];
  const badge = (v) => v
    ? `<span style="color:var(--vert);font-weight:700">Viable ✓</span>`
    : `<span style="color:var(--rouge);font-weight:700">Non viable ✕</span>`;

  el.innerHTML = `
    <tr>
      <th style="${_thL}">Scénario</th>
      <th style="${_thR}">CA An 3</th>
      <th style="${_thR}">Marge nette An 3</th>
      <th style="${_thR}">An 3</th>
    </tr>
    ${order.map(s => {
      const sc = scenarios[s.key];
      if (!sc || !sc.annees || !sc.annees.length) return "";
      const c = sc.annees[sc.annees.length - 1];   // année de croisière (An 3)
      return `<tr>
        <td style="padding:6px 4px">${s.icon} ${s.label} <span style="color:var(--gris);font-size:.8rem">(×${sc.mult})</span></td>
        <td style="text-align:right;padding:6px 4px">${eur(c.ca)}</td>
        <td style="text-align:right;padding:6px 4px;font-weight:600;color:${c.marge_nette >= 0 ? "var(--vert)" : "var(--rouge)"}">${eur(c.marge_nette)}</td>
        <td style="text-align:right;padding:6px 4px">${badge(c.viable)}</td>
      </tr>`;
    }).join("")}`;
}

/* ---------- Trésorerie mensuelle ---------- */
function computeTresorerie(data, t) {
  const mois = Array.from({ length: 12 }, () => ({ revenus: 0, charges: 0 }));

  /* Revenus distribués uniformément */
  mois.forEach(m => m.revenus += t.ca_total / 12);

  /* Coûts variables et rémunérations : distribués uniformément */
  mois.forEach(m => {
    m.charges += t.total_remunerations   / 12;
    m.charges += t.couts_variables_total / 12;
    m.charges += t.total_amortissements  / 12;
  });

  /* Charges externes selon fréquence */
  (data.charges_externes || []).forEach(c => {
    const montant = parseFloat(c.montant_unitaire) || 0;
    const freq    = c.frequence;
    if      (freq === "mensuel")              { mois.forEach(m => m.charges += montant); }
    else if (freq === "hebdomadaire")         { mois.forEach(m => m.charges += montant * (52 / 12)); }
    else if (freq === "quotidien")            { mois.forEach(m => m.charges += montant * (365 / 12)); }
    else if (freq === "trimestriel")          { [2, 5, 8, 11].forEach(i => mois[i].charges += montant); }
    else if (freq === "semestriel")           { [5, 11].forEach(i => mois[i].charges += montant); }
    else if (freq === "annuel" || freq === "unique") { mois[0].charges += montant; }
  });

  let cumul = 0;
  return mois.map((m, i) => {
    const net = m.revenus - m.charges;
    cumul += net;
    return { i, revenus: m.revenus, charges: m.charges, net, cumul };
  });
}

function renderTresorerie(data, t) {
  const ctx = document.getElementById("chart-treso");
  if (!ctx) return;
  const treso = computeTresorerie(data, t);

  const datasets = [
    {
      label: "Résultat mensuel",
      data: treso.map(m => Math.round(m.net)),
      backgroundColor: treso.map(m => m.net >= 0 ? "rgba(31,158,120,.75)" : "rgba(214,69,80,.75)"),
      borderRadius: 4,
      order: 2,
    },
    {
      label: "Cumul",
      data: treso.map(m => Math.round(m.cumul)),
      type: "line",
      borderColor: "#6a2c91",
      backgroundColor: "transparent",
      borderWidth: 2,
      pointRadius: 3,
      fill: false,
      order: 1,
    },
  ];

  if (chartTreso) {
    chartTreso.data.labels   = NOMS_MOIS;
    chartTreso.data.datasets = datasets;
    chartTreso.update();
  } else {
    chartTreso = new Chart(ctx, {
      type: "bar",
      data: { labels: NOMS_MOIS, datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: { y: { ticks: { callback: v => eurK(v) } } },
        plugins: { legend: { display: true, position: "bottom" } },
      },
    });
  }
}

/* ---------- Acquisition & LTV ---------- */
function renderAcquisition(cac, prixMensuel, churnPct, budgetAcq) {
  const el      = document.getElementById("acq-results");
  const section = document.getElementById("section-acq");
  if (!el || !section) return;

  if (!cac && !churnPct && !budgetAcq && !prixMensuel) {
    section.style.display = "none";
    return;
  }
  section.style.display = "";

  const ltv           = (churnPct > 0 && prixMensuel > 0) ? prixMensuel / (churnPct / 100) : null;
  const ratioLTVCAC   = (ltv && cac > 0) ? ltv / cac : null;
  const payback       = (cac > 0 && prixMensuel > 0) ? Math.ceil(cac / prixMensuel) : null;
  const clientsAcquis = (cac > 0 && budgetAcq > 0) ? Math.round(budgetAcq / cac) : null;

  let ratioClass = "", ratioLabel = "—";
  if (ratioLTVCAC !== null) {
    ratioLabel = ratioLTVCAC.toFixed(1) + "×";
    ratioClass = ratioLTVCAC >= 3 ? "pos" : ratioLTVCAC >= 1 ? "" : "neg";
  }

  el.innerHTML = `
    ${ltv !== null ? `<div class="kpi"><span>LTV estimée</span><span class="val">${eur(ltv)}</span></div>` : ""}
    ${ratioLTVCAC !== null ? `
      <div class="kpi">
        <span>Ratio LTV / CAC <small style="color:var(--gris)">(idéal ≥ 3×)</small></span>
        <span class="val ${ratioClass}">${ratioLabel}</span>
      </div>` : ""}
    ${payback !== null ? `<div class="kpi"><span>Payback</span><span class="val">${payback} mois</span></div>` : ""}
    ${clientsAcquis !== null ? `
      <div class="kpi">
        <span>Clients acquis avec ${eur(budgetAcq)}/an</span>
        <span class="val">${clientsAcquis}</span>
      </div>` : ""}`;
}

/* ================================================================
   CHARGEMENT / SAUVEGARDE
   ================================================================ */
function load(data) {
  if (typeof data === "string") { try { data = JSON.parse(data); } catch (e) { data = {}; } }
  data = data || {};
  const p = data.parametres || {};

  window.PROFIL_STATUT = p.profil_statut || null;   // profil déjà choisi ? (persisté avec le modèle)

  if (p.collecte_tva !== undefined)              document.getElementById("collecte_tva").checked = p.collecte_tva;
  if (p.cotisations_patronales !== undefined)    document.getElementById("cotisations_patronales").checked = p.cotisations_patronales;
  if (p.cotisation_ca_pct !== undefined)         document.getElementById("cotisation_ca_pct").value = p.cotisation_ca_pct * 100;
  if (p.taux_cotisations_patronales !== undefined) document.getElementById("taux_cotisations_patronales").value = p.taux_cotisations_patronales * 100;
  if (p.taux_taxe_salaires !== undefined)        document.getElementById("taux_taxe_salaires").value = p.taux_taxe_salaires * 100;
  if (p.heures_par_etp !== undefined)            document.getElementById("heures_par_etp").value = p.heures_par_etp;
  if (p.dividendes_cibles !== undefined)         document.getElementById("dividendes_cibles").value = p.dividendes_cibles;

  (data.charges_externes || []).forEach(addCharge);
  (data.equipe            || []).forEach(addRole);
  (data.investissements   || []).forEach(addInvest);
  (data.offres            || []).forEach(addOffre);

  /* Restaurer les champs Pro */
  if (window.MODEL_ID && data.pro) {
    const pro = data.pro;
    const set   = (id, v) => { const el = document.getElementById(id); if (el && v != null) el.value = v; };
    const check = (id, v) => { const el = document.getElementById(id); if (el) el.checked = !!v; };
    check("inclure_is",          pro.inclure_is);
    set("croissance_ca",         pro.croissance_ca      ?? 10);
    set("croissance_charges",    pro.croissance_charges  ?? 3);
    set("sc_pessimiste",         pro.sc_pessimiste       ?? 0.6);
    set("sc_optimiste",          pro.sc_optimiste        ?? 1.5);
    if (pro.acq_cac)           set("acq_cac",           pro.acq_cac);
    if (pro.acq_prix_mensuel)  set("acq_prix_mensuel",  pro.acq_prix_mensuel);
    if (pro.acq_churn)         set("acq_churn",         pro.acq_churn);
    if (pro.acq_budget)        set("acq_budget",        pro.acq_budget);
  }

  /* Exemple par défaut si modèle vide */
  if (!(data.offres || []).length && !(data.charges_externes || []).length) {
    addCharge({ description: "Loyer & assurances", frequence: "mensuel", montant_unitaire: 400 });
    addRole({ description: "Moi (fondatrice)", etp: 1, remuneration_nette_mensuelle: 1800 });
    addOffre({ description: "Atelier", prix: 150, cout_variable: 30, quantite: 200 });
  }
}

async function sauvegarder() {
  if (!window.MODEL_ID) { location.href = "/inscription"; return; }
  await fetch("/api/modeles/" + window.MODEL_ID, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ data: collect() }),
  });
  const toast = document.getElementById("toast");
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 1800);
}

document.addEventListener("input",  (e) => { if (e.target.closest(".calc")) schedule(); });
document.addEventListener("change", (e) => { if (e.target.closest(".calc")) schedule(); });

/* Onboarding : le profil est la 1re chose à configurer. On ouvre l'assistant
   automatiquement tant qu'aucun statut n'a été choisi et qu'il n'a pas déjà
   été proposé sur cet appareil. */
function maybeOnboard() {
  if (window.PROFIL_STATUT) return;                 // profil déjà choisi (persisté)
  let seen = false;
  try { seen = !!localStorage.getItem("athena_profil_seen_" + (window.MODEL_ID || "decouverte")); } catch (e) {}
  if (seen) return;                                 // déjà proposé sur cet appareil
  if (typeof ouvrirWizard === "function") ouvrirWizard();
}

window.addEventListener("DOMContentLoaded", () => {
  load(window.MODEL_DATA);
  recalc();
  maybeOnboard();
});
