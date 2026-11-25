"""Konsolenprotokoll von Uvicorn ohne Suchtexte.

Das Zugriffsprotokoll von Uvicorn schreibt die volle Adresse mit Query-String
(zum Beispiel /transactions?q=Name). Ein Filter am Zugriffs-Handler schneidet
alles ab dem "?" ab. Start- und Fehlermeldungen bleiben unverändert. Anfrage-Inhalte
(Formulare, Uploads) protokolliert Uvicorn nie.
"""
import copy
import logging

from uvicorn.config import LOGGING_CONFIG


class StripQueryFilter(logging.Filter):
    """Uvicorn-Zugriffssatz: args = (Client, Methode, Pfad mit Query, HTTP-Version, Status)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = args[:2] + (args[2].split("?", 1)[0],) + args[3:]
        return True


def build_log_config() -> dict:
    cfg = copy.deepcopy(LOGGING_CONFIG)
    cfg["filters"] = {"strip_query": {"()": "kassivo.logconfig.StripQueryFilter"}}
    cfg["handlers"]["access"]["filters"] = ["strip_query"]
    return cfg
