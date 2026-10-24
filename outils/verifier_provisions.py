"""Recalcule toute la chaîne actuarielle sans IA, puis la compare au moteur de l'agent.

Ce script est une seconde implémentation, écrite indépendamment du moteur
agent/moteur_actuariel.py, des articles 2, 3 et 4 de la politique de provisionnement :
Chain-Ladder pondéré par les volumes, erreur de Mack (1993), Bornhuetter-Ferguson,
best estimate actualisé, SCR de réserve, marge de risque et ratio de solvabilité.
Il produit les réponses de référence de l'évaluation, puis vérifie que le moteur
donne exactement les mêmes résultats, à l'euro près.

Utilisation, depuis la racine du dépôt :
    pip install -r outils/requirements.txt
    python outils/verifier_provisions.py
"""
from pathlib import Path
import os

import numpy as np
import pandas as pd

RACINE = Path(__file__).resolve().parent.parent
DONNEES = RACINE / "donnees"
SP_A_PRIORI, SIGMA, COUT_CAPITAL, FONDS_PROPRES = 0.65, 0.10, 0.06, 17_500_000


def euros(x):
    return f"{x:,.0f} €".replace(",", " ")


def pct(x):
    return f"{x:.2f} %".replace(".", ",")


triangle = pd.read_csv(DONNEES / "triangle_charges_payees.csv")
primes = pd.read_csv(DONNEES / "primes_acquises.csv")["primes_acquises_eur"].to_numpy(float)
z = pd.read_csv(DONNEES / "courbe_taux.csv")["taux_zero_coupon_pct"].to_numpy(float) / 100
C = triangle.iloc[:, 1:].to_numpy(float)
n = len(C)
annees = triangle["annee_survenance"].tolist()

# Article 2 : Chain-Ladder pondéré par les volumes, sans facteur de queue
derniers = np.array([C[i, n - 1 - i] for i in range(n)])
f = np.array([C[: n - 1 - j, j + 1].sum() / C[: n - 1 - j, j].sum() for j in range(n - 1)])
reste = np.array([np.prod(f[n - 1 - i:]) for i in range(n)])        # facteur cumulé restant
ultimes = derniers * reste
prov_cl = ultimes - derniers

# Triangle complété par les facteurs
P = C.copy()
for i in range(n):
    for j in range(n - i, n):
        P[i, j] = P[i, j - 1] * f[j - 1]

# Article 2 : erreur de prédiction de Mack, covariance entre années comprise
s2 = np.array([
    sum(C[i, j] * (C[i, j + 1] / C[i, j] - f[j]) ** 2 for i in range(n - 1 - j)) / (n - 2 - j)
    for j in range(n - 2)
])
s2 = np.append(s2, min(s2[-1] ** 2 / s2[-2], s2[-2], s2[-1]))
volumes = np.array([C[: n - 1 - k, k].sum() for k in range(n - 1)])
terme = lambda i: [k for k in range(n - 1 - i, n - 1)]
mse = [0.0] + [P[i, -1] ** 2 * sum(s2[k] / f[k] ** 2 * (1 / P[i, k] + 1 / volumes[k]) for k in terme(i))
               for i in range(1, n)]
covariances = sum(P[i, -1] * P[l, -1] * sum(2 * s2[k] / f[k] ** 2 / volumes[k] for k in terme(i))
                  for i in range(1, n) for l in range(i + 1, n))
mack = np.sqrt(sum(mse) + covariances)

# Article 2 : Bornhuetter-Ferguson pour 2024 et 2025, provision retenue
prov_bf = primes * SP_A_PRIORI * (1 - 1 / reste)
retenue = np.where(np.arange(n) >= n - 2, prov_bf, prov_cl)

# Article 3 : flux futurs par année calendaire, best estimate, SCR, marge de risque
flux = np.zeros(n - 1)
for i in range(1, n):
    increments = np.diff(P[i, n - 1 - i:])
    flux[: len(increments)] += retenue[i] * increments / increments.sum()
valeur = lambda depart: sum(flux[depart + k] * (1 + z[k]) ** -(k + 0.5) for k in range(n - 1 - depart))
be_restant = np.array([valeur(t) for t in range(n - 1)])
best_estimate = be_restant[0]
scr = 3 * SIGMA * best_estimate
marge = COUT_CAPITAL * sum(scr * be_restant[t] / best_estimate * (1 + z[t]) ** -(t + 1) for t in range(n - 1))
ratio = FONDS_PROPRES / scr * 100

reference = {
    "Provision Chain-Ladder": prov_cl.sum(),
    "Charge ultime Chain-Ladder": ultimes.sum(),
    "Erreur de Mack": mack,
    "Provision BF 2024": prov_bf[-2],
    "Provision BF 2025": prov_bf[-1],
    "Provision retenue": retenue.sum(),
    "Best estimate": best_estimate,
    "SCR de réserve": scr,
    "Marge de risque": marge,
    "Provisions techniques": best_estimate + marge,
    "Manque pour 150 %": 1.5 * scr - FONDS_PROPRES,
}

print("=" * 76)
print("MUTUELLE LORRAINE ASSURANCES · dommages aux biens · données au 31/12/2025")
print("=" * 76)
print("Facteurs de développement : " + " ; ".join(f"{x:.4f}".replace(".", ",") for x in f))
print(f"\n{'Survenance':<11}{'Paiements':>15}{'Ultime CL':>15}{'S/P ultime':>12}{'Prov. CL':>14}{'Retenue':>14}")
for i, a in enumerate(annees):
    print(f"{a:<11}{euros(derniers[i]):>15}{euros(ultimes[i]):>15}{pct(ultimes[i] / primes[i] * 100):>12}"
          f"{euros(prov_cl[i]):>14}{euros(retenue[i]):>14}")
print(f"\nCoefficient de variation de Mack : {pct(mack / prov_cl.sum() * 100)}")
print(f"Écart Chain-Ladder / BF 2025 : {pct((prov_cl[-1] / prov_bf[-1] - 1) * 100)}")
print(f"Ratio de solvabilité : {pct(ratio)}")
print("\nINDICATEUR                      RÉFÉRENCE")
for nom, v in reference.items():
    print(f"  {nom:<28}{euros(v):>16}")

# Contrôle croisé avec le moteur de l'agent
print("\nCONTRÔLE CROISÉ AVEC agent/moteur_actuariel.py")
os.chdir(DONNEES)
moteur = {}
exec((RACINE / "agent" / "moteur_actuariel.py").read_text(encoding="utf-8"), moteur)
t = moteur["calculer"]()["totaux"]
pairs = {
    "Provision Chain-Ladder": t["provision_cl"], "Charge ultime Chain-Ladder": t["ultime_cl"],
    "Erreur de Mack": t["mack"], "Provision retenue": t["provision_retenue"],
    "Best estimate": t["best_estimate"], "SCR de réserve": t["scr"], "Marge de risque": t["marge_de_risque"],
    "Provisions techniques": t["provisions_techniques"], "Manque pour 150 %": t["manque_pour_150"],
}
ecarts = {nom: abs(round(v) - round(reference[nom])) for nom, v in pairs.items()}
for nom, e in ecarts.items():
    print(f"  {'✓' if e == 0 else '✗'} {nom:<28} écart {e} €")
print(f"\n{sum(e == 0 for e in ecarts.values())} indicateurs sur {len(ecarts)} identiques à l'euro près.")
