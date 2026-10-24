"""Moteur actuariel de ReservePilot : la méthode de la politique, codée une fois pour toutes.

Ce module applique exactement les articles 2, 3 et 4 de la Politique_Provisionnement_MLA :
Chain-Ladder pondéré par les volumes, erreur de Mack (1993), Bornhuetter-Ferguson pour
2024 et 2025, flux futurs, best estimate actualisé en milieu d'année, SCR de réserve,
marge de risque au coût du capital, ratio de solvabilité et statuts des limites.

Utilisation dans l'interpréteur de code de l'agent :
    import glob
    exec(open(glob.glob("/mnt/data/*moteur_actuariel.py")[0]).read())
    r = calculer()                     # hypothèses de la politique
    afficher(r)                        # tous les résultats, en français
    s = calculer(sp_a_priori=0.62)     # simulation, sans rien modifier
    exporter_excel(r, "/mnt/data/provisions_MLA.xlsx")

Utilisation locale, depuis la racine du dépôt :
    python agent/moteur_actuariel.py
"""
import glob
import os

import numpy as np
import pandas as pd

SP_A_PRIORI = 0.65          # article 2
SIGMA_RESERVE = 0.10        # article 3
COUT_DU_CAPITAL = 0.06      # article 3
FONDS_PROPRES = 17_500_000  # article 3
ANNEES_BF = 2               # article 2 : les deux dernières survenances
LIMITES = {                 # article 4 : (seuil d'alerte, limite)
    "cv_mack": (7.5, 10.0),
    "sp_ultime": (72.0, 75.0),
    "ecart_cl_bf": (10.0, 20.0),
    "ratio_solvabilite": (150.0, 130.0),
}


def _trouver(nom):
    """Retrouve un fichier, même renommé par Foundry (préfixe assistant-...)."""
    for motif in (f"/mnt/data/*{nom}", f"/mnt/data/**/*{nom}", nom, f"*{nom}"):
        trouves = glob.glob(motif, recursive=True)
        if trouves:
            return trouves[0]
    raise FileNotFoundError(f"Fichier introuvable : {nom}")


def statut(valeur, alerte, limite, vers_le_bas=False):
    """Statuts de l'article 4. Pour le ratio de solvabilité, les seuils s'appliquent vers le bas."""
    if vers_le_bas:
        if valeur < limite:
            return "DÉPASSEMENT"
        if valeur < alerte:
            return "ALERTE"
        return "CONFORME"
    if valeur > limite:
        return "DÉPASSEMENT"
    if valeur >= alerte:
        return "ALERTE"
    return "CONFORME"


