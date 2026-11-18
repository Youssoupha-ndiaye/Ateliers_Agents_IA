"""Crée un agenda Google par conseiller sur le compte de test (Exercice 3.1).

Usage : python agent/setup_calendars.py
Affiche la ligne CONSEILLERS=... à coller dans .env.

- Idempotent : si un agenda « Visites – Awa » existe déjà, il est réutilisé (pas de doublon).
- Le scope `calendar` complet (création d'agendas) n'est demandé QUE par ce script, une fois,
  et son jeton n'est PAS enregistré : l'agent, lui, n'a que `calendar.events`.
"""
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from config import GOOGLE_CLIENT_SECRET, SCOPE_CREATION_AGENDAS, TZ

NOMS = ["Awa", "Moussa", "Fatou"]


def main():
    flow = InstalledAppFlow.from_client_secrets_file(str(GOOGLE_CLIENT_SECRET), SCOPE_CREATION_AGENDAS)
    creds = flow.run_local_server(port=0, prompt="consent",
                                  authorization_prompt_message="Connectez-vous avec le compte de l'ATELIER : {url}")
    svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
    existants = {c["summary"]: c["id"] for c in svc.calendarList().list().execute().get("items", [])}
    ids = []
    for n in NOMS:
        titre = f"Visites – {n}"
        if titre in existants:
            cid = existants[titre]
            print(f"Agenda déjà présent : {titre}")
        else:
            cid = svc.calendars().insert(body={"summary": titre, "timeZone": TZ}).execute()["id"]
            print(f"Agenda créé : {titre}")
        ids.append(f"{n}={cid}")
    print("\nCopiez cette ligne dans votre fichier .env :\n")
    print("CONSEILLERS=" + ",".join(ids))


if __name__ == "__main__":
    main()
