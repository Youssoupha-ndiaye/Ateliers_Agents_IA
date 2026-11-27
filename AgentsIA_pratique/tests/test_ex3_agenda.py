"""Tests hors ligne de l'exercice 3 (sans Google, sans LLM).
Usage : python tests/test_ex3_agenda.py
"""
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

import calendar_tools  # noqa: E402
import regles  # noqa: E402
import validation  # noqa: E402

TZ = regles.TZ
validation.set_approbateur(lambda a, d: (True, "test"))


def h(j, hh, mm=0):
    return datetime.combine(j, datetime.min.time(), TZ).replace(hour=hh, minute=mm)


def test_dates():
    lundi = date(2026, 11, 16)
    assert regles.resoudre_date("jeudi prochain", lundi) == date(2026, 11, 19)
    assert regles.resoudre_date("jeudi", date(2026, 11, 19)) == date(2026, 11, 26)  # strictement après
    assert regles.resoudre_date("demain", lundi) == date(2026, 11, 17)
    assert regles.resoudre_date("21/11", lundi) == date(2026, 11, 21)
    assert regles.plage_moment("fin d’après-midi") == (regles.time(16), regles.time(18))
    assert regles.plage_moment("ci suba") == (regles.time(9), regles.time(12))


def test_horaires():
    j = date(2026, 11, 19)
    assert regles.est_dans_horaires(h(j, 9))
    assert regles.est_dans_horaires(h(j, 17, 15))       # 17:15 + 45 min = 18:00
    assert not regles.est_dans_horaires(h(j, 17, 30))   # finit après 18 h
    assert not regles.est_dans_horaires(h(j, 8, 45))
    assert not regles.est_dans_horaires(h(date(2026, 11, 22), 10))  # dimanche


def test_premiere_heure_disponible():
    j = date(2026, 11, 19)
    occupations = {
        "Awa": [(h(j, 9), h(j, 10))],
        "Moussa": [],
        "Fatou": [],
    }
    creneaux = regles.creneaux_libres(j, "journee", occupations, nombre=1, maintenant=h(j, 8))
    assert len(creneaux) == 1
    assert datetime.fromisoformat(creneaux[0]["debut"]) == h(j, 9)
    assert creneaux[0]["conseiller"] in ("Moussa", "Fatou")


def test_charge_journaliere_departage_a_heure_egale():
    j = date(2026, 11, 19)
    occupations = {
        "Awa": [(h(j, 12), h(j, 12, 45)), (h(j, 14), h(j, 14, 45))],
        "Moussa": [(h(j, 13), h(j, 13, 45))],
        "Fatou": [],
    }
    creneaux = regles.creneaux_libres(j, "journee", occupations, nombre=1, maintenant=h(j, 8))
    assert datetime.fromisoformat(creneaux[0]["debut"]) == h(j, 9)
    assert creneaux[0]["conseiller"] == "Fatou"


def test_visite_inscrite_dans_agenda_du_conseiller_reparti():
    j = date(2026, 11, 19)
    occupations = {
        "Awa": [(h(j, 12), h(j, 12, 45)), (h(j, 14), h(j, 14, 45))],
        "Moussa": [(h(j, 13), h(j, 13, 45))],
        "Fatou": [],
    }
    backend = calendar_tools.FakeCalendarBackend(occupe=occupations)
    proposition = regles.creneaux_libres(j, "journee", occupations, nombre=1, maintenant=h(j, 8))[0]
    resultat = calendar_tools.Agenda(backend).creer_visite(
        proposition["conseiller"], proposition["debut"], "SK-001", "adresse", "Client", "client@example.com"
    )
    assert resultat["ok"] and resultat["conseiller"] == "Fatou", resultat
    assert len(backend.events["Fatou"]) == 1
    assert len(backend.events["Awa"]) == 2 and len(backend.events["Moussa"]) == 1


