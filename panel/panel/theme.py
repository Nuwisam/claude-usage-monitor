"""Paleta — z frontend/src/styles/theme.css, jako krotki RGB.

W CSS polprzezroczystosci robi `color-mix(... , transparent)`. Panel rysuje na
nieprzezroczystym tle, wiec mieszamy z gory: `mix()` daje gotowy kolor i w czasie
rysowania nie ma zadnej alfy.

Wszystkie pary tlo/pierwszy plan musza przezyc kwantyzacje RGB565 (5/6/5) —
pilnuje tego tests/test_render.py::test_kolory_przezywaja_kwantyzacje.
"""

BG = (0x1C, 0x1B, 0x19)
SURFACE = (0x26, 0x25, 0x23)
SUNKEN = (0x21, 0x1F, 0x1D)
TEXT = (0xF0, 0xEE, 0xE6)

ACCENT = (0xD9, 0x77, 0x57)
ACCENT_800 = (0x46, 0x28, 0x1D)
ACCENT_700 = (0x8A, 0x4A, 0x33)
ACCENT_500 = (0xD9, 0x77, 0x57)
ACCENT_300 = (0xE8, 0x94, 0x77)
ACCENT_200 = (0xF0, 0xAB, 0x90)
ACCENT_100 = (0xF7, 0xCB, 0xB8)

NEUTRAL_900 = (0x32, 0x2F, 0x2B)
NEUTRAL_800 = (0x41, 0x3D, 0x37)
NEUTRAL_600 = (0x7A, 0x74, 0x6A)
NEUTRAL_400 = (0x9A, 0x93, 0x88)
NEUTRAL_100 = (0xD6, 0xD2, 0xC6)


def mix(fg, pct, bg=BG):
    """`color-mix(in srgb, fg pct%, transparent)` polozony na `bg`."""
    k = pct / 100.0
    return tuple(int(round(f * k + b * (1 - k))) for f, b in zip(fg, bg))


# Nazwane odcienie z makiety 4a — trzymane tutaj, zeby w draw.py i render.py
# nie bylo ani jednej surowej liczby procentowej.
TEXT_78 = mix(TEXT, 78)     # zegar
TEXT_70 = mix(TEXT, 70)     # podpis resetu sesji
TEXT_62 = mix(TEXT, 62)     # narzedzie i maszyna na karcie 1a
TEXT_60 = mix(TEXT, 60)     # etykieta tygodnia, podpis resetu tygodnia
TEXT_58 = mix(TEXT, 58)     # to samo w polowce karty 1b — ciasniej, wiec ciszej
TEXT_55 = mix(TEXT, 55)     # znak procenta
TEXT_52 = mix(TEXT, 52)     # wiek odczytu
TEXT_50 = mix(TEXT, 50)     # plan, etykieta kredytow gdy nie sa biezace
TEXT_45 = mix(TEXT, 45)     # limit kredytow
TEXT_40 = mix(TEXT, 40)
TEXT_28 = mix(TEXT, 28)     # kontur kreskowany
TEXT_26 = mix(TEXT, 26)
TEXT_25 = mix(TEXT, 25)     # tor kredytow gdy brak danych
TEXT_10 = mix(TEXT, 10)     # skos w konturze
DIVIDER = mix(TEXT, 14)     # separator pasow
GHOST = mix(TEXT, 45)       # kreska ostatniego pomiaru

# Odcienie polozone na INNYM tle niz `BG`.
#
# W CSS `color-mix(..., transparent)` miesza sie z tym, co akurat jest pod spodem, wiec
# ten sam procent nad kaflem `Szczegół` i nad tlem karty to DWA ROZNE kolory. Panel nie
# ma alfy — miesza z gory — wiec kazda para (procent, tlo) musi byc osobna stala.
# Uzycie tu `TEXT_45` zamiast `TEXT_45_SURFACE` nie jest zaokragleniem: roznica tel to
# (5, 4, 4), czyli po kwantyzacji 5/6/5 slychac ja na kanale zielonym.
TEXT_78_SURFACE = mix(TEXT, 78, SURFACE)    # tresc w kaflu `Szczegół`
TEXT_45_SURFACE = mix(TEXT, 45, SURFACE)    # etykieta w tym kaflu
TEXT_72_SUNKEN = mix(TEXT, 72, SUNKEN)      # szczegol najpilniejszej w stopce listy
TEXT_70_SUNKEN = mix(TEXT, 70, SUNKEN)      # wartosc w listwie `Tryb`
TEXT_62_SUNKEN = mix(TEXT, 62, SUNKEN)      # nazwy reszty w stopce ukladu 1d
TEXT_45_SUNKEN = mix(TEXT, 45, SUNKEN)      # etykiety w listwie i w stopce


def to_rgb565_pair(c):
    """Kolor po kwantyzacji panelu — do testow czytelnosci."""
    r, g, b = c
    return (r >> 3, g >> 2, b >> 3)