def calculer(sp_a_priori=SP_A_PRIORI, fonds_propres=FONDS_PROPRES):
    """Enchaîne toute la chaîne actuarielle et renvoie un dictionnaire de résultats."""
    triangle = pd.read_csv(_trouver("triangle_charges_payees.csv"))
    primes_df = pd.read_csv(_trouver("primes_acquises.csv"))
    courbe = pd.read_csv(_trouver("courbe_taux.csv"))

    annees = triangle["annee_survenance"].astype(int).tolist()
    C = triangle.drop(columns="annee_survenance").to_numpy(dtype=float)
    n = C.shape[0]
    primes = primes_df.set_index("annee_survenance").loc[annees, "primes_acquises_eur"].to_numpy(dtype=float)
    taux = courbe.sort_values(courbe.columns[0])["taux_zero_coupon_pct"].to_numpy(dtype=float) / 100

    # Article 2 : dernière diagonale connue et facteurs pondérés par les volumes.
    # Seules les survenances qui disposent des deux colonnes entrent dans le facteur.
    diagonale = np.array([C[i, n - 1 - i] for i in range(n)])
    f = np.array([C[: n - 1 - j, j + 1].sum() / C[: n - 1 - j, j].sum() for j in range(n - 1)])
    facteur_cumule = np.array([np.prod(f[j:]) for j in range(n)])
    fc_annee = np.array([facteur_cumule[n - 1 - i] for i in range(n)])
    ultime = diagonale * fc_annee
    prov_cl = ultime - diagonale

    # Triangle complété par Chain-Ladder
    F = C.copy()
    for i in range(n):
        for j in range(n - i, n):
            F[i, j] = F[i, j - 1] * f[j - 1]

    # Article 2 : erreur de Mack (1993), covariance comprise, dernier sigma² extrapolé
    sig2 = np.zeros(n - 1)
    for j in range(n - 2):
        m = n - 1 - j
        sig2[j] = sum(C[i, j] * (C[i, j + 1] / C[i, j] - f[j]) ** 2 for i in range(m)) / (m - 1)
    sig2[n - 2] = min(sig2[n - 3] ** 2 / sig2[n - 4], sig2[n - 4], sig2[n - 3])
    somme_col = np.array([C[: n - 1 - k, k].sum() for k in range(n - 1)])
    mse = np.zeros(n)
    for i in range(1, n):
        mse[i] = F[i, n - 1] ** 2 * sum(
            sig2[k] / f[k] ** 2 * (1 / F[i, k] + 1 / somme_col[k]) for k in range(n - 1 - i, n - 1))
    mse_total = mse.sum()
    for i in range(1, n):
        for l in range(i + 1, n):
            mse_total += F[i, n - 1] * F[l, n - 1] * sum(
                2 * sig2[k] / f[k] ** 2 / somme_col[k] for k in range(n - 1 - i, n - 1))
    mack = np.sqrt(mse)
    mack_total = float(np.sqrt(mse_total))
    cv = mack_total / prov_cl.sum() * 100

    # Article 2 : Bornhuetter-Ferguson pour les deux dernières survenances
    prov_bf = primes * sp_a_priori * (1 - 1 / fc_annee)
    retenue = prov_cl.copy()
    retenue[-ANNEES_BF:] = prov_bf[-ANNEES_BF:]
    methode = ["Chain-Ladder"] * (n - ANNEES_BF) + ["Bornhuetter-Ferguson"] * ANNEES_BF

    # Article 3 : flux futurs par année calendaire, selon la cadence Chain-Ladder
    flux = np.zeros(n - 1)
    for i in range(1, n):
        increments = np.array([F[i, j] - F[i, j - 1] for j in range(n - i, n)])
        flux[: len(increments)] += increments / increments.sum() * retenue[i]

    # Article 3 : best estimate actualisé en milieu d'année, année 1 = 2026
    actualisation = np.array([(1 + taux[k]) ** -(k + 0.5) for k in range(n - 1)])
    best_estimate = float((flux * actualisation).sum())
    scr = 3 * SIGMA_RESERVE * best_estimate

    # Article 3 : marge de risque, SCR proportionnel au best estimate restant
    be_t = np.array([sum(flux[t + k] * (1 + taux[k]) ** -(k + 0.5) for k in range(n - 1 - t))
                     for t in range(n - 1)])
    scr_t = scr * be_t / be_t[0]
    marge = COUT_DU_CAPITAL * sum(scr_t[t] * (1 + taux[t]) ** -(t + 1) for t in range(n - 1))
    provisions_techniques = best_estimate + marge
    ratio = fonds_propres / scr * 100

    # Article 4 : limites et statuts
    sp_ultime = ultime / primes * 100
    ecart = (prov_cl[-1] / prov_bf[-1] - 1) * 100
    a, l = LIMITES["sp_ultime"]
    statuts_sp = [statut(x, a, l) for x in sp_ultime]
    pire_sp = max(range(n), key=lambda i: sp_ultime[i])

    return {
        "annees": annees,
        "primes": primes,
        "paiements": diagonale,
        "facteurs": f,
        "facteur_cumule": fc_annee,
        "ultime_cl": ultime,
        "provision_cl": prov_cl,
        "provision_bf": prov_bf,
        "provision_retenue": retenue,
        "methode": methode,
        "mack_par_annee": mack,
        "sp_ultime": sp_ultime,
        "statut_sp": statuts_sp,
        "totaux": {
            "paiements": float(diagonale.sum()),
            "ultime_cl": float(ultime.sum()),
            "provision_cl": float(prov_cl.sum()),
            "provision_retenue": float(retenue.sum()),
            "mack": mack_total,
            "cv_mack_pct": cv,
            "ecart_cl_bf_derniere_pct": ecart,
            "best_estimate": best_estimate,
            "effet_actualisation": float(retenue.sum() - best_estimate),
            "scr": scr,
            "marge_de_risque": marge,
            "marge_pct_be": marge / best_estimate * 100,
            "provisions_techniques": provisions_techniques,
            "fonds_propres": fonds_propres,
            "ratio_solvabilite_pct": ratio,
            "fonds_propres_pour_150": 1.5 * scr,
            "manque_pour_150": max(1.5 * scr - fonds_propres, 0.0),
        },
        "flux_par_annee": dict(zip(range(annees[-1] + 1, annees[-1] + n), flux)),
        "statuts": {
            "cv_mack": statut(cv, *LIMITES["cv_mack"]),
            "sp_ultime_pire": statuts_sp[pire_sp],
            "ecart_cl_bf": statut(abs(ecart), *LIMITES["ecart_cl_bf"]),
            "ratio_solvabilite": statut(ratio, *LIMITES["ratio_solvabilite"], vers_le_bas=True),
        },
        "hypotheses": {"sp_a_priori": sp_a_priori, "fonds_propres": fonds_propres},
    }


