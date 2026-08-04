"""Srodowisko dla testow.

Ustawiane TU, a nie w linii polecenia, bo `app/config.py` tworzy `Settings()` na poziomie
modulu — wartosc, ktora przyjdzie pozniej niz pierwszy import `app.*`, nie zostanie
zauwazona. conftest.py katalogu jest wczytywany przed zebraniem lezacych w nim testow,
czyli przed tym importem.

Zwykle przypisanie, NIE `setdefault`: przypadkowe AUTH_MODE albo ALLOWED_EMAILS w powloce
programisty decydowaloby wtedy o tym, co ten zestaw w ogole sprawdza, a wynik rozjezdzalby
sie miedzy maszynami bez sladu w repozytorium.
"""
import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["INGEST_TOKENS"] = "t:m"
os.environ["ALLOWED_EMAILS"] = "a@b.pl"
# Domyslny tryb zestawu. Testy, ktore sprawdzaja same tryby, podmieniaja `settings`
# punktowo — patrz tests/test_auth_modes.py.
os.environ["AUTH_MODE"] = "none"
