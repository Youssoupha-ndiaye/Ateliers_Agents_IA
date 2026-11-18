"""OAuth Google sur le compte de TEST de l'atelier (Exercice 3.1).

Premier lancement : ouvre le navigateur, vous vous connectez avec le compte de l'atelier,
vous acceptez les autorisations, puis le jeton est stocké dans secrets/token.json (hors Git).

Usage :
    python agent/google_auth.py            # crée / rafraîchit le jeton
    python agent/google_auth.py --check    # vérifie l'accès : compte connecté, agendas, libellés
    python agent/google_auth.py --console  # sans navigateur local (Colab, serveur) : copier-coller du code
"""
import os
import sys

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from config import GOOGLE_CLIENT_SECRET, GOOGLE_TOKEN, SCOPES


def _nouveau_jeton(scopes) -> Credentials:
    if not GOOGLE_CLIENT_SECRET.exists():
        raise FileNotFoundError(
            f"{GOOGLE_CLIENT_SECRET} introuvable. Téléchargez l'ID client OAuth « Application de bureau » "
            "depuis la console Google Cloud (voir README §4.3) et enregistrez-le sous ce nom.")
    flow = InstalledAppFlow.from_client_secrets_file(str(GOOGLE_CLIENT_SECRET), scopes)
    if "--console" in sys.argv:
        # Pas de navigateur sur la machine : on affiche l'URL, l'utilisateur colle l'URL de retour.
        flow.redirect_uri = "http://localhost:1"
        url, _ = flow.authorization_url(prompt="consent", access_type="offline")
        print("Ouvrez cette URL, connectez-vous avec le compte de l'ATELIER et acceptez :\n", url)
        retour = input("\nCollez ici l'URL complète de la page (même en erreur « site inaccessible ») : ").strip()
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"  # retour sur http://localhost, accepté pour un client « bureau »
        flow.fetch_token(authorization_response=retour)
        return flow.credentials
    return flow.run_local_server(port=0, prompt="consent",
                                 authorization_prompt_message="Connectez-vous avec le compte Google de l'ATELIER : {url}")


def credentials(scopes=SCOPES) -> Credentials:
    creds = None
    if GOOGLE_TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN), scopes)
        # jeton créé avec d'autres scopes -> on le refait
        if creds.scopes and not set(scopes) <= set(creds.scopes):
            creds = None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            # en mode « Test », Google invalide le jeton de rafraîchissement au bout de 7 jours
            print("Jeton expiré ou révoqué : nouvelle autorisation nécessaire.")
            creds = None
    if not creds or not creds.valid:
        creds = _nouveau_jeton(scopes)
        GOOGLE_TOKEN.parent.mkdir(exist_ok=True)
        GOOGLE_TOKEN.write_text(creds.to_json())
    return creds


def service(name: str, version: str):
    return build(name, version, credentials=credentials(), cache_discovery=False)


def verifier():
    gm = service("gmail", "v1")
    profil = gm.users().getProfile(userId="me").execute()
    print(f"✅ Gmail : connecté en tant que {profil['emailAddress']} ({profil.get('messagesTotal', 0)} messages)")
    libs = [l["name"] for l in gm.users().labels().list(userId="me").execute()["labels"] if l["name"].startswith("SamaKer")]
    print("   Libellés SamaKer :", libs or "aucun (ils seront créés au premier lancement de l'exercice 4)")
    try:
        import config
        cal = service("calendar", "v3")
        for nom, cid in config.conseillers().items():
            n = len(cal.events().list(calendarId=cid, maxResults=5).execute().get("items", []))
            print(f"✅ Agenda de {nom} accessible ({n} événement(s) lus)")
    except RuntimeError as e:
        print("⚠️ ", e)


if __name__ == "__main__":
    credentials()
    print("Jeton OAuth enregistré dans", GOOGLE_TOKEN)
    if "--check" in sys.argv:
        verifier()
