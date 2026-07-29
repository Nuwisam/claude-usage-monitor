"""Geometria ukladu 4a. Kazda wspolrzedna jest WYLICZANA z kilku stalych.

4a: dwa konta w pasach na cala szerokosc, w kazdym pasie procent stoi w waskiej
kolumnie OBOK bloku (etykieta / pasek / reset), a kredyty siedza na dole pasa.
Zysk wzgledem ukladu z liczba nad paskiem: etykieta i podpis dziela wysokosc
z liczba, zamiast stac nad i pod paskiem na calej szerokosci.

Rozmiary czcionek i wysokosci paskow sa z makiety i maja tam uzasadnienie:
trzy stopnie tekstu i nic pomiedzy (10 px tylko etykiety wersalikami, 11 px dane
wtorne, od 12 px tresc), a paski trzy grubosci wedlug wagi okna.
"""

PAD_X = 14
PAD_TOP = 9
PAD_BOT = 9
ROW_GAP = 6
INNER_GAP = 4

NUM_W = 76          # kolumna procentu; ciasna celowo, zeby paski startowaly rowno
NUM_GAP = 12
PCT_GAP = 2         # odstep miedzy liczba a znakiem %

HEADER_H = 17
LABEL_H = 11
LINE_H = 14

SES_BAR_H = 12
WK_BAR_H = 9
CREDITS_BAR_H = 5
CREDITS_H = 14

# Czcionki
F_NAME = 15
F_PLAN = 10
F_CLOCK = 14
F_LABEL = 10
F_RESET = 12
F_AGO = 11
F_SES_NUM = 42
F_SES_NUM_TIGHT = 34    # wartosc trzycyfrowa schodzi o stopien, inaczej nie wchodzi
F_SES_PCT = 14
F_WK_NUM = 30
F_WK_PCT = 12
F_CREDITS_USED = 14
F_CREDITS_LIMIT = 11
F_WORDS = 13            # "nie wiem" zamiast liczby

DIVIDER_H = 1


class Band:
    """Prostokaty jednego pasa konta, we wspolrzednych EKRANU."""

    def __init__(self, top, height, width):
        self.top = top
        self.height = height
        self.bottom = top + height

        self.x0 = PAD_X
        self.x1 = width - PAD_X
        self.num_right = PAD_X + NUM_W
        self.block_x0 = self.num_right + NUM_GAP
        self.block_x1 = self.x1

        y = top + PAD_TOP
        self.header = (self.x0, y, self.x1, y + HEADER_H)
        y += HEADER_H + ROW_GAP

        self.ses_top = y
        self.ses_label = (self.block_x0, y, self.block_x1, y + LABEL_H)
        y += LABEL_H + INNER_GAP
        self.ses_bar = (self.block_x0, y, self.block_x1, y + SES_BAR_H)
        y += SES_BAR_H + INNER_GAP
        self.ses_line = (self.block_x0, y, self.block_x1, y + LINE_H)
        self.ses_bottom = y + LINE_H
        self.ses_centre = (self.ses_top + self.ses_bottom) // 2

        y = self.ses_bottom + ROW_GAP
        self.wk_top = y
        self.wk_label = (self.block_x0, y, self.block_x1, y + LABEL_H)
        y += LABEL_H + INNER_GAP
        self.wk_bar = (self.block_x0, y, self.block_x1, y + WK_BAR_H)
        y += WK_BAR_H + INNER_GAP
        self.wk_line = (self.block_x0, y, self.block_x1, y + LINE_H)
        self.wk_bottom = y + LINE_H
        self.wk_centre = (self.wk_top + self.wk_bottom) // 2

        # Kredyty przyklejone do dolu pasa (margin-top: auto w makiecie).
        cy = self.bottom - PAD_BOT - CREDITS_H
        self.credits = (self.x0, cy, self.x1, cy + CREDITS_H)
        self.credits_centre = cy + CREDITS_H // 2

    @property
    def fits(self):
        """Czy kredyty nie wchodza na tydzien. Pilnuje tego
        tests/test_render.py::test_kredyty_nie_wchodza_na_tydzien."""
        return self.credits[1] >= self.wk_bottom


class Layout:
    def __init__(self, width=480, height=320):
        self.width = width
        self.height = height
        band_h = (height - DIVIDER_H) // 2
        self.band_a = Band(0, band_h, width)
        self.divider = (0, band_h, width, band_h + DIVIDER_H)
        self.band_b = Band(band_h + DIVIDER_H, height - band_h - DIVIDER_H, width)
        self.bands = (self.band_a, self.band_b)
