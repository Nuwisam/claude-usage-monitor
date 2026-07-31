"""Rysowanie klatki 4a.

Panel przyjmuje WYLACZNIE pelne klatki (patrz naglowek ax206.py), wiec nie ma tu
zadnego sledzenia brudnych obszarow — klatka powstaje w calosci za kazdym razem.
Oszczednosc jest gdzie indziej: `Frame.payload` porownuje sie z ostatnio wyslana
i identycznej nie wysylamy. Renderowanie kosztuje ~10 ms, transfer 376 ms.
"""
from . import draw, layout as L, theme, view as V
from .ax206 import image_to_rgb565

LABEL_SESSION = "SESJA 5 H"
LABEL_WEEK = "TYDZIEŃ"
LABEL_CREDITS = "KREDYTY"


class BandState:
    """Wszystko, co pas konta ma pokazac. Skladane w app.py."""

    __slots__ = ("title", "plan", "session", "weekly", "credits",
                 "session_view", "weekly_view", "reset_session", "reset_week",
                 "ago", "note", "show_clock")

    def __init__(self, title="", plan="", session=None, weekly=None, credits=None,
                 session_view=None, weekly_view=None, reset_session=("", None),
                 reset_week=("", None), ago="", note=None, show_clock=False):
        self.title = title
        self.plan = plan
        self.session = session
        self.weekly = weekly
        self.credits = credits
        self.session_view = session_view or V.missing_view()
        self.weekly_view = weekly_view or V.missing_view()
        self.reset_session = reset_session
        self.reset_week = reset_week
        self.ago = ago
        self.note = note
        self.show_clock = show_clock


class ScreenState:
    __slots__ = ("clock", "link", "bands", "message")

    def __init__(self, clock="", link="down", bands=(), message=None):
        self.clock = clock
        self.link = link            # "live" | "reconnecting" | "down"
        self.bands = list(bands)
        self.message = message      # pelnoekranowy komunikat zamiast pasow


def band_state(account, name=None, now_ms=0.0, show_clock=False, note=None):
    """model.AccountStatus -> BandState. JEDYNE miejsce tego przejscia, wspolne
    dla klienta i dla tools/render-png.py — inaczej narzedzie diagnostyczne
    pokazywaloby cos innego niz panel."""
    from . import fmt

    if account is None:
        # Pas bez ramki mowi wprost, ze nie ma danych. Sama ikonka zegara bez
        # tekstu wygladalaby jak blad rysowania, a nie jak informacja.
        why = note or "brak danych z serwera"
        return BandState(title=name or "—", note=note, show_clock=show_clock,
                         reset_session=(why, None), reset_week=(why, None),
                         ago="—")

    session = V.pick_session(account.series)
    weekly = V.pick_weekly(account.series)
    credits = V.credits(account.rung("credits"))

    # Wiek liczymy z POTWIERDZENIA, nie z zapisu probki: dedup nie zapisuje
    # probki przy niezmienionej wartosci, wiec `capturedAt` bywa o godziny
    # starsze niz ostatni pomiar (frontend/src/lib/freshness.ts:37-38).
    #
    # Ze STARSZEGO z dwoch okien, nie z pierwszego lepszego. Ta etykieta jest
    # JEDYNYM nosnikiem swiezosci (patrz view.py) i stoi tylko przy wierszu
    # sesji — a backend potwierdza kazda serie OSOBNO, wiec tydzien bywa o dni
    # starszy niz sesja. Biorac stempel sesji, panel pisalby "przed chwila" tuz
    # obok pewnie wygladajacego paska tygodnia sprzed trzech dni. Wiek ma prawo
    # przesadzac w strone starosci, nigdy w strone swiezosci.
    moments = []
    for candidate in (session, weekly):
        if candidate is not None:
            moment = fmt.parse_utc(candidate.confirmed_at or candidate.captured_at)
            if moment is not None:
                moments.append(moment)
    if not moments:
        moment = fmt.parse_utc(account.last_sample_at)
        if moment is not None:
            moments.append(moment)
    age = fmt.ago(fmt.ms(min(moments)), now_ms) if moments else "—"

    return BandState(
        title=name or account.title,
        plan=V.plan_label(account),
        session=session,
        weekly=weekly,
        credits=credits,
        session_view=V.describe_series(session) if session else V.missing_view(),
        weekly_view=V.describe_series(weekly) if weekly else V.missing_view(),
        reset_session=V.reset_note(session, now_ms),
        reset_week=V.reset_note(weekly, now_ms, with_day=True),
        ago=age,
        note=note,
        show_clock=show_clock,
    )


class Frame:
    __slots__ = ("image", "payload")

    def __init__(self, image):
        self.image = image
        self.payload = image_to_rgb565(image)


