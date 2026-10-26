"""Note type cloned from the reference deck.

Reference package (not modified):
Ultimate English Collection EN-def (with audio).apkg

Field order:
  0 Front  definition in the language being learned (card prompt)
  1 Back   lexeme / headword (type-in answer)
  2 Audio  [sound:vocab_NNNN_Lexeme.ext] plus numbered examples

Examples, when present, are appended as:
  [sound:vocab_0002_speak.wav]<br><br>1. ...<br>2. ...
With no examples the field is only the sound tag. Tags on the sample notes are empty.
"""

from __future__ import annotations

MODEL_ID = 205501121325
MODEL_NAME = "EN definition (type-in + audio)"
MODEL_MOD = 1791801750

FIELD_FRONT = "Front"
FIELD_BACK = "Back"
FIELD_AUDIO = "Audio"
FIELDS = (FIELD_FRONT, FIELD_BACK, FIELD_AUDIO)

QFMT = "{{Front}}\n\n{{type:Back}}"
AFMT = "{{Front}}\n\n<hr id=answer>\n\n{{type:Back}}\n\n{{Audio}}"
CSS = (
    ".card { font-family: Segoe UI, Arial, sans-serif; font-size: 20px; "
    "text-align: center; color: #111; background: #fff; line-height: 1.45; }"
)

LATEX_PRE = (
    "\\documentclass[12pt]{article}\n"
    "\\special{papersize=3in,5in}\n"
    "\\usepackage[utf8]{inputenc}\n"
    "\\usepackage{amssymb,amsmath}\n"
    "\\pagestyle{empty}\n"
    "\\setlength{\\parindent}{0in}\n"
    "\\begin{document}\n"
)
LATEX_POST = "\\end{document}"

# Deck options copied from the reference collection so [sound:] autoplays
# when the answer (and therefore the Audio field) is shown.
DECK_CONFIG = {
    "autoplay": True,
    "id": 1,
    "lapse": {
        "delays": [10],
        "leechAction": 0,
        "leechFails": 8,
        "minInt": 1,
        "mult": 0,
    },
    "maxTaken": 60,
    "mod": 0,
    "name": "Default",
    "new": {
        "bury": True,
        "delays": [1, 10],
        "initialFactor": 2500,
        "ints": [1, 4, 7],
        "order": 1,
        "perDay": 20,
        "separate": True,
    },
    "replayq": True,
    "rev": {
        "bury": True,
        "ease4": 1.3,
        "fuzz": 0.05,
        "ivlFct": 1,
        "maxIvl": 36500,
        "minSpace": 1,
        "perDay": 100,
    },
    "timer": 0,
    "usn": 0,
}


def note_model(deck_id: int) -> dict:
    """Return a fresh model dict. ``did`` is the deck used when this model is new."""
    return {
        "css": CSS,
        "did": deck_id,
        "flds": [
            {
                "name": name,
                "ord": index,
                "font": "Liberation Sans",
                "media": [],
                "rtl": False,
                "size": 20,
                "sticky": False,
            }
            for index, name in enumerate(FIELDS)
        ],
        "id": str(MODEL_ID),
        "latexPost": LATEX_POST,
        "latexPre": LATEX_PRE,
        "latexsvg": False,
        "mod": MODEL_MOD,
        "name": MODEL_NAME,
        "req": [[0, "all", [0]]],
        "sortf": 0,
        "tags": [],
        "tmpls": [
            {
                "name": "Card 1",
                "qfmt": QFMT,
                "afmt": AFMT,
                "ord": 0,
                "bafmt": "",
                "bqfmt": "",
                "bfont": "",
                "bsize": 0,
                "did": None,
            }
        ],
        "type": 0,
        "usn": -1,
        "vers": [],
    }
