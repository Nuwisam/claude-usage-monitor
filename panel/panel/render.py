"""Rysowanie klatki 4a.

Renderer rysuje JEDNO logiczne plotno i nie wie o zadnym wyswietlaczu: ani o
kolejnosci bajtow, ani o obrocie, ani o tym, czy ekran umie przyjac prostokat.
Klatka powstaje w calosci za kazdym razem (~10 ms); co z niej trafi na szklo
i w jakiej postaci, rozstrzyga warstwa panelu (panel/surface.py + sterownik).
"""
from PIL import Image

from . import draw, layout as L, theme, view as V
from .pixels import pack_rgb565

# Only right angles: a display is either mounted the way the canvas is drawn or
# turned a quarter. Anything else would resample pixels and this layout is built
# out of hairlines that do not survive that.
_ROTATIONS = {90: Image.ROTATE_90, 180: Image.ROTATE_180, 270: Image.ROTATE_270}

LABEL_SESSION = "SESJA 5 H"
LABEL_WEEK = "TYDZIEŃ"
LABEL_CREDITS = "KREDYTY"


class BandState:
    """Wszystko, co pas konta ma pokazac. Skladane w app.py."""

    __slots__ = ("title", "plan", "session", "weekly", "credits",
                 "session_view", "weekly_view", "reset_session", "reset_week",
                 "ago", "note", "show_clock", "alert")

    def __init__(self, title="", plan="", session=None, weekly=None, credits=None,
                 session_view=None, weekly_view=None, reset_session=("", None),
                 reset_week=("", None), ago="", note=None, show_clock=False,
                 alert=False):
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
        # Powod blokady jednym slowem albo None. Zapala pasek akcentu na lewej krawedzi
        # pasa i przelacza nazwe konta na ACCENT_100 — czerwieni nie ma w projekcie.
        self.alert = alert


class AlertRow:
    """Jedna blokada w postaci gotowej do narysowania. Kazdy uklad karty bierze
    z tego samego zestawu pol — rozni sie tym, ktore pokazuje i jak duzo."""

    __slots__ = ("short", "project", "tool", "machine", "waited", "detail", "mode")

    def __init__(self, short="", project="", tool="", machine="", waited="",
                 detail="", mode=""):
        self.short = short          # powod jednym slowem: zgoda / pytanie / plan
        self.project = project
        self.tool = tool
        self.machine = machine
        self.waited = waited
        self.detail = detail
        self.mode = mode            # tryb uprawnien (+ typ subagenta)


class AlertState:
    """Karta przejmujaca ekran. Skladana przez `alert_state()`.

    Uklad wybiera sie LICZBA blokad, nie flaga: prog jest przy trzech, bo trzy nazwy
    projektow w 34 px nie istnieja. Stan niesie wiec wszystkie wiersze, a renderer
    rozstrzyga, ile z nich i jak duzo o kazdym zmiesci sie na 480 x 320.
    """

    __slots__ = ("title", "rows", "count", "at", "rest", "footer", "flood")

    def __init__(self, title="", rows=(), count=0, at="", rest=(), footer=None,
                 flood=False):
        self.flood = flood          # klatka PELNA: pasmo zalane akcentem plus rail
        self.title = title          # baner: CZEKA NA ZGODĘ / CZEKAJĄ · 3 / ...
        self.rows = list(rows)
        self.count = count          # WSZYSTKIE blokady, takze niewypisane
        self.at = at                # godzina najstarszego czekania NA EKRANIE
        self.rest = list(rest)      # nazwy projektow, ktore nie zmiescily sie w wierszach
        self.footer = footer        # np. informacja o niezgodnym kontrakcie


class ScreenState:
    # __slots__ jako LITERAL, nie `__slots__ += (...)`. To drugie wykonuje sie bez bledu,
    # przepisuje atrybut klasy i wywala AttributeError dopiero przy pierwszym przypisaniu.
    __slots__ = ("clock", "link", "bands", "message", "alert")

    def __init__(self, clock="", link="down", bands=(), message=None, alert=None):
        self.clock = clock
        self.link = link            # "live" | "reconnecting" | "down"
        self.bands = list(bands)
        self.message = message      # pelnoekranowy komunikat zamiast pasow
        self.alert = alert          # AlertState — bije i pasy, i komunikat


# Ile wierszy pokazuje uklad listowy. Wiecej niz trzy nie miesci sie w 282 px
# w stopniu, ktory da sie przeczytac z drugiego konca biurka.
ALERT_ROWS_MAX = 3