class Renderer:
    def __init__(self, width=480, height=320):
        self.layout = L.Layout(width, height)

    # -- wejscie publiczne -------------------------------------------------

    def frame(self, state):
        img, d = draw.new_canvas((self.layout.width, self.layout.height))
        if state.message:
            self._message(d, state.message)
        else:
            for band_rect, band in zip(self.layout.bands, state.bands):
                if band is None:
                    self._empty_band(d, band_rect)
                else:
                    self._band(d, band_rect, band, state)
            d.rectangle(self.layout.divider, fill=theme.DIVIDER)
        return Frame(img)

    # -- czesci ------------------------------------------------------------

    def _message(self, d, message):
        """Pelnoekranowa karta stanu. Panel jest urzadzeniem — blad ma byc widoczny
        NA NIM, nie tylko w logu, ktorego nikt nie otwiera."""
        title, *rest = message if isinstance(message, (list, tuple)) else [message]
        f_title = draw.font(24)
        f_body = draw.font(15)
        d.text((L.PAD_X, 28), draw.ellipsize(title, f_title,
                                             self.layout.width - 2 * L.PAD_X),
               font=f_title, fill=theme.TEXT)
        y = 72
        for line in rest:
            d.text((L.PAD_X, y), draw.ellipsize(line, f_body,
                                                self.layout.width - 2 * L.PAD_X),
                   font=f_body, fill=theme.TEXT_60)
            y += 22

    def _empty_band(self, d, b):
        f = draw.font(13)
        d.text((b.x0, b.top + b.height // 2), "drugie konto nieskonfigurowane",
               font=f, fill=theme.TEXT_40, anchor="lm")

    def _band(self, d, b, band, state):
        self._header(d, b, band, state)
        self._window(d, b, band, kind="session")
        self._window(d, b, band, kind="week")
        # Rysujemy wszystko poza "wylaczone i bez kwot" — tam nie ma czego pokazac.
        # Kredyty odciete przez organizacje MAJA kwoty (ostatni pomiar) i wiersz zostaje.
        if band.credits is not None and not (band.credits.state == "off"
                                             and band.credits.used is None):
            self._credits(d, b, band.credits)

    def _header(self, d, b, band, state):
        f_name = draw.font(L.F_NAME)
        f_plan = draw.font(L.F_PLAN)
        f_clock = draw.font(L.F_CLOCK)
        y = b.header[1]
        right = b.x1

        if band.show_clock:
            self._link_mark(d, (right - 4, y + 9), state.link)
            right -= 14
            w = draw.text_width(state.clock, f_clock)
            d.text((right, y + 1), state.clock, font=f_clock, fill=theme.TEXT_78,
                   anchor="ra")
            right -= w + 8

        if band.plan:
            w = draw.tracked_width(band.plan.upper(), f_plan, 1)
            draw.text_tracked(d, (right - w, y + 5), band.plan.upper(), f_plan,
                              theme.TEXT_50, tracking=1)
            right -= w + 8

        title = draw.ellipsize(band.title, f_name, max(20, right - b.x0))
        d.text((b.x0, y), title, font=f_name, fill=theme.TEXT)

    def _link_mark(self, d, centre, link):
        """Kropka pelna = na zywo, pierscien = wznawiam, przekreslona = brak.

        Roznica RYSUNKIEM, nie kolorem: gdy strumien padnie, wiek odczytu rosnie
        obu kontom naraz i wyglada to identycznie jak "przestales pracowac".
        """
        if link == "live":
            draw.dot(d, centre, 3, theme.ACCENT)
        elif link == "reconnecting":
            draw.ring(d, centre, 3, theme.ACCENT_300)
        else:
            draw.ring(d, centre, 3, theme.NEUTRAL_600)
            draw.cross(d, centre, 4, theme.NEUTRAL_600)

    def _window(self, d, b, band, kind):
        session = kind == "session"
        v = band.session_view if session else band.weekly_view
        bar_box = b.ses_bar if session else b.wk_bar
        label_box = b.ses_label if session else b.wk_label
        line_box = b.ses_line if session else b.wk_line
        centre = b.ses_centre if session else b.wk_centre
        lead, at = band.reset_session if session else band.reset_week

        # --- kolumna procentu ---
        self._number(d, b, v, centre,
                     big=L.F_SES_NUM if session else L.F_WK_NUM,
                     tight=L.F_SES_NUM_TIGHT if session else L.F_WK_NUM,
                     small=L.F_SES_PCT if session else L.F_WK_PCT)

        # --- etykieta ---
        f_label = draw.font(L.F_LABEL)
        label = LABEL_SESSION if session else LABEL_WEEK
        colour = theme.ACCENT_200 if session else theme.TEXT_60
        draw.text_tracked(d, (label_box[0], label_box[1]), label, f_label,
                          colour, tracking=1)

        # --- pasek ---
        draw.bar(d, bar_box, v,
                 theme.ACCENT if session else theme.ACCENT_500)

        # --- podpis pod paskiem ---
        f_reset = draw.font(L.F_RESET)
        x = line_box[0]
        gy = line_box[1] + L.LINE_H // 2
        draw.clock_glyph(d, (x + 5, gy), 5,
                         theme.ACCENT_300 if session else theme.mix(theme.ACCENT_300, 70))
        x += 15
        text = lead if not at else "%s · %s" % (lead, at)
        room = line_box[2] - x - (86 if session else 0)
        d.text((x, gy), draw.ellipsize(text, f_reset, room), font=f_reset,
               fill=theme.TEXT_70 if session else theme.TEXT_60, anchor="lm")

        if session and band.ago:
            f_ago = draw.font(L.F_AGO)
            w = draw.text_width(band.ago, f_ago)
            d.text((line_box[2], gy), band.ago, font=f_ago, fill=theme.TEXT_52,
                   anchor="rm")
            draw.dot(d, (line_box[2] - w - 8, gy), 2, theme.ACCENT)

    def _number(self, d, b, v, centre, big, tight, small):
        """Liczba i znak %, wyrownane do PRAWEJ krawedzi waskiej kolumny.

        Przy `nie wiem` znak % MUSI zniknac — "nie wiem %" to realna pulapka
        naiwnego portu, bo procent jest tam czescia szablonu, nie danych.
        """
        if v.number is None:
            f = draw.font(L.F_WORDS)
            base = draw.baseline_for_centre(f, v.words or "?", centre)
            d.text((b.num_right, base), v.words or "?", font=f,
                   fill=theme.TEXT_50, anchor="rs")
            return

        size = tight if len(v.number) >= 3 else big
        f_num = draw.font(size)
        f_pct = draw.font(small)
        base = draw.baseline_for_centre(f_num, v.number, centre)
        pct_w = draw.text_width("%", f_pct)
        d.text((b.num_right, base), "%", font=f_pct, fill=theme.TEXT_55, anchor="rs")
        d.text((b.num_right - pct_w - L.PCT_GAP, base), v.number, font=f_num,
               fill=theme.TEXT, anchor="rs")

    def _credits(self, d, b, c):
        x0, y0, x1, y1 = b.credits
        cy = b.credits_centre
        f_label = draw.font(L.F_LABEL)
        f_used = draw.font(L.F_CREDITS_USED)
        f_limit = draw.font(L.F_CREDITS_LIMIT)

        label_colour = theme.ACCENT_200 if c.is_current else theme.TEXT_50
        w = draw.tracked_width(LABEL_CREDITS, f_label, 1)
        lx = b.num_right - w
        draw.text_tracked(d, (lx, cy - 5), LABEL_CREDITS, f_label, label_colour,
                          tracking=1)
        if c.is_current:
            # Strzalka: tydzien stoi na 100%, wiec to kredyty sa teraz szczeblem,
            # ktory Cie ogranicza.
            draw.arrow_down_right(d, (lx - 11, cy - 4, lx - 4, cy + 2), theme.ACCENT_300)

        x = b.block_x0
        if c.state == "unknown":
            d.text((x, cy), "brak danych", font=f_limit, fill=theme.TEXT_45, anchor="lm")
            x += draw.text_width("brak danych", f_limit) + 8
            draw.dashed_rounded(d, (x, cy - 2, x1, cy + 2), 2, theme.TEXT_25)
            return

        d.text((x, cy), c.used or "—", font=f_used, fill=theme.TEXT, anchor="lm")
        x += draw.text_width(c.used or "—", f_used) + 4
        tail = "/ %s %s" % (c.limit or "—", c.currency or "")
        d.text((x, cy + 1), tail.strip(), font=f_limit, fill=theme.TEXT_45, anchor="lm")
        x += draw.text_width(tail.strip(), f_limit) + 10

        if x < x1 - 20:
            bar_y = cy - L.CREDITS_BAR_H // 2
            box = (x, bar_y, x1, bar_y + L.CREDITS_BAR_H)
            draw.rounded(d, box, L.CREDITS_BAR_H // 2, fill=theme.NEUTRAL_900)
            w = int(round((x1 - x) * c.bar_pct / 100.0))
            if w > 0:
                draw.rounded(d, (x, bar_y, x + max(w, L.CREDITS_BAR_H),
                                 bar_y + L.CREDITS_BAR_H),
                             L.CREDITS_BAR_H // 2, fill=theme.ACCENT_500)
