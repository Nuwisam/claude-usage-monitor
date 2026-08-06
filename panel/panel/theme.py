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

# Jedyna barwa w tym pliku, ktorej NIE MA w frontend/src/styles/theme.css — bo w calej
# palecie projektu nie ma czerwieni. Sluzy wylacznie ostrzezeniu o zablokowanej sesji.
#
# Zielona skladowa musi zostac nisko (<= 0x50): akcent panelu sam jest pomaranczowo-
# czerwony, wiec jasniejszy odcien przeczytalby sie jako "troche inny pomarancz", a nie
# jako inny kolor. Po kwantyzacji 5/6/5 wychodzi (28, 18, 7) — odrozniane i od BG (3, 6, 3),
# i od ACCENT (27, 29, 10).
DANGER = (0xE0, 0x4B, 0x3A)

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
TEXT_60 = mix(TEXT, 60)     # etykieta tygodnia, podpis resetu tygodnia
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


def to_rgb565_pair(c):
    """Kolor po kwantyzacji panelu — do testow czytelnosci."""
    r, g, b = c
    return (r >> 3, g >> 2, b >> 3)
