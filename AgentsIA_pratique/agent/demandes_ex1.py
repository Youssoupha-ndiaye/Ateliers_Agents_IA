"""10 demandes de test des exercices 1 et 2."""

DEMANDES = [
    "Je cherche un F3 à Mermoz à moins de 400 000 F.",
    "Un studio aux Almadies pour 150 000 F maximum ?",
    "Avez-vous une villa à Ngor ?",
    "Je veux un F2 à Sacré-Cœur, budget 300 000 F.",
    "Un appartement meublé au Plateau ?",
    "F4 à Ouakam à moins de 450 000 F CFA",
    "Je cherche quelque chose à Rufisque.",
    "Un F5 à Fann pas trop cher",
    "Un F3 à Point E à moins de 100 000 F",
    "Donnez-moi les détails du bien SK-011",
]

SYS = ("Tu es l'assistant de l'agence Sama Kër Immobilier. Réponds en français. "
       "Utilise TOUJOURS rechercher_biens/detail_bien ; ne cite que des biens renvoyés par les outils (id, loyer, adresse). "
       "Si le résultat est vide ou en erreur de quartier, dis clairement qu'aucun bien ne correspond. N'invente rien.")