def alert_title(blocked):
    """Napis w pasmie. Przy jednej blokadzie zdanie o niej, przy wielu licznik.

    "3 czekają" (doslownie z makiety) nie jest polszczyzna przy pieciu — "5 czekają"
    to blad, a "5 czeka" to inna forma niz przy trzech. Gole "czekają" BEZ zwiazanego
    liczebnika jest poprawne dla kazdej licznosci, wiec liczba stoi za kropka jako
    osobny licznik, a nie jako podmiot.
    """
    if len(blocked) == 1:
        return blocked[0].title
    return "CZEKAJĄ · %d" % len(blocked)


def alert_state(blocked, now_ms=0.0, footer=None, flood=False):
    """[status.Blocked] -> AlertState. O kolejnosci rozstrzyga `status.parse_frame`,
    tutaj juz nie ma decyzji do podjecia poza tym, ile sie zmiesci."""
    from . import fmt

    if not blocked:
        return None
    shown = blocked[:ALERT_ROWS_MAX] if len(blocked) > 2 else blocked
    rows = [AlertRow(
        short=b.short,
        project=b.project or "—",
        tool=b.tool or "",
        machine=b.machine or "",
        waited=fmt.waited(fmt.ms(b.since), now_ms),
        detail=b.detail or "",
        mode=b.mode_label,
    ) for b in shown]
    # Godzina w pasmie to poczatek NAJSTARSZEGO czekania na ekranie, nie `since`
    # naglowka: kolejnosc sortuje najpierw po powodzie, wiec pierwszy wpis nie musi
    # byc najstarszy.
    stamps = [b.since for b in shown if b.since is not None]
    return AlertState(
        title=alert_title(blocked),
        rows=rows,
        count=len(blocked),
        at=fmt.hm(min(stamps)) if stamps else "",
        rest=[b.project for b in blocked[len(shown):] if b.project],
        footer=footer,
        flood=flood,
    )


def band_state(account, name=None, now_ms=0.0, show_clock=False, note=None,
               alert=False):
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
                         ago="—", alert=alert)

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
        reset_week=V.reset_note(weekly, now_ms),
        ago=age,
        note=note,
        show_clock=show_clock,
        alert=alert,
    )


class Frame:
    """One rendered image, plus whatever the drivers ask it to become.

    The renderer draws ONE logical canvas; each display then wants it rotated its
    own way and packed in its own byte order. Both are memoised here rather than
    in the drivers, so two screens of the same kind pay for the packing once and a
    rotation is computed once per frame instead of once per panel. Measured:
    transpose 0.09 ms, packing 1.4 ms, render 10.6 ms.
    """

    __slots__ = ("image", "_rot", "_packed")

    def __init__(self, image):
        self.image = image
        self._rot = {0: image}
        self._packed = {}

    def device_image(self, rotate=0):
        """The image in DEVICE space. rotate is degrees counter-clockwise."""
        img = self._rot.get(rotate)
        if img is None:
            if rotate not in _ROTATIONS:
                raise ValueError("unsupported rotation %r (have %s)"
                                 % (rotate, sorted(_ROTATIONS)))
            img = self.image.transpose(_ROTATIONS[rotate])
            self._rot[rotate] = img
        return img

    def rgb565(self, order, rotate=0):
        """Full-frame payload for a display, memoised per (order, rotation)."""
        key = (order, rotate)
        payload = self._packed.get(key)
        if payload is None:
            payload = pack_rgb565(self.device_image(rotate), order)
            self._packed[key] = payload
        return payload