def test_propositions_equilibrees_entre_conseillers():
    j = date(2026, 11, 19)
    occupations = {"Awa": [], "Moussa": [], "Fatou": []}
    creneaux = regles.creneaux_libres(j, "journee", occupations, nombre=3, maintenant=h(j, 8))
    assert [datetime.fromisoformat(c["debut"]) for c in creneaux] == [
        h(j, 9), h(j, 9, 30), h(j, 10)
    ]
    assert {c["conseiller"] for c in creneaux} == {"Awa", "Moussa", "Fatou"}


def test_trois_creneaux_libres():
    j = regles.resoudre_date("jeudi prochain")
    b = calendar_tools.FakeCalendarBackend(occupe={"Awa": [(h(j, 16), h(j, 18))], "Moussa": [(h(j, 16), h(j, 17))]})
    r = calendar_tools.Agenda(b).lister_creneaux_libres("jeudi prochain", "fin d'apres-midi", 3)
    assert r["ok"] and len(r["creneaux"]) == 3, r
    occ = b.occupations(None, None)
    for c in r["creneaux"]:
        d, f = datetime.fromisoformat(c["debut"]), datetime.fromisoformat(c["fin"])
        assert h(j, 16) <= d and f <= h(j, 18)
        assert f - d == timedelta(minutes=45)
        assert not any(regles.chevauche(d, f, a, z) for a, z in occ[c["conseiller"]])


def test_aucun_doublon_concurrent():
    j = regles.resoudre_date("jeudi prochain")
    ag = calendar_tools.Agenda(calendar_tools.FakeCalendarBackend())
    debut = h(j, 16).isoformat()
    res = []
    th = [threading.Thread(target=lambda n=n: res.append(
        ag.creer_visite("Fatou", debut, "SK-001", "Villa n°12, rue MZ-45, Mermoz, Dakar", n, "x@example.com")))
        for n in ("A", "B", "C")]
    [t.start() for t in th]; [t.join() for t in th]
    assert sum(r["ok"] for r in res) == 1, res
    assert all("CRENEAU_PRIS" in r["erreur"] for r in res if not r["ok"])
    ev = next(iter(v for k, v in ag.b.events["Fatou"].items()))
    assert ev["location"] == "Villa n°12, rue MZ-45, Mermoz, Dakar"


def test_refus_conseiller_rien_ecrit():
    j = regles.resoudre_date("jeudi prochain")
    ag = calendar_tools.Agenda(calendar_tools.FakeCalendarBackend())
    validation.set_approbateur(lambda a, d: (False, "non"))
    r = ag.creer_visite("Awa", h(j, 10).isoformat(), "SK-001", "adr", "A", "a@x.sn")
    validation.set_approbateur(lambda a, d: (True, "test"))
    assert not r["ok"] and "REFUS_CONSEILLER" in r["erreur"] and not ag.b.events["Awa"]


def test_deplacer_et_annuler():
    j = regles.resoudre_date("jeudi prochain")
    ag = calendar_tools.Agenda(calendar_tools.FakeCalendarBackend())
    r = ag.creer_visite("Awa", h(j, 10).isoformat(), "SK-001", "adr", "A", "a@x.sn")
    eid = r["event_id"]
    # déplacement de 15 min : chevauche l'ancienne position, doit être accepté (elle ne se bloque pas elle-même)
    assert ag.deplacer_visite("Awa", eid, h(j, 10, 15).isoformat())["ok"]
    assert not ag.deplacer_visite("Awa", eid, h(j, 18).isoformat())["ok"]   # hors horaires
    assert ag.annuler_visite("Awa", eid)["ok"] and not ag.b.events["Awa"]
    assert not ag.annuler_visite("Awa", eid)["ok"]                          # déjà annulée


if __name__ == "__main__":
    n = 0
    for nom, f in list(globals().items()):
        if nom.startswith("test_"):
            f(); n += 1; print("✅", nom)
    print(f"{n} tests réussis")
