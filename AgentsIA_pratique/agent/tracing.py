"""Traces JSONL : chaque appel d'outil (arguments + résultat), chaque appel LLM
(tokens, durée) et chaque validation humaine (Exercice 1.4)."""
import json
import time
from contextvars import ContextVar
from datetime import datetime
from typing import Any

try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError:  # permet d'utiliser log() dans les tests hors ligne, sans LangChain
    BaseCallbackHandler = object

from config import TRACES_DIR

_fichier: ContextVar[str] = ContextVar("trace_file", default="")
METRIQUES: dict[str, Any] = {}


def demarrer(nom: str) -> str:
    TRACES_DIR.mkdir(exist_ok=True)
    f = TRACES_DIR / f"{datetime.now():%Y%m%d-%H%M%S}_{nom}.jsonl"
    _fichier.set(str(f))
    METRIQUES.clear()
    METRIQUES.update(tool_calls=0, tokens_in=0, tokens_out=0, outils=[])
    return str(f)


def log(evenement: str, **data):
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), "evt": evenement, **data}
    if _fichier.get():
        with open(_fichier.get(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


class TraceHandler(BaseCallbackHandler):
    def __init__(self):
        self._t = {}

    def on_tool_start(self, serialized, input_str, *, run_id, inputs=None, **kw):
        self._t[run_id] = time.time()
        METRIQUES["tool_calls"] += 1
        METRIQUES["outils"].append(serialized.get("name"))
        log("tool_start", outil=serialized.get("name"), arguments=inputs or input_str)

    def on_tool_end(self, output, *, run_id, **kw):
        out = getattr(output, "content", output)
        log("tool_end", resultat=str(out)[:3000], duree_s=round(time.time() - self._t.pop(run_id, time.time()), 2))

    def on_tool_error(self, error, *, run_id, **kw):
        log("tool_error", erreur=repr(error))

    def on_chat_model_start(self, serialized, messages, *, run_id, **kw):
        self._t[run_id] = time.time()

    def on_llm_end(self, response, *, run_id, **kw):
        for gen in response.generations:
            for g in gen:
                msg = getattr(g, "message", None)
                um = getattr(msg, "usage_metadata", None) or {}
                METRIQUES["tokens_in"] += um.get("input_tokens", 0)
                METRIQUES["tokens_out"] += um.get("output_tokens", 0)
                log("llm_end", contenu=getattr(msg, "content", "")[:2000],
                    tool_calls=getattr(msg, "tool_calls", None), usage=um,
                    duree_s=round(time.time() - self._t.pop(run_id, time.time()), 2))
