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

# Powod blokady w naglowku pasa, obok plakietki planu. Zmierzony zapas w naglowku przy
# najdluzszej realnej nazwie to 202 px w gornym pasie i 259 px w dolnym, wiec kilkadziesiat
# pikseli na jedno slowo wersalikami gryzie wylacznie wtedy, gdy nazwa i tak jest skracana.
F_REASON = 10
REASON_GAP = 6


# --- karta zablokowanej sesji ------------------------------------------------
#
# Wlasny margines, szerszy niz w pasie: karta ma jedna kolumne tresci, a pas trzy,
# wiec 14 px, ktore w pasie sa kompromisem, tutaj bylyby ciasnota bez powodu.
ALERT_PAD_X = 18
BANNER_H = 38
# Linia bazowa napisow w pasmie, ZMIERZONA na wyrenderowanej makiecie: dol cyfr zegara
# stoi tam na 24,33 px, a Pillow z anchor="ls" klazie dol tuszu na `base - 1`. Bylo 26,
# czyli o 1,67 px za nisko w kazdym z czterech ukladow — nie szum kroju, tylko stala.
BANNER_BASE = 24
F_BANNER = 15
BANNER_TRACK = 2        # 0,13em na 15 px
F_BANNER_AT = 15

# Rail na lewej krawedzi karty, pod pasmem. Stoi w OBU klatkach: `NEUTRAL_900`
# w spoczynkowej, `ACCENT` w pelnej — zalanie go przemalowuje, nie powoluje.
# Zjazd koloru zajmuje pasmo, a nizej juz tylko te 6 px — tlo karty zostaje
# `theme.BG`, bo pelnoekranowe pole akcentu przy jasnosci 5 to blask w oczy
# i widoczne pasmowanie RGB565.
RAIL_W = 6

# Odstep po obu stronach kropki w wierszu "narzedzie · maszyna" — ale TYLKO w ukladzie
# 1a, gdzie makieta sklada ten wiersz z trzech pudelek z `gap: 7px` i przyciemniona
# kropka. Uklady 1b i 1c maja tam JEDEN przebieg tekstu ze zwyklymi spacjami.
META_DOT_GAP = 7

# Znacznik przy koncie po zwinieciu karty. Siedzi w polu marginesu pasa (PAD_X 14),
# wiec uklad pasow nie drga ani o piksel.
MARK_W = 4


