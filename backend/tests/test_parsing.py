"""Testy normalizatora na REALNYM payloadzie z konta Max (krok 0), nie na wymyslonym.

fixtures/usage_max.json to dokladna odpowiedz /api/oauth/usage zapisana przez sonde.
"""
import json
from datetime import datetime
from pathlib import Path

import pytest

from app.parsing import (
    Observation, humanize, limit_series_key, parse_pct, parse_ts, parse_usage,
)

FIX = Path(__file__).parent / "fixtures" / "usage_max.json"
REAL = json.loads(FIX.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- prymitywy
@pytest.mark.parametrize("raw,expected", [
    (10.0, 10.0), (29, 29.0), ("73", 73.0), ("73%", 73.0), (" 12.5 % ", 12.5),
    (None, None), (True, None), ("abc", None), ({}, None),
])
def test_parse_pct(raw, expected):
    assert parse_pct(raw) == expected


def test_parse_ts_iso_z_mikrosekundami():
    # dokladnie taki format zwraca endpoint
    dt = parse_ts("2026-07-26T19:59:59.822592+00:00")
    assert dt == datetime(2026, 7, 26, 19, 59, 59, 822592)
    assert dt.tzinfo is None          # trzymamy naiwny UTC


def test_parse_ts_offset_przeliczany_na_utc():
    assert parse_ts("2026-07-26T21:59:59+02:00") == datetime(2026, 7, 26, 19, 59, 59)


def test_parse_ts_epoch_ze_statusline():
    # 1785099599 == 2026-07-26T20:59:59Z; statusline podaje epoch, endpoint ISO
    assert parse_ts(1785099599) == datetime(2026, 7, 26, 20, 59, 59)


def test_parse_ts_epoch_i_iso_daja_ten_sam_moment():
    iso = parse_ts("2026-07-26T20:59:59+00:00")
    epoch = parse_ts(1785099599)
    assert iso == epoch


def test_parse_ts_smieci_nie_wywalaja():
    for junk in (None, True, "wczoraj", "", [], {}):
        assert parse_ts(junk) is None


# --------------------------------------------------------------------------- realny payload
def test_realny_payload_ma_17_kluczy():
    r = parse_usage(REAL)
    assert len(r.seen_keys) == 17


def test_wykrywa_buckety_ktorych_nie_bylo_w_dokumentacji():
    """Piec kluczy nie bylo ani w walidatorze w binarce, ani w repo referencyjnym.
    Parser ma je przyjac bez mrugniecia — to caly sens otwartego zbioru serii."""
    r = parse_usage(REAL)
    for nowy in ("amber_ladder", "iguana_necktie", "nimbus_quill",
                 "tangelo", "omelette_promotional"):
        assert nowy in r.seen_keys


def test_puste_buckety_trafiaja_do_null_keys_a_nie_do_obserwacji():
    r = parse_usage(REAL)
    assert "seven_day_opus" in r.null_keys
    assert not [o for o in r.observations if o.bucket_key == "seven_day_opus"]


def test_five_hour_i_seven_day_maja_wartosci():
    r = parse_usage(REAL)
    by = {o.series_key: o for o in r.observations}
    assert by["bucket:five_hour"].utilization == pytest.approx(REAL["five_hour"]["utilization"])
    assert by["bucket:seven_day"].utilization == pytest.approx(REAL["seven_day"]["utilization"])
    assert by["bucket:five_hour"].resets_at is not None


def test_bucket_zachowuje_pola_dollars_w_extra():
    """limit_dollars/remaining_dollars/used_dollars sa null na Max, ale musza przetrwac
    do bazy — prawdopodobnie wypelniaja sie przy kredytach albo na Team."""
    r = parse_usage(REAL)
    by = {o.series_key: o for o in r.observations}
    assert "limit_dollars" in by["bucket:five_hour"].extra


def test_limits_daja_wlasne_serie_z_is_active_i_severity():
    r = parse_usage(REAL)
    lim = [o for o in r.observations if o.source == "limit"]
    assert len(lim) == len(REAL["limits"])
    aktywne = [o for o in lim if o.is_active]
    assert len(aktywne) == 1
    # Na koncie testowym wiazacy jest limit tygodniowy, NIE sesyjny.
    assert aktywne[0].kind == "weekly_all"
    assert all(o.severity == "normal" for o in lim)


def test_limit_scoped_niesie_nazwe_modelu():
    r = parse_usage(REAL)
    scoped = [o for o in r.observations if o.kind == "weekly_scoped"]
    assert scoped and scoped[0].model_display_name == "Fable"
    assert "fable" in scoped[0].series_key


def test_spend_jest_seria_pierwszej_kategorii():
    """Na koncie Team to JEST wiazacy limit ('org's monthly spend limit'), wiec nie moze
    byc polem pobocznym."""
    r = parse_usage(REAL)
    sp = [o for o in r.observations if o.source == "spend"]
    assert len(sp) == 1
    assert sp[0].series_key == "spend:org"
    # kwoty zostaja w jednostkach mniejszych z wykladnikiem, bez splaszczania do float
    assert sp[0].extra["used"] == {"amount_minor": 0, "currency": "USD", "exponent": 2}


def test_extra_usage_jest_seria():
    r = parse_usage(REAL)
    assert any(o.series_key == "extra:usage" for o in r.observations)


def test_realny_payload_nie_generuje_problemow():
    assert parse_usage(REAL).problems == []


def test_wszystkie_utilization_sa_w_skali_0_100():
    for o in parse_usage(REAL).observations:
        if o.utilization is not None:
            assert 0.0 <= o.utilization <= 100.0


# --------------------------------------------------------------------------- odpornosc
def test_nieznany_bucket_jest_przyjmowany():
    p = dict(REAL)
    p["zupelnie_nowy_bucket"] = {"utilization": 42.5, "resets_at": "2026-08-01T00:00:00+00:00"}
    r = parse_usage(p)
    by = {o.series_key: o for o in r.observations}
    assert by["bucket:zupelnie_nowy_bucket"].utilization == 42.5
    assert r.problems == []


def test_uszkodzone_pole_nie_przerywa_reszty():
    """Lepiej zapisac 15 z 17 serii niz odrzucic caly pomiar."""
    p = dict(REAL)
    p["five_hour"] = "nagle string"
    r = parse_usage(p)
    assert r.problems                                  # zglaszamy
    assert any(o.series_key == "bucket:seven_day" for o in r.observations)   # reszta przeszla


def test_limits_nie_bedace_lista_nie_wywalaja():
    p = dict(REAL); p["limits"] = {"nie": "lista"}
    r = parse_usage(p)
    assert r.problems and r.observations


def test_pusty_i_niepoprawny_payload():
    assert parse_usage({}).observations == []
    assert parse_usage(None).problems


def test_percent_jako_int_a_utilization_jako_float():
    """limits[].percent jest calkowite, bucket.utilization zmiennoprzecinkowe —
    obie sciezki musza dac float."""
    r = parse_usage(REAL)
    for o in r.observations:
        assert o.utilization is None or isinstance(o.utilization, float)


def test_series_key_jest_stabilny():
    a = limit_series_key("weekly_scoped", "weekly", "Fable", None)
    b = limit_series_key("weekly_scoped", "weekly", "Fable", None)
    assert a == b == "limit:weekly_scoped|weekly|fable|-"


def test_series_key_nie_przekracza_255():
    k = limit_series_key("x" * 300, "y" * 300, "z" * 300, "w" * 300)
    assert len(k) <= 255
