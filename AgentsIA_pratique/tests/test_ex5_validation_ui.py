"""Vérifie que la première réponse du pipeline passe par la validation humaine."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

import calendar_tools  # noqa: E402
import gmail_tools  # noqa: E402
import pipeline  # noqa: E402
import validation  # noqa: E402


def test_gmail_ne_rejoue_pas_les_anciens_messages_du_meme_fil():
    messages = [
        {"id": "reponse", "threadId": "fil-1"},
        {"id": "ancien", "threadId": "fil-1"},
    ]
    metadata = {
        "reponse": {"threadId": "fil-1", "labelIds": ["a_traiter"]},
        "ancien": {"threadId": "fil-1", "labelIds": []},
    }

    class MessagesAPI:
        def list(self, **kwargs):
            return SimpleNamespace(execute=lambda: {"messages": messages})

        def get(self, id, **kwargs):
            return SimpleNamespace(execute=lambda: metadata[id])

    backend = object.__new__(gmail_tools.GmailBackend)
    backend.svc = SimpleNamespace(messages=lambda: MessagesAPI())
    backend.ids = {"attente_validation": "attente", "repondu": "repondu", "transmis": "transmis"}
    backend.message = lambda mid: {"message_id": mid}

    assert backend.non_traites() == [{"message_id": "reponse"}]


def test_reponse_soumise_a_validation_avec_email_recu_et_brouillon():
    msg = {"de": "Client <client@example.com>", "sujet": "Visite", "corps": "Je cherche un studio."}
    gmail = gmail_tools.FakeGmailBackend([msg])
    recu = gmail.non_traites()[0]
    ctx = {"thread_id": recu["thread_id"], "de": recu["de"], "sujet": recu["sujet"],
           "message_recu": recu["corps"]}
    demandes = []
    validation.set_approbateur(lambda action, details: (demandes.append((action, details)) or False, "refus de test"))

    pipeline._garantir_validation_brouillon(
        recu, gmail, ctx, "Bonjour, voici notre réponse concernant votre demande de visite. L'équipe Sama Kër Immobilier"
    )

    assert len(gmail.drafts) == 1
    assert gmail.envoyes == []
    assert ctx["refus"] == "refus de test"
    assert len(demandes) == 1
    assert demandes[0][0] == "Envoyer l'email"
    assert demandes[0][1]["message_recu"] == recu["corps"]
    assert demandes[0][1]["corps"] == next(iter(gmail.drafts.values()))["corps"]


def _dossier_test():
    return {
        "biens_proposes": ["SK-001", "SK-011"],
        "creneaux_proposes": [
            {"conseiller": "Awa", "debut": "2026-11-26T16:00:00+00:00",
             "fin": "2026-11-26T16:45:00+00:00"},
            {"conseiller": "Moussa", "debut": "2026-11-26T16:30:00+00:00",
             "fin": "2026-11-26T17:15:00+00:00"},
        ],
    }


def test_confirmation_sans_logement_demande_le_choix_via_validation():
    msg = {"de": "Client <client@example.com>", "sujet": "Re: Visite",
           "corps": "16:00 - 16:45 ça devrait aller\n\nLe message précédent a écrit :\n> SK-001"}
    gmail = gmail_tools.FakeGmailBackend([msg])
    recu = gmail.non_traites()[0]
    ctx = {"thread_id": recu["thread_id"], "de": recu["de"], "sujet": recu["sujet"],
           "message_recu": pipeline._texte_nouveau_message(msg)}
    demandes = []
    validation.set_approbateur(lambda action, details: (demandes.append((action, details)) or False, "refus de test"))

    traite = asyncio.run(pipeline._traiter_confirmation_client(
        msg, gmail, calendar_tools.FakeCalendarBackend(), ctx, _dossier_test(), []
    ))

    assert traite
    assert len(gmail.drafts) == 1 and gmail.envoyes == []
    assert len(demandes) == 1 and demandes[0][0] == "Envoyer l'email"
    assert "SK-001 et SK-011" in demandes[0][1]["corps"]
    assert "message précédent" not in demandes[0][1]["message_recu"]


def test_confirmation_complete_demande_validation_et_cree_visite():
    msg = {"de": "Client <client@example.com>", "sujet": "Re: Visite",
           "corps": "Je choisis SK-001, 16:00 - 16:45 avec Awa, ça devrait aller.\n\nLe message précédent a écrit :"}
    gmail = gmail_tools.FakeGmailBackend([msg])
    recu = gmail.non_traites()[0]
    ctx = {"thread_id": recu["thread_id"], "de": recu["de"], "sujet": recu["sujet"],
           "message_recu": pipeline._texte_nouveau_message(msg)}
    agenda = calendar_tools.FakeCalendarBackend()
    demandes = []
    validation.set_approbateur(lambda action, details: (demandes.append((action, details)) or True, "validé test"))

    class DetailBien:
        name = "detail_bien"

        async def ainvoke(self, arguments):
            assert arguments["bien_id"] == "SK-001"
            return {"ok": True, "bien": {"id": "SK-001", "adresse": "Adresse de test"}}

    traite = asyncio.run(pipeline._traiter_confirmation_client(
        msg, gmail, agenda, ctx, _dossier_test(), [DetailBien()]
    ))

    assert traite and ctx["cloture"] and ctx["envoye"]
    assert len(demandes) == 1
    assert demandes[0][0] == "Créer la visite ET envoyer la confirmation au client"
    assert demandes[0][1]["bien"] == "SK-001"
    assert demandes[0][1]["message_recu"] == ctx["message_recu"]
    assert len(agenda.events["Awa"]) == 1
    assert len(gmail.envoyes) == 1


if __name__ == "__main__":
    test_gmail_ne_rejoue_pas_les_anciens_messages_du_meme_fil()
    test_reponse_soumise_a_validation_avec_email_recu_et_brouillon()
    test_confirmation_sans_logement_demande_le_choix_via_validation()
    test_confirmation_complete_demande_validation_et_cree_visite()
    print("✅ validation UI testée")
