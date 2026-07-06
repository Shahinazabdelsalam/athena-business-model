/* Athena Business Model — logique du calculateur (frontend).
   La saisie est envoyée au moteur serveur (/api/calculer), source de vérité,
   qui renvoie l'analyse de viabilité. Les résultats sont recalculés à chaque
   modification (avec un léger debounce). */

const FREQUENCES = ["mensuel", "trimestriel", "semestriel", "annuel", "hebdomadaire", "quotidien", "unique"];
const eur = (n) => (n == null || isNaN(n)) ? "—" :
  new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(n);
const pct = (n) => (n == null || isNaN(n)) ? "—" : (n * 100).toFixed(1) + " %";

let chart = null;
let timer = null;

/* ---------- Construction des lignes ---------- */
function charge(d = {}) {
  return `<div class="row charges">
    <div><div class="col-label">Description</div><input class="c-desc" type="text" value="${d.description || ""}" placeholder="Loyer"></div>
    <div><div class="col-label">Fréquence</div><select class="c-freq">${FREQUENCES.map(f => `<option ${d.frequence === f ? "selected" : ""}>${f}</option>`).join("")}</select></div>
    <div><div class="col-label">Montant (HT)</div><input class="c-mont" type="number" step="10" value="${d.montant_unitaire ?? ""}"></div>
    <button class="mini-btn" onclick="this.parentElement.remove();schedule()">✕</button></div>`;
}
function role(d = {}) {
  return `<div class="row equipe">
    <div><div class="col-label">Rôle</div><input class="e-desc" type="text" value="${d.description || ""}" placeholder="Direction"></div>
    <div><div class="col-label">ETP <span class="tip" data-tip="Équivalent Temps Plein. 1 = plein temps, 0,5 = mi-temps. Toi à 80% = 0,8.">?</span></div><input class="e-etp" type="number" step="0.1" value="${d.etp ?? ""}"></div>
    <div><div class="col-label">Net mensuel/ETP</div><input class="e-net" type="number" step="50" value="${d.remuneration_nette_mensuelle ?? ""}"></div>
    <button class="mini-btn" onclick="this.parentElement.remove();schedule()">✕</button></div>`;
}
function invest(d = {}) {
  return `<div class="row invest">
    <div><div class="col-label">Description</div><input class="i-desc" type="text" value="${d.description || ""}" placeholder="Ordinateur"></div>
    <div><div class="col-label">Durée (ans)</div><input class="i-duree" type="number" step="1" value="${d.duree_amortissement ?? ""}"></div>
    <div><div class="col-label">Montant (HT)</div><input class="i-mont" type="number" step="50" value="${d.montant_investi ?? ""}"></div>
    <button class="mini-btn" onclick="this.parentElement.remove();schedule()">✕</button></div>`;
}
function offre(d = {}) {
  return `<div class="row offres">
    <div><div class="col-label">Offre</div><input class="o-desc" type="text" value="${d.description || ""}" placeholder="Atelier"></div>
    <div><div class="col-label">Prix (HT)</div><input class="o-prix" type="number" step="10" value="${d.prix ?? ""}"></div>
    <div><div class="col-label">Coût var. (HT)</div><input class="o-cvar" type="number" step="10" value="${d.cout_variable ?? ""}"></div>
    <div><div class="col-label">Nb / an</div><input class="o-qte" type="number" step="1" value="${d.quantite ?? ""}"></div>
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
  return {
    parametres: {
      collecte_tva: document.getElementById("collecte_tva").checked,
      cotisations_patronales: document.getElementById("cotisations_patronales").checked,
      cotisation_ca_pct: (parseFloat(document.getElementById("cotisation_ca_pct").value) || 0) / 100,
      taux_cotisations_patronales: (parseFloat(document.getElementById("taux_cotisations_patronales").value) || 0) / 100,
      taux_taxe_salaires: (parseFloat(document.getElementById("taux_taxe_salaires").value) || 0) / 100,
      heures_par_etp: parseFloat(document.getElementById("heures_par_etp").value) || 1582,
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
    offres: [...document.querySelectorAll("#offres .row")].map(el => ({
      description: val(el, ".o-desc"), prix: num(el, ".o-prix"), cout_variable: num(el, ".o-cvar"), quantite: num(el, ".o-qte"),
    })),
  };
}

/* ---------- Calcul & rendu ---------- */
function schedule() { clearTimeout(timer); timer = setTimeout(recalc, 250); }

async function recalc() {
  const data = collect();
  let r;
  try {
    const res = await fetch("/api/calculer", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
    r = await res.json();
  } catch (e) { return; }
  const t = r.totaux, cr = r.compte_resultat;

  const verdict = document.getElementById("verdict");
  verdict.className = "verdict " + (t.viable ? "ok" : "ko");
  document.getElementById("verdict-txt").textContent = t.viable ? "Viable ✓" : "Non viable ✕";

  document.getElementById("k-ca").textContent = eur(t.ca_total);
  document.getElementById("k-mb").textContent = eur(t.marge_brute_total);
  document.getElementById("k-cf").textContent = eur(t.couts_fixes);
  const mn = document.getElementById("k-mn");
  mn.textContent = eur(t.marge_nette);
  mn.className = "val " + (t.marge_nette >= 0 ? "pos" : "neg");
  document.getElementById("k-seuil").textContent = eur(t.seuil_ca);
  document.getElementById("k-couv").textContent = pct(t.taux_couverture);

  document.getElementById("pl").innerHTML = `
    <tr><td>Chiffre d'affaires</td><td>${eur(cr.chiffre_affaires)}</td></tr>
    <tr><td>− Coûts variables</td><td>${eur(-cr.couts_variables)}</td></tr>
    ${cr.cotisation_ca ? `<tr><td>− Cotisation sur CA</td><td>${eur(-cr.cotisation_ca)}</td></tr>` : ""}
    <tr class="total"><td>= Marge brute</td><td>${eur(cr.marge_brute)}</td></tr>
    <tr><td>− Charges externes</td><td>${eur(-cr.charges_externes)}</td></tr>
    <tr><td>− Rémunérations</td><td>${eur(-cr.remunerations)}</td></tr>
    <tr><td>− Amortissements</td><td>${eur(-cr.amortissements)}</td></tr>
    <tr class="total"><td>= Marge nette</td><td>${eur(cr.marge_nette)}</td></tr>`;

  drawChart(t);
}

function drawChart(t) {
  const ctx = document.getElementById("chart");
  const revenus = t.ca_total;
  const coutsTot = t.couts_variables_total + t.cotisation_ca + t.couts_fixes;
  const cfg = {
    type: "bar",
    data: {
      labels: ["Revenus", "Coûts"],
      datasets: [
        { label: "CA / Coûts variables", data: [revenus, t.couts_variables_total + t.cotisation_ca], backgroundColor: ["#1f9e78", "#e9b949"] },
        { label: "Coûts fixes", data: [0, t.couts_fixes], backgroundColor: ["#1f9e78", "#a06cd5"] },
      ],
    },
    options: { responsive: true, maintainAspectRatio: false,
      scales: { x: { stacked: true }, y: { stacked: true, ticks: { callback: v => (v / 1000) + "k" } } },
      plugins: { legend: { display: true, position: "bottom" } } },
  };
  if (chart) { chart.data = cfg.data; chart.update(); } else { chart = new Chart(ctx, cfg); }
}

/* ---------- Chargement / sauvegarde ---------- */
function load(data) {
  if (typeof data === "string") { try { data = JSON.parse(data); } catch (e) { data = {}; } }
  data = data || {};
  const p = data.parametres || {};
  if (p.collecte_tva !== undefined) document.getElementById("collecte_tva").checked = p.collecte_tva;
  if (p.cotisations_patronales !== undefined) document.getElementById("cotisations_patronales").checked = p.cotisations_patronales;
  if (p.cotisation_ca_pct !== undefined) document.getElementById("cotisation_ca_pct").value = p.cotisation_ca_pct * 100;
  if (p.taux_cotisations_patronales !== undefined) document.getElementById("taux_cotisations_patronales").value = p.taux_cotisations_patronales * 100;
  if (p.taux_taxe_salaires !== undefined) document.getElementById("taux_taxe_salaires").value = p.taux_taxe_salaires * 100;
  if (p.heures_par_etp !== undefined) document.getElementById("heures_par_etp").value = p.heures_par_etp;

  (data.charges_externes || []).forEach(addCharge);
  (data.equipe || []).forEach(addRole);
  (data.investissements || []).forEach(addInvest);
  (data.offres || []).forEach(addOffre);

  if (!(data.offres || []).length && !(data.charges_externes || []).length) {
    addCharge({ description: "Loyer & assurances", frequence: "mensuel", montant_unitaire: 400 });
    addRole({ description: "Moi (fondatrice)", etp: 1, remuneration_nette_mensuelle: 1800 });
    addOffre({ description: "Atelier", prix: 150, cout_variable: 30, quantite: 200 });
  }
}

async function sauvegarder() {
  if (!window.MODEL_ID) { location.href = "/inscription"; return; }
  await fetch("/api/modeles/" + window.MODEL_ID, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ data: collect() }),
  });
  const toast = document.getElementById("toast");
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 1800);
}

document.addEventListener("input", (e) => { if (e.target.closest(".calc")) schedule(); });
document.addEventListener("change", (e) => { if (e.target.closest(".calc")) schedule(); });
window.addEventListener("DOMContentLoaded", () => { load(window.MODEL_DATA); recalc(); });