class Renderer:
    def __init__(self, width=480, height=320):
        self.layout = L.Layout(width, height)

    # -- wejscie publiczne -------------------------------------------------

    def frame(self, state):
        img, d = draw.new_canvas((self.layout.width, self.layout.height))
        if state.alert is not None:
            # Alert bije takze `message`. Odwrotna kolejnosc zdegradowalaby go do
            # wiersza na dole karty bledu — a to jest jedyna rzecz na tym ekranie,
            # ktora wymaga, zebys wstal od biurka.
            self._alert(d, state.alert)
        elif state.message:
            self._message(d, state.message)
        else:
            for band_rect, band in zip(self.layout.bands, state.bands):
                if band is None:
                    self._empty_band(d, band_rect)
                else:
                    self._band(d, band_rect, band, state)
            draw.fill_rect(d, self.layout.divider, theme.DIVIDER)
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

    def _alert(self, d, a):
        """Karta przejmujaca ekran. Uklad wybiera LICZBA blokad — patrz AlertState."""
        if a.rest:
            self._alert_many(d, a)
        elif len(a.rows) >= 3:
            self._alert_list(d, a)
        elif len(a.rows) == 2:
            self._alert_pair(d, a)
        else:
            self._alert_solo(d, a)

    def _alert_banner(self, d, a, x0, x1):
        """Pasmo karty, wspolne dla wszystkich ukladow.

        Zalanie akcentem (`flood`) jest cala animacja, jaka panel ma: przerysowuje sie
        linia po linii, wiec klatek posrednich nie ma sensu liczyc — sa dwie, pusta
        i pelna. Pasmo z railem to ~13% klatki, czyli miesci sie w ticku; pelnoekranowy
        blysk bylby pelna klatka, a ta idzie na Turingu 1,87 s i wychodzi z niej powolne
        zamalowanie zamiast blysku.

        Rail stoi w OBU klatkach — zalanie tylko go przemalowuje. Pasek, ktory pojawia
        sie z niczego i znika, jest mocniejszym ruchem niz zmiana koloru, a karta ma
        dzieki temu stala lewa krawedz przez cale swoje zycie, nie tylko przez
        `alert_flash_sec`.
        """
        f_head = draw.font(L.F_BANNER)
        f_at = draw.font(L.F_BANNER_AT)
        draw.fill_rect(d, (0, 0, self.layout.width, L.BANNER_H),
                       theme.ACCENT if a.flood else theme.ACCENT_800)
        draw.fill_rect(d, (0, L.BANNER_H, L.RAIL_W, self.layout.height),
                       theme.ACCENT if a.flood else theme.NEUTRAL_900)
        # W zalanym pasmie napis schodzi na tlo karty: 5,51:1 zamiast 2,69:1.
        head_colour = theme.BG if a.flood else theme.ACCENT_100
        at_colour = theme.BG if a.flood else theme.ACCENT_200

        base = L.BANNER_BASE
        right = x1
        if a.at:
            d.text((right, base), a.at, font=f_at, fill=at_colour, anchor="rs")
            right -= draw.text_width(a.at, f_at) + 12
        draw.text_tracked(d, (x0, base),
                          draw.ellipsize_tracked(a.title, f_head, right - x0,
                                                 L.BANNER_TRACK),
                          f_head, head_colour, tracking=L.BANNER_TRACK, anchor="ls")

    def _alert_solo(self, d, a):
        """1a — jedna blokada. Nazwa projektu jest bohaterem karty."""
        L_ = self.layout.alert_solo
        row = a.rows[0]

        room = L_.x1 - L_.x0
        # Ten sam mechanizm co F_SES_NUM -> F_SES_NUM_TIGHT w pasie: dluga nazwa schodzi
        # o stopien zamiast byc obcieta w polowie.
        f_project = draw.font(L_.F_PROJECT)
        if draw.text_width(row.project, f_project) > room:
            f_project = draw.font(L_.F_PROJECT_TIGHT)
        d.text((L_.x0, L_.project_base), draw.ellipsize(row.project, f_project, room),
               font=f_project, fill=theme.TEXT, anchor="ls")

        self._alert_meta(d, (L_.x0, L_.meta_base), row, draw.font(L_.F_META), room,
                         dim_dot=True)
        d.text((L_.x0, L_.waited_base), "czeka %s" % row.waited,
               font=draw.font(L_.F_WAITED), fill=theme.ACCENT_200, anchor="ls")

        if row.detail:
            self._alert_detail(d, L_, row.detail)
        mode = row.mode
        if a.footer:
            # Niezgodny kontrakt schodzi do listwy diagnostycznej — jest diagnostyka,
            # a karta i tak bije wszystko inne.
            mode = "%s · %s" % (mode, a.footer) if mode else a.footer
        if mode:
            self._alert_mode(d, L_, mode)

        # Pasmo NA KONCU: rail schodzi po calej wysokosci, takze przez listwe trybu.
        # Rysowany wczesniej, zostalby przez nia zamalowany — w makiecie rail jest
        # `position: absolute`, wiec maluje sie nad blokami w przeplywie.
        self._alert_banner(d, a, L_.x0, L_.x1)

    def _alert_pair(self, d, a):
        """1b — dwie blokady w dwoch rownych polowach."""
        L_ = self.layout.alert_pair
        for (top, _bottom), row in zip(L_.halves, a.rows):
            self._alert_half(d, L_, top, row)
        draw.fill_rect(d, L_.divider, theme.DIVIDER)
        self._alert_banner(d, a, L_.x0, L_.x1)

    def _alert_half(self, d, L_, top, row):
        """Jedna polowa: powod i czas w jednym wierszu, pod nimi nazwa, opis i szczegol
        skrocony do JEDNEJ linii — przy dwoch blokadach nie ma miejsca na wiecej, a dwie
        linie w jednej polowie i jedna w drugiej czytalyby sie jako pierwszenstwo."""
        f_short = draw.font(L_.F_SHORT)
        f_waited = draw.font(L_.F_WAITED)
        room = L_.x1 - L_.x0

        base = top + L_.SHORT_BASE
        left = room
        if row.waited:
            d.text((L_.x1, base), row.waited, font=f_waited, fill=theme.ACCENT_200,
                   anchor="rs")
            left -= draw.text_width(row.waited, f_waited) + L_.GAP
        draw.text_tracked(d, (L_.x0, base),
                          draw.ellipsize_tracked(row.short.upper(), f_short, left,
                                                 L_.SHORT_TRACK),
                          f_short, theme.ACCENT_200, tracking=L_.SHORT_TRACK,
                          anchor="ls")

        f_project = draw.font(L_.F_PROJECT)
        d.text((L_.x0, top + L_.PROJECT_BASE),
               draw.ellipsize(row.project, f_project, room), font=f_project,
               fill=theme.TEXT, anchor="ls")

        # 58%, nie 62% jak w 1a: polowka jest ciasniejsza, wiec wiersz wtorny cichnie
        # o stopien, zeby nazwa projektu nie musiala z nim konkurowac.
        self._alert_meta(d, (L_.x0, top + L_.META_BASE), row, draw.font(L_.F_META),
                         room, colour=theme.TEXT_58)
        if row.detail:
            f_detail = draw.font(L_.F_DETAIL)
            d.text((L_.x0, top + L_.DETAIL_BASE),
                   draw.ellipsize(row.detail, f_detail, room), font=f_detail,
                   fill=theme.TEXT_70, anchor="ls")

    def _alert_list(self, d, a):
        """1c — trzy blokady w liscie, szczegol najpilniejszej w stopce."""
        L_ = self.layout.alert_list
        detail = a.rows[0].detail
        rects = L_.rows(footer=bool(detail))
        for (top, bottom), row in zip(rects, a.rows):
            self._alert_row(d, L_, top, row)
            if bottom < rects[-1][1]:
                draw.fill_rect(d, (0, bottom, L_.width, bottom + L.DIVIDER_H),
                               theme.DIVIDER)
        if detail:
            self._alert_footer(d, L_, L_.FOOT_LABEL, detail)
        self._alert_banner(d, a, L_.x0, L_.x1)

    def _alert_many(self, d, a):
        """1d — trzy najpilniejsze, reszta zliczona w stopce."""
        L_ = self.layout.alert_many
        rects = L_.rows(footer=True)
        for (top, bottom), row in zip(rects, a.rows):
            self._alert_row(d, L_, top, row, machine_only=True)
            if bottom < rects[-1][1]:
                draw.fill_rect(d, (0, bottom, L_.width, bottom + L.DIVIDER_H),
                               theme.DIVIDER)
        self._alert_rest(d, L_, a)
        self._alert_banner(d, a, L_.x0, L_.x1)

    def _alert_rest(self, d, L_, a):
        """Stopka licznika: ile jeszcze czeka i jak sie nazywaja.

        "+2 WIĘCEJ" jest nieodmienne, wiec dziala dla kazdej licznosci — inaczej niz
        "2 inne" / "5 innych".
        """
        x0, y0, x1, y1 = L_.footer
        draw.fill_rect(d, L_.footer, theme.SUNKEN)
        f_label = draw.font(L_.F_FOOT_LABEL)
        f_names = draw.font(L_.F_FOOT)
        base = y0 + L_.FOOT_BASE
        label = "+%d WIĘCEJ" % len(a.rest)
        x = L_.x0 + draw.text_tracked(d, (L_.x0, base), label, f_label,
                                      theme.ACCENT_200, tracking=1, anchor="ls")
        x += L_.FOOT_GAP
        d.text((x, base), draw.ellipsize(", ".join(a.rest), f_names, L_.x1 - x),
               font=f_names, fill=theme.TEXT_62_SUNKEN, anchor="ls")

    def _alert_row(self, d, L_, top, row, machine_only=False):
        """Jeden wiersz listy: powod w stalej kolumnie, nazwa z opisem, czas do prawej.

        Kolumna powodu jest STALA, a nie dopasowana do napisu: przy trzech wierszach oko
        czyta pionowa krawedz nazw, a nie kazdy wiersz osobno.
        """
        f_reason = draw.font(L_.F_REASON)
        f_time = draw.font(L_.F_TIME)
        draw.text_tracked(d, (L_.x0, top + L_.REASON_BASE),
                          draw.ellipsize_tracked(row.short.upper(), f_reason,
                                                 L_.REASON_W, L_.REASON_TRACK),
                          f_reason, theme.ACCENT_200, tracking=L_.REASON_TRACK,
                          anchor="ls")
        if row.waited:
            d.text((L_.x1, top + L_.TIME_BASE), row.waited, font=f_time,
                   fill=theme.ACCENT_200, anchor="rs")

        room = L_.name_x1 - L_.name_x
        f_project = draw.font(L_.F_PROJECT)
        d.text((L_.name_x, top + L_.PROJECT_BASE),
               draw.ellipsize(row.project, f_project, room), font=f_project,
               fill=theme.TEXT, anchor="ls")
        if machine_only:
            # Przy czterech blokadach narzedzie wypada: nazwa maszyny mowi, GDZIE isc,
            # a narzedzie dopiero po dojsciu — na 480 px pierwsze bije drugie.
            d.text((L_.name_x, top + L_.META_BASE),
                   draw.ellipsize(row.machine, draw.font(L_.F_META), room),
                   font=draw.font(L_.F_META), fill=theme.TEXT_50, anchor="ls")
        else:
            self._alert_meta(d, (L_.name_x, top + L_.META_BASE), row,
                             draw.font(L_.F_META), room, colour=theme.TEXT_50,
                             named=False)

    def _alert_footer(self, d, L_, label, text):
        """Stopka: jedna linia o najpilniejszej blokadzie albo licznik reszty."""
        x0, y0, x1, y1 = L_.footer
        draw.fill_rect(d, L_.footer, theme.SUNKEN)
        f_label = draw.font(L_.F_FOOT_LABEL)
        f_text = draw.font(L_.F_FOOT)
        draw.text_tracked(d, (L_.x0, y0 + L_.FOOT_LABEL_BASE), label, f_label,
                          theme.TEXT_45_SUNKEN, tracking=1, anchor="ls")
        d.text((L_.x0, y0 + L_.FOOT_TEXT_BASE),
               draw.ellipsize(text, f_text, L_.x1 - L_.x0), font=f_text,
               fill=theme.TEXT_72_SUNKEN, anchor="ls")

    def _alert_meta(self, d, xy, row, f, room, colour=theme.TEXT_62, named=True,
                    dim_dot=False):
        """Narzedzie i maszyna, na LINII BAZOWEJ podanej przez wolajacego.

        `named` przelacza "maszyna laptop" na samo "laptop": przy jednej i dwoch
        blokadach jest miejsce na slowo, ktore mowi, co ta nazwa znaczy, a w liscie
        nie ma — i tam kontekst niesie sama kolumna.

        `dim_dot` to uklad 1a i tylko on. Makieta sklada tam ten wiersz z TRZECH pudelek
        z `gap: 7px`, a kropce daje wlasny, ciemniejszy odcien — czyta sie to jako dwie
        informacje, a nie jako jedno zdanie. Uklady 1b i 1c maja w tym samym miejscu
        jeden przebieg tekstu ze zwyklymi spacjami, wiec kropka jest tam w kolorze
        wiersza. Rysowanie wszedzie wersji z 1a gubilo kropke w liscie: `TEXT_28` na tle
        karty to ~1,5:1.
        """
        x, y = xy
        name = ("maszyna %s" % row.machine) if named else row.machine
        if not dim_dot:
            czlony = [c for c in (row.tool, name) if c]
            if czlony:
                d.text((x, y), draw.ellipsize(" · ".join(czlony), f, room), font=f,
                       fill=colour, anchor="ls")
            return

        if row.tool:
            text = draw.ellipsize(row.tool, f, room)
            d.text((x, y), text, font=f, fill=colour, anchor="ls")
            x += draw.text_width(text, f)
        if name:
            if row.tool:
                d.text((x + L.META_DOT_GAP, y), "·", font=f, fill=theme.TEXT_28,
                       anchor="ls")
                x += 2 * L.META_DOT_GAP + draw.text_width("·", f)
            d.text((x, y), draw.ellipsize(name, f, xy[0] + room - x), font=f,
                   fill=colour, anchor="ls")

    def _alert_detail(self, d, L_, detail):
        """Kafel `Szczegół` — to, o co Claude pyta, a nie tylko to, ze pyta."""
        f_label = draw.font(L_.F_DETAIL_LABEL)
        f_text = draw.font(L_.F_DETAIL)
        inner = L_.x1 - L_.x0 - 2 * L_.DETAIL_PAD_X
        lines = draw.wrap_lines(detail, f_text, inner, L_.DETAIL_LINES)
        box = L_.detail_box(len(lines))
        draw.rounded(d, box, L_.DETAIL_RADIUS, fill=theme.SURFACE)
        x = box[0] + L_.DETAIL_PAD_X
        # Odcienie mieszane z tlem KAFLA, nie karty: w makiecie polprzezroczystosc
        # laduje na tym, co pod spodem, a tu pod spodem jest `SURFACE`.
        draw.text_tracked(d, (x, box[1] + L_.DETAIL_LABEL_BASE), "SZCZEGÓŁ", f_label,
                          theme.TEXT_45_SURFACE, tracking=1, anchor="ls")
        y = box[1] + L_.DETAIL_TEXT_BASE
        for line in lines:
            d.text((x, y), line, font=f_text, fill=theme.TEXT_78_SURFACE, anchor="ls")
            y += L_.DETAIL_LINE

    def _alert_mode(self, d, L_, mode):
        """Listwa diagnostyczna: dlaczego to w ogole jest pytanie."""
        f_label = draw.font(L_.F_MODE_LABEL)
        f_mode = draw.font(L_.F_MODE)
        draw.fill_rect(d, L_.mode, theme.SUNKEN)
        x = L.ALERT_PAD_X
        # Etykieta i wartosc stoja na WSPOLNEJ linii bazowej, nie kazda wysrodkowana
        # osobno: przy dwoch stopniach pisma srodek pudelka fontu wypada gdzie indziej
        # niz srodek liter i wersaliki wygladaja, jakby sie osunely.
        y = L_.MODE_BASE
        # `+ 1` to odstep miedzyliterowy ZA ostatnia litera: CSS go zostawia, a
        # `draw.text_tracked` odejmuje go od zwracanej szerokosci. Bez tego etykieta
        # ma pudelko o piksel za waskie i wartosc podchodzi jej pod sam ogon.
        x += draw.text_tracked(d, (x, y), "TRYB", f_label, theme.TEXT_45_SUNKEN,
                               tracking=1, anchor="ls") + 1 + 8
        d.text((x, y), draw.ellipsize(mode, f_mode, L_.x1 - x), font=f_mode,
               fill=theme.TEXT_70_SUNKEN, anchor="ls")

    def _empty_band(self, d, b):
        f = draw.font(13)
        d.text((b.x0, b.top + b.height // 2), "drugie konto nieskonfigurowane",
               font=f, fill=theme.TEXT_40, anchor="lm")

    def _band(self, d, b, band, state):
        if band.alert:
            # Pasek siedzi w polu marginesu (PAD_X 14), wiec uklad pasa nie drga ani
            # o piksel — i ma PELNA wysokosc pasa, niezaleznie od tego, ile wierszy
            # pas ma w srodku.
            draw.fill_rect(d, (0, b.top, L.MARK_W, b.bottom), theme.ACCENT)
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
            right -= w + L.REASON_GAP

        if band.alert:
            # Powod stoi W LINII Z PLANEM, nie przy nazwie: nazwa bywa skracana, a ten
            # napis nie moze zniknac razem z jej koncowka.
            f_reason = draw.font(L.F_REASON)
            word = band.alert.upper()
            w = draw.tracked_width(word, f_reason, 1)
            draw.text_tracked(d, (right - w, y + 5), word, f_reason,
                              theme.ACCENT_200, tracking=1)
            right -= w + 8

        room = max(20, right - b.x0)
        title = draw.ellipsize(band.title, f_name, room)
        d.text((b.x0, y), title, font=f_name,
               fill=theme.ACCENT_100 if band.alert else theme.TEXT)

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