class AlertSolo:
    """Jedna blokada. Nazwa projektu jest bohaterem, bo do konkretnego okna trzeba
    wrocic — a przy jednej blokadzie wiadomo, do ktorego."""

    # LINIE BAZOWE, zmierzone na wyrenderowanej makiecie — nie policzone z pudelek CSS.
    #
    # Powod: kroj panelu (Segoe UI) ma inne metryki pionowe niz font makiety, wiec ten
    # sam model pudelka klazie tusz o 1-3 px gdzie indziej. Odstep miedzy pudelkami jest
    # niewidzialny, polozenie tuszu widac — wiec to ono jest kontraktem. Sprawdzane
    # pomiarem w tests/test_alert.py::test_tusz_pasma_i_listwy_stoi_tam_gdzie_makieta.
    PROJECT_BASE = 87
    META_BASE = 112
    WAITED_BASE = 158
    DETAIL_TOP = 180
    DETAIL_LABEL_BASE = 19  # obie od GORNEJ KRAWEDZI kafla, bo kafel jest kotwica
    DETAIL_TEXT_BASE = 37
    MODE_BASE = 306         # zmierzone 306,33; gorna krawedz listwy zgadza sie co do px

    F_PROJECT = 34
    F_PROJECT_TIGHT = 26    # jak F_SES_NUM -> F_SES_NUM_TIGHT: stopien nizej, nie nowy mechanizm
    F_META = 12
    F_WAITED = 26
    F_DETAIL_LABEL = 10
    LH_DETAIL_LABEL = 10
    F_DETAIL = 12
    DETAIL_LINE = 16        # 1,35 x 12 px, zaokraglone w dol na ADVANCE miedzy liniami
    DETAIL_LINES = 2
    DETAIL_PAD_X = 12
    DETAIL_PAD_Y = 10
    DETAIL_GAP = 5
    DETAIL_RADIUS = 4
    MODE_H = 34
    F_MODE_LABEL = 10
    F_MODE = 11

    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.banner = (0, 0, width, BANNER_H)
        self.x0 = ALERT_PAD_X
        self.x1 = width - ALERT_PAD_X

        self.project_base = self.PROJECT_BASE
        self.meta_base = self.META_BASE
        self.waited_base = self.WAITED_BASE
        self.detail_y = self.DETAIL_TOP

        self.mode = (0, height - self.MODE_H, width, height)
        self.mode_centre = height - self.MODE_H // 2

    def detail_box(self, lines):
        """Kafel szczegolu rosnie z liczba linii, a nie odwrotnie: jednolinijkowy
        `detail` nie ma powodu zostawiac pustego pola pod soba.

        Wysokosc bloku tekstu obcinana W DOL. Przegladarka trzyma blok w ulamku —
        jedna linia przy line-height 1,35 to 16,2 px — i dopiero KRAWEDZ kafla laduje
        na siatce pikseli: 20 + 10 + 5 + 16,2 = 51,2 px maluje sie jako 51, nie 52.
        Zaokraglanie w gore samego bloku dawalo kafel o piksel za wysoki (zmierzone
        na wyrenderowanej makiecie: 51 px, panel rysowal 52).
        """
        n = max(1, lines)
        block = (162 * n) // 10             # floor(16,2 x n)
        h = 2 * self.DETAIL_PAD_Y + self.F_DETAIL_LABEL + self.DETAIL_GAP + block
        return (self.x0, self.detail_y, self.x1, self.detail_y + h)

    def fits(self, lines):
        """Czy kafel szczegolu nie wchodzi na listwe trybu. Pilnuje tego
        tests/test_alert.py::test_uklady_nie_nachodza_na_siebie."""
        return self.detail_box(lines)[3] <= self.mode[1]


class AlertPair:
    """Dwie blokady — dwie rowne polowy. Ten sam stopien pisma i ten sam zestaw pol
    w obu: pierwszenstwo niesie KOLEJNOSC z `status.parse_frame`, nie typografia.
    Do dwoch blokad nazwa projektu zostaje bohaterem, bo do konkretnego okna trzeba
    wrocic i trzeba wiedziec, do ktorego."""

    F_SHORT = 10
    SHORT_TRACK = 1         # 0,09em na 10 px
    F_WAITED = 17
    F_PROJECT = 30
    F_META = 11
    F_DETAIL = 12
    GAP = 10                # miedzy powodem a czasem w gornym wierszu polowy

    # Linie bazowe liczone od GORY POLOWY, zmierzone na makiecie (jak w AlertSolo).
    #
    # Makieta ma polowy po 140,5 px z trescia centrowana pionowo, wiec JEJ WLASNE dwie
    # polowy roznia sie o piksel — raz sub-piksel wypada w gore, raz w dol. Tego nie
    # odtwarzamy: obie polowy dostaja te same offsety, bo lezac obok siebie musza
    # wygladac tak samo. Blad wzgledem makiety to najwyzej 1 px na polowe.
    SHORT_BASE = 37         # powod i czas stoja na jednej linii bazowej
    PROJECT_BASE = 73
    META_BASE = 91
    DETAIL_BASE = 113

    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.x0 = ALERT_PAD_X
        self.x1 = width - ALERT_PAD_X
        # 282 px na dwie polowy z jednym wlosem dzielnika: 281 px tresci dzieli sie na
        # 140,5 px kazdej, a przegladarka oddaje reszte DOLNEJ polowie — dzielnik laduje
        # na wierszu 178, nie 179. Zmierzone na wyrenderowanej makiecie; wczesniejszy
        # komentarz twierdzil odwrotnie i stad brala sie ta jedna linia roznicy.
        top = BANNER_H
        self.divider_y = top + (height - top - DIVIDER_H) // 2
        self.divider = (0, self.divider_y, width, self.divider_y + DIVIDER_H)
        self.halves = ((top, self.divider_y),
                       (self.divider_y + DIVIDER_H, height))


