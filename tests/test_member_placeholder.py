"""Ein Klick auf "Zuordnen" ohne Auswahl darf nicht dauerhaft "kein Mitglied" speichern."""
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATES = Path(__file__).resolve().parent.parent / "src/kassivo/web/templates"


def _form(selected):
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=True)
    env.filters["eur"] = str
    macros = env.get_template("_macros.html").module
    return str(macros.member_form(1, selected, [{"id": 7, "name": "Anna Beispiel", "austritt": None}], "/unmatched"))


def test_without_member_a_choice_is_required():
    html = _form(None)
    assert "required" in html
    assert html.index("Bitte wählen") < html.index("— keins —")


def test_with_member_it_stays_preselected_and_keins_is_selectable():
    html = _form(7)
    assert "Bitte wählen" not in html
    assert 'value="7" selected' in html
    assert "— keins —" in html
