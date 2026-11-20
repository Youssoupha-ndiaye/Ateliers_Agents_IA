"""Interface Gradio de validation (optionnelle ; la CLI suffit).
Le pipeline tourne dans un thread ; chaque demande de validation apparaît à l'écran
et attend le clic du conseiller. Usage : python agent/ui_gradio.py"""
import asyncio
import json
import queue
import threading

import gradio as gr

import validation
from pipeline import boucle

demandes: "queue.Queue[tuple[str, dict, queue.Queue]]" = queue.Queue()
courante = {}


def approbateur_ui(action, details):
    rep = queue.Queue()
    demandes.put((action, details, rep))
    return rep.get()  # bloque jusqu'à la décision humaine


def rafraichir():
    if not courante and not demandes.empty():
        a, d, r = demandes.get()
        courante.update(action=a, details=d, rep=r)
    if courante:
        return f"### {courante['action']}", json.dumps(courante["details"], ensure_ascii=False, indent=2)
    return "### Aucune validation en attente", ""


def decider(ok, motif):
    if courante:
        courante.pop("rep").put((ok, motif or ("validé" if ok else "refusé")))
        courante.clear()
    return rafraichir()


validation.set_approbateur(approbateur_ui)
threading.Thread(target=lambda: asyncio.run(boucle(once=False)), daemon=True).start()

with gr.Blocks(title="Sama Kër – validation conseiller") as app:
    titre = gr.Markdown()
    det = gr.Code(language="json", label="Action proposée par l'agent")
    motif = gr.Textbox(label="Commentaire / motif de refus")
    with gr.Row():
        ok = gr.Button("✅ Valider", variant="primary")
        ko = gr.Button("❌ Refuser")
    ok.click(lambda m: decider(True, m), motif, [titre, det])
    ko.click(lambda m: decider(False, m), motif, [titre, det])
    gr.Timer(2).tick(rafraichir, None, [titre, det])
    app.load(rafraichir, None, [titre, det])

if __name__ == "__main__":
    app.launch()
