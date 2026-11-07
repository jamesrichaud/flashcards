"""Counts your answers and shows today's count under each card's answer.

Example add-on for this web app (not for desktop Anki). Copy this file into the `addons` folder
inside the app's data folder (`data/addons/answer_counter.py`), then turn it on in
Settings > Add-ons. It shows every hook: card_render, after_answer and an extra page.
"""
import html
import time

from fastapi import APIRouter, Request

from flashcards import addons

NAME = "Answer counter"

_counts = {}   # (collection path, day) -> {1: n, 2: n, 3: n, 4: n}


def _day():
    return time.strftime("%Y-%m-%d")


def card_render(text, card, kind, col):
    if kind != "answer":
        return text
    n = sum(_counts.get((col.path, _day()), {}).values())
    return text + f'<p style="text-align:center;opacity:.6;font-size:14px">Answered today: {n}</p>'


def after_answer(col, card, rating):
    c = _counts.setdefault((col.path, _day()), {1: 0, 2: 0, 3: 0, 4: 0})
    c[rating] = c.get(rating, 0) + 1


router = APIRouter()
MENU = [("Today's answers", "/")]


@router.get("/")
def today(request: Request):
    with addons.collection(request) as col:
        c = _counts.get((col.path, _day()), {})
    rows = "".join(f"<tr><td>{name}</td><td class=n>{c.get(i, 0)}</td></tr>"
                   for i, name in enumerate(("Again", "Hard", "Good", "Easy"), 1))
    return addons.page(request, "Answer counter",
                       f'<div class="box"><h3 style="margin-top:0">Answers since the server started, today</h3>'
                       f'<table>{rows}</table><p class="muted">{html.escape(_day())}</p></div>')