def euros(x):
    return f"{x:,.0f} €".replace(",", " ")


def pct(x):
    return f"{x:.2f} %".replace(".", ",")


def afficher(r):
    """Affiche tous les résultats au format de la politique."""
    t, s = r["totaux"], r["statuts"]
    print("FACTEURS DE DÉVELOPPEMENT : " + " ; ".join(f"{x:.4f}".replace(".", ",") for x in r["facteurs"]))
    print("\nSURVENANCE | PAIEMENTS | ULTIME CL | S/P ULTIME | PROVISION CL | PROVISION BF | RETENUE | MÉTHODE | ERREUR MACK")
    for i, a in enumerate(r["annees"]):
        bf = euros(r["provision_bf"][i]) if r["methode"][i] != "Chain-Ladder" else "-"
        print(f"{a} | {euros(r['paiements'][i])} | {euros(r['ultime_cl'][i])} | {pct(r['sp_ultime'][i])} {r['statut_sp'][i]} | "
              f"{euros(r['provision_cl'][i])} | {bf} | {euros(r['provision_retenue'][i])} | {r['methode'][i]} | {euros(r['mack_par_annee'][i])}")
    print(f"\nTotal paiements : {euros(t['paiements'])} · ultime CL : {euros(t['ultime_cl'])}")
    print(f"Provision Chain-Ladder : {euros(t['provision_cl'])}")
    print(f"Erreur de Mack : {euros(t['mack'])} · CV {pct(t['cv_mack_pct'])} · {s['cv_mack']}")
    print(f"Écart CL / BF dernière survenance : {pct(t['ecart_cl_bf_derniere_pct'])} · {s['ecart_cl_bf']}")
    print(f"Provision retenue : {euros(t['provision_retenue'])}")
    print(f"Best estimate : {euros(t['best_estimate'])} · effet d'actualisation {euros(t['effet_actualisation'])}")
    print(f"SCR de réserve : {euros(t['scr'])}")
    print(f"Marge de risque : {euros(t['marge_de_risque'])} ({pct(t['marge_pct_be'])} du best estimate)")
    print(f"Provisions techniques : {euros(t['provisions_techniques'])}")
    print(f"Ratio de solvabilité : {pct(t['ratio_solvabilite_pct'])} · {s['ratio_solvabilite']}")
    print(f"Fonds propres requis pour 150 % : {euros(t['fonds_propres_pour_150'])} · manque {euros(t['manque_pour_150'])}")
    print("Flux futurs : " + " ; ".join(f"{a} {euros(v)}" for a, v in r["flux_par_annee"].items()))
    print(f"Hypothèses : S/P a priori {pct(r['hypotheses']['sp_a_priori'] * 100)} · fonds propres {euros(r['hypotheses']['fonds_propres'])}")

    print("\nTABLEAU DES LIMITES (article 4)")
    print("INDICATEUR | VALEUR | SEUIL D'ALERTE | LIMITE | STATUT")
    pires = [f"{a} : {pct(v)}" for a, v, st in zip(r["annees"], r["sp_ultime"], r["statut_sp"]) if st != "CONFORME"]
    lignes = [
        ("Coefficient de variation de Mack", pct(t["cv_mack_pct"]), "7,50 %", "10,00 %", s["cv_mack"]),
        ("S/P ultime Chain-Ladder par survenance", " ; ".join(pires) or f"max {pct(max(r['sp_ultime']))}", "72,00 %", "75,00 %", s["sp_ultime_pire"]),
        ("Écart CL / BF dernière survenance", pct(t["ecart_cl_bf_derniere_pct"]), "10,00 %", "20,00 %", s["ecart_cl_bf"]),
        ("Ratio de solvabilité", pct(t["ratio_solvabilite_pct"]), "inférieur à 150 %", "inférieur à 130 %", s["ratio_solvabilite"]),
    ]
    for ligne in lignes:
        print(" | ".join(ligne))
    print("\nACTIONS REQUISES (article 5)")
    actions = []
    if s["sp_ultime_pire"] == "ALERTE":
        actions.append("S/P ultime en ALERTE (" + " ; ".join(pires) + ") : analyse des sinistres graves et de la tarification de l'année concernée par la fonction actuarielle sous 15 jours, puis présentation au Comité technique.")
    if s["ecart_cl_bf"] == "ALERTE":
        actions.append("Écart CL / BF en ALERTE : revue du S/P a priori de l'article 2 avant la prochaine clôture ; la provision Bornhuetter-Ferguson reste retenue dans l'attente.")
    if s["ratio_solvabilite"] == "ALERTE":
        actions.append("Ratio de solvabilité en ALERTE : information du Directeur général et du Comité des risques sous 5 jours ouvrés ; plan de rétablissement au-dessus de 150 % présenté au Conseil d'administration ; mise à jour de l'ORSA.")
    if "DÉPASSEMENT" in s.values():
        actions.append("DÉPASSEMENT : information immédiate du Directeur général ; Conseil d'administration extraordinaire sous 10 jours ; notification à l'ACPR si le ratio de solvabilité passe sous 100 %.")
    for k, a in enumerate(actions or ["Aucune action d'escalade requise."], 1):
        print(f"{k}. {a}")


