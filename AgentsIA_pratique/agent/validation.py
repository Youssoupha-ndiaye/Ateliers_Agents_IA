"""Validation humaine obligatoire (exigence non négociable de l'agence).
Toute action d'écriture (envoi d'email, création/déplacement/annulation de visite)
passe par demander(). Mode CLI par défaut ; l'UI Gradio (ui_gradio.py) remplace l'approbateur."""
import json
import os
from typing import Callable

# Un approbateur reçoit (action, détails) et renvoie (accord: bool, commentaire: str)
Approbateur = Callable[[str, dict], tuple[bool, str]]


def approbateur_cli(action: str, details: dict) -> tuple[bool, str]:
    print("\n" + "=" * 70)
    print(f"VALIDATION CONSEILLER REQUISE — {action}")
    print(json.dumps(details, ensure_ascii=False, indent=2))
    rep = input("Valider ? [o]ui / [n]on (+ motif) : ").strip()
    return rep.lower().startswith("o"), rep


def approbateur_refus(action, details):  # utilisé si aucun humain n'est branché
    return False, "aucun conseiller disponible"


_approbateur: Approbateur = approbateur_cli if os.isatty(0) else approbateur_refus


def set_approbateur(fn: Approbateur):
    global _approbateur
    _approbateur = fn


COMPTEUR = {"n": 0}  # nombre de validations demandées (critère de l'exercice 5 : exactement deux)


def demander(action: str, details: dict) -> tuple[bool, str]:
    from tracing import log
    COMPTEUR["n"] += 1
    ok, motif = _approbateur(action, details)
    log("validation_humaine", action=action, details=details, accord=ok, motif=motif)
    return ok, motif