class AlertList:
    """Trzy blokady — trzy rowne wiersze. Prog jest wlasnie tutaj: trzy nazwy
    projektow w 34 px nie istnieja, wiec nazwa schodzi do 19 px, a powod przenosi sie
    do stalej kolumny po lewej. Szczegol NAJNOWSZEJ ladauje w stopce — jeden,
    bo trzy nie zmieszcza sie w zadnym czytelnym stopniu."""

    ROWS = 3
    FOOTER_H = 49
    REASON_W = 58
    COL_GAP = 12
    TIME_W = 62             # kolumna czasu, do prawej; stala, zeby nazwy konczyly sie rowno

    F_REASON = 10
    REASON_TRACK = 1        # 0,09em na 10 px
    F_PROJECT = 19
    F_META = 11
    F_TIME = 14
    F_FOOT_LABEL = 10
    F_FOOT = 12

    # Linie bazowe od GORY WIERSZA, zmierzone na makiecie (srodkowy wiersz — blad
    # rozklada sie wtedy na oba skrajne zamiast kumulowac w jednym).
    REASON_BASE = 42
    PROJECT_BASE = 37
    META_BASE = 54
    TIME_BASE = 43
    # ... i od gory stopki.
    FOOT_LABEL_BASE = 18
    FOOT_TEXT_BASE = 35

    FOOT_LABEL = "NEWEST DETAIL"

    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.x0 = ALERT_PAD_X
        self.x1 = width - ALERT_PAD_X
        self.name_x = self.x0 + self.REASON_W + self.COL_GAP
        self.name_x1 = self.x1 - self.TIME_W - self.COL_GAP

        self.footer = (0, height - self.FOOTER_H, width, height)

    def rows(self, footer=True):
        """Wiersze przy stopce i bez niej. Bez szczegolu stopka nie ma czego pokazac,
        a pusty pas na dole czytalby sie jako urwany ekran.

        Reszta z dzielenia idzie tam, gdzie daje ja przegladarka: wysokosci licza sie
        z ULAMKOWYCH granic, a nie przez `//`. Przy 242 px na trzy wiersze daje to
        81 / 80 / 81, a nie 80 / 80 / 80 i dwa piksele tla przy stopce.
        """
        top = BANNER_H
        bottom = self.footer[1] if footer else self.height
        span = bottom - top - (self.ROWS - 1) * DIVIDER_H
        out = []
        y = top
        for i in range(self.ROWS):
            h = (round(span * (i + 1) / self.ROWS)
                 - round(span * i / self.ROWS))
            out.append((y, y + h))
            y += h + DIVIDER_H
        return out


class AlertMany(AlertList):
    """Cztery blokady i wiecej. Te same trzy wiersze co w 1c, ale nazwa schodzi
    o stopien i znika narzedzie: przy pieciu blokadach szczegol jednej jest
    przypadkowy, wiec zamiast niego stopka liczy reszte i wymienia ja z nazwy.

    Licznik w pasmie liczy WSZYSTKIE, nie tylko wypisane."""

    FOOTER_H = 38           # jedna linia zamiast dwoch, bo stopka nie ma tu etykiety nad tekstem
    F_PROJECT = 17
    F_MACHINE = 11

    REASON_BASE = 44
    PROJECT_BASE = 39
    META_BASE = 55
    TIME_BASE = 45
    FOOT_BASE = 22          # etykieta i nazwy na JEDNEJ linii bazowej
    FOOT_GAP = 10


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
        # 319 px na dwa pasy to 159,5 px kazdy — przegladarka daje nadmiarowy piksel
        # GORNEMU pasowi, wiec dzielnik stoi w wierszu 160, nie 159.
        band_h = (height - DIVIDER_H + 1) // 2
        self.band_a = Band(0, band_h, width)
        self.divider = (0, band_h, width, band_h + DIVIDER_H)
        self.band_b = Band(band_h + DIVIDER_H, height - band_h - DIVIDER_H, width)
        self.bands = (self.band_a, self.band_b)
        self.alert_solo = AlertSolo(width, height)
        self.alert_pair = AlertPair(width, height)
        self.alert_list = AlertList(width, height)
        self.alert_many = AlertMany(width, height)
