"""Génère les trois fichiers de données de la Mutuelle Lorraine Assurances (mutuelle fictive).

Branche dommages aux biens des particuliers, survenances 2016 à 2025 :
- triangle_charges_payees.csv : paiements cumulés, triangle 10 × 10 en euros ;
- primes_acquises.csv : primes acquises et S/P a priori de 65 % ;
- courbe_taux.csv : courbe des taux zéro-coupon sans risque, maturités 1 à 15 ans.

Chaque survenance reçoit une charge ultime tirée autour d'un S/P réaliste, puis se
développe selon une cadence de paiement typique de l'assurance habitation (46 % payés
la première année, 99 % après huit ans), avec un bruit plus fort sur les premières
années de développement. La graine est fixée : chaque exécution reproduit exactement
les fichiers du dossier donnees/.

Utilisation, depuis la racine du dépôt :
    python outils/generer_donnees.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

DOSSIER = Path(__file__).resolve().parent.parent / "donnees"
generateur = np.random.default_rng(2410)

ANNEES = list(range(2016, 2026))
N = len(ANNEES)
PRIMES = np.array([38.2, 39.5, 40.9, 42.0, 43.8, 45.1, 47.3, 49.6, 52.4, 55.0]) * 1e6
SP_ULTIME = np.array([0.64, 0.67, 0.62, 0.70, 0.66, 0.63, 0.69, 0.71, 0.66, 0.60])
CADENCE = np.array([0.46, 0.73, 0.85, 0.915, 0.952, 0.974, 0.987, 0.994, 0.998, 1.0])

# 1. Triangle des paiements cumulés, arrondis au millier d'euros et toujours croissants
cumules = np.full((N, N), np.nan)
for i in range(N):
    ultime = PRIMES[i] * SP_ULTIME[i] * generateur.normal(1, 0.03)
    precedent = 0.0
    for j in range(N - i):
        cible = ultime * CADENCE[j] * generateur.normal(1, 0.012 if j < 4 else 0.004)
        valeur = max(cible, precedent * 1.0005)
        cumules[i, j] = round(valeur / 1000) * 1000
        precedent = cumules[i, j]
triangle = pd.DataFrame(cumules, columns=[f"dev_{j + 1}" for j in range(N)])
triangle.insert(0, "annee_survenance", ANNEES)
triangle.to_csv(DOSSIER / "triangle_charges_payees.csv", index=False, float_format="%.0f")

# 2. Primes acquises et S/P a priori de la tarification
pd.DataFrame({
    "annee_survenance": ANNEES,
    "primes_acquises_eur": PRIMES.round(0).astype(int),
    "sp_a_priori_pct": 65.0,
}).to_csv(DOSSIER / "primes_acquises.csv", index=False)

# 3. Courbe des taux zéro-coupon, forme de Nelson-Siegel simplifiée
maturites = np.arange(1, 16)
taux = np.round(1.90 + 0.95 * (1 - np.exp(-maturites / 6.0)) + 0.004 * maturites, 3)
pd.DataFrame({"maturite_annees": maturites, "taux_zero_coupon_pct": taux}).to_csv(
    DOSSIER / "courbe_taux.csv", index=False)

print(f"Fichiers écrits dans {DOSSIER}")
print(triangle.to_string(index=False))