def exporter_excel(r, chemin="/mnt/data/provisions_MLA.xlsx"):
    """Écrit le fichier Excel des provisions par année de survenance et la synthèse Solvabilité II."""
    detail = pd.DataFrame({
        "Année de survenance": r["annees"],
        "Primes acquises (€)": r["primes"].round(0),
        "Paiements cumulés (€)": r["paiements"].round(0),
        "Charge ultime CL (€)": r["ultime_cl"].round(0),
        "S/P ultime (%)": r["sp_ultime"].round(2),
        "Statut S/P": r["statut_sp"],
        "Provision CL (€)": r["provision_cl"].round(0),
        "Provision BF (€)": [round(v) if m != "Chain-Ladder" else None for v, m in zip(r["provision_bf"], r["methode"])],
        "Provision retenue (€)": r["provision_retenue"].round(0),
        "Méthode": r["methode"],
        "Erreur de Mack (€)": r["mack_par_annee"].round(0),
    })
    t = r["totaux"]
    synthese = pd.DataFrame({
        "Indicateur": ["Provision Chain-Ladder", "Erreur de Mack", "Coefficient de variation (%)", "Provision retenue",
                       "Best estimate", "Effet d'actualisation", "SCR de réserve", "Marge de risque",
                       "Provisions techniques", "Fonds propres éligibles", "Ratio de solvabilité (%)"],
        "Valeur": [round(t["provision_cl"]), round(t["mack"]), round(t["cv_mack_pct"], 2), round(t["provision_retenue"]),
                   round(t["best_estimate"]), round(t["effet_actualisation"]), round(t["scr"]), round(t["marge_de_risque"]),
                   round(t["provisions_techniques"]), round(t["fonds_propres"]), round(t["ratio_solvabilite_pct"], 2)],
    })
    with pd.ExcelWriter(chemin) as classeur:
        detail.to_excel(classeur, sheet_name="Provisions", index=False)
        synthese.to_excel(classeur, sheet_name="Solvabilité II", index=False)
    return chemin


if __name__ == "__main__" and "__file__" in globals():
    ici = os.path.dirname(os.path.abspath(__file__))
    donnees = os.path.join(ici, "..", "donnees")
    os.chdir(donnees if os.path.isdir(donnees) else ici)
    afficher(calculer())
    print("\nSIMULATION S/P A PRIORI 62 %")
    s = calculer(sp_a_priori=0.62)["totaux"]
    print(f"Provision retenue {euros(s['provision_retenue'])} · ratio {pct(s['ratio_solvabilite_pct'])}")
