"""Drawing frame 4a.

The renderer draws ONE logical canvas and knows nothing about any display: not
the byte order, not the rotation, not whether the screen can take a rectangle.
The frame is built whole every time (~10 ms); what of it reaches the glass, and
in what form, is settled by the panel layer (panel/surface.py + driver).
"""
from PIL import Image

from . import draw, layout as L, theme, view as V
from .pixels import pack_rgb565

# Only right angles: a display is either mounted the way the canvas is drawn or
# turned a quarter. Anything else would resample pixels and this layout is built
# out of hairlines that do not survive that.
_ROTATIONS = {90: Image.ROTATE_90, 180: Image.ROTATE_180, 270: Image.ROTATE_270}

LABEL_SESSION = "SESSION 5 H"
LABEL_WEEK = "WEEK"
LABEL_CREDITS = "CREDITS"


class BandState:
    """Everything the account band has to show. Assembled in app.py."""

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
        # The block reason in a single word, or None. Lights the accent bar on the left
        # edge of the band and switches the account name to ACCENT_100 — no red here.
        self.alert = alert


class AlertRow:
    """A single block in a form ready to draw. Every card layout takes from the same
    set of fields — they differ in which ones they show and in how much."""

    __slots__ = ("short", "project", "tool", "machine", "waited", "detail", "mode")

    def __init__(self, short="", project="", tool="", machine="", waited="",
                 detail="", mode=""):
        self.short = short          # the reason in one word: allow / question / plan
        self.project = project
        self.tool = tool
        self.machine = machine
        self.waited = waited
        self.detail = detail
        self.mode = mode            # permission mode (+ subagent type)


class AlertState:
    """The card that takes over the screen. Assembled by `alert_state()`.

    The layout is chosen by the NUMBER of blocks, not by a flag: the threshold is at
    three, because three project names at 34 px do not exist. So the state carries all
    the rows, and the renderer settles how many of them, and how much about each, fits
    on 480 x 320.
    """

    __slots__ = ("title", "rows", "count", "at", "rest", "footer", "flood")

    def __init__(self, title="", rows=(), count=0, at="", rest=(), footer=None,
                 flood=False):
        self.flood = flood          # the FULL frame: banner flooded with accent, plus rail
        self.title = title          # banner: NEEDS PERMISSION / WAITING · 3 / ...
        self.rows = list(rows)
        self.count = count          # ALL blocks, including the unlisted ones
        self.at = at                # the hour of the oldest wait ON SCREEN
        self.rest = list(rest)      # names of the projects that did not fit into the rows
        self.footer = footer        # e.g. a note about a contract mismatch


class ScreenState:
    # __slots__ as a LITERAL, not `__slots__ += (...)`. The latter runs without an error,
    # rewrites the class attribute and blows up with AttributeError only on the first
    # assignment.
    __slots__ = ("clock", "link", "bands", "message", "alert")

    def __init__(self, clock="", link="down", bands=(), message=None, alert=None):
        self.clock = clock
        self.link = link            # "live" | "reconnecting" | "down"
        self.bands = list(bands)
        self.message = message      # a full-screen message instead of the bands
        self.alert = alert          # AlertState — beats both the bands and the message


# How many rows the list layout shows. More than three does not fit into 282 px at a
# size that can be read from the far end of the desk.
ALERT_ROWS_MAX = 3


def alert_title(blocked):
    """The banner caption. With one block, a sentence about it; with several, a counter.

    "3 waiting" (the mockup's literal form) does not survive every count in the language
    this was written for: the bare verb WITHOUT a bound numeral is correct for any count,
    while a numeral forces a form that changes with it. So the number comes after a dot
    as a separate counter and not as a subject — a shape that reads the same at three and
    at five, which is why it stays.
    """
    if len(blocked) == 1:
        return blocked[0].title
    return "WAITING · %d" % len(blocked)


def alert_state(blocked, now_ms=0.0, footer=None, flood=False):
    """[status.Blocked] -> AlertState. The order is settled by `status.parse_frame`;
    there is no decision left to take here beyond how much fits."""
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
    # The hour in the banner is the start of the OLDEST wait on screen, not the `since`
    # of the heading: rows run youngest first, so the first entry is by design the
    # newest — and the banner has to say how long all of this has been waiting.
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
    """model.AccountStatus -> BandState. The ONLY place this transition happens, shared
    by the client and by tools/render-png.py — otherwise the diagnostic tool would
    show something other than the panel."""
    from . import fmt

    if account is None:
        # A band with no frame says outright that there is no data. A bare clock
        # glyph with no text would look like a drawing bug, not like information.
        why = note or "no data from server"
        return BandState(title=name or "—", note=note, show_clock=show_clock,
                         reset_session=(why, None), reset_week=(why, None),
                         ago="—", alert=alert)

    session = V.pick_session(account.series)
    weekly = V.pick_weekly(account.series)
    credits = V.credits(account.rung("credits"))

    # The age is taken from the CONFIRMATION, not from the sample's write: dedup
    # does not write a sample when the value has not changed, so `capturedAt` is at
    # times hours older than the last measurement (frontend/src/lib/freshness.ts:37-38).
    #
    # From the OLDER of the two windows, not from whichever comes first. This label
    # is the ONLY carrier of freshness (see view.py) and it sits next to the
    # session row only — while the backend confirms every series SEPARATELY, so the
    # week is at times days older than the session. Taking the session's stamp, the
    # panel would write "3 s ago" right next to a confident-looking week bar from
    # three days back. The age may overstate staleness, never freshness.
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

    # -- public entry ------------------------------------------------------

    def frame(self, state):
        img, d = draw.new_canvas((self.layout.width, self.layout.height))
        if state.alert is not None:
            # The alert beats `message` as well. The reverse order would demote it to
            # a line at the bottom of an error card — and it is the one thing on this
            # screen that calls for getting up from the desk.
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

    # -- parts -------------------------------------------------------------

    def _message(self, d, message):
        """A full-screen status card. The panel is a device — an error has to be visible
        ON IT, not only in a log nobody opens."""
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
        """The card that takes over the screen. The layout is chosen by the NUMBER of
        blocks — see AlertState."""
        if a.rest:
            self._alert_many(d, a)
        elif len(a.rows) >= 3:
            self._alert_list(d, a)
        elif len(a.rows) == 2:
            self._alert_pair(d, a)
        else:
            self._alert_solo(d, a)

    def _alert_banner(self, d, a, x0, x1):
        """The card's banner, shared by every layout.

        The accent flood (`flood`) is the whole animation this panel has: it redraws line
        by line, so there is no point counting intermediate frames — there are two, empty
        and full. The banner with the rail is ~13% of the frame, so it fits inside a
        tick; a full-screen flash would be a full frame, and that one takes 1.87 s on the
        Turing and comes out as a slow repaint instead of a flash.

        The rail is present in BOTH frames — the flood only repaints it. A bar that appears
        out of nothing and disappears is a stronger movement than a change of colour, and
        it gives the card a fixed left edge for its whole life, not only for
        `alert_flash_sec`.
        """
        f_head = draw.font(L.F_BANNER)
        f_at = draw.font(L.F_BANNER_AT)
        draw.fill_rect(d, (0, 0, self.layout.width, L.BANNER_H),
                       theme.ACCENT if a.flood else theme.ACCENT_800)
        draw.fill_rect(d, (0, L.BANNER_H, L.RAIL_W, self.layout.height),
                       theme.ACCENT if a.flood else theme.NEUTRAL_900)
        # In a flooded banner the caption drops to the card's background: 5.51:1
        # instead of 2.69:1.
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
        """1a — one block. The project name is the card's hero."""
        L_ = self.layout.alert_solo
        row = a.rows[0]

        room = L_.x1 - L_.x0
        # The same mechanism as F_SES_NUM -> F_SES_NUM_TIGHT in the band: a long name
        # drops one size instead of being cut in half.
        f_project = draw.font(L_.F_PROJECT)
        if draw.text_width(row.project, f_project) > room:
            f_project = draw.font(L_.F_PROJECT_TIGHT)
        d.text((L_.x0, L_.project_base), draw.ellipsize(row.project, f_project, room),
               font=f_project, fill=theme.TEXT, anchor="ls")

        self._alert_meta(d, (L_.x0, L_.meta_base), row, draw.font(L_.F_META), room,
                         dim_dot=True)
        d.text((L_.x0, L_.waited_base), "waiting %s" % row.waited,
               font=draw.font(L_.F_WAITED), fill=theme.ACCENT_200, anchor="ls")

        if row.detail:
            self._alert_detail(d, L_, row.detail)
        mode = row.mode
        if a.footer:
            # A contract mismatch drops into the diagnostic strip — it IS diagnostics,
            # and the card beats everything else anyway.
            mode = "%s · %s" % (mode, a.footer) if mode else a.footer
        if mode:
            self._alert_mode(d, L_, mode)

        # The banner LAST: the rail runs the full height, through the mode strip too.
        # Drawn earlier, it would be painted over by that strip — in the mockup the
        # rail is `position: absolute`, so it paints above the blocks in the flow.
        self._alert_banner(d, a, L_.x0, L_.x1)

    def _alert_pair(self, d, a):
        """1b — two blocks in two equal halves."""
        L_ = self.layout.alert_pair
        for (top, _bottom), row in zip(L_.halves, a.rows):
            self._alert_half(d, L_, top, row)
        draw.fill_rect(d, L_.divider, theme.DIVIDER)
        self._alert_banner(d, a, L_.x0, L_.x1)

    def _alert_half(self, d, L_, top, row):
        """One half: reason and time on one line, below them the name, the meta line and
        the detail cut to ONE line — with two blocks there is no room for more, and two
        lines in one half against one in the other would read as precedence."""
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

        # 58%, not 62% as in 1a: the half is tighter, so the secondary line quietens
        # by one step, so that the project name does not have to compete with it.
        self._alert_meta(d, (L_.x0, top + L_.META_BASE), row, draw.font(L_.F_META),
                         room, colour=theme.TEXT_58)
        if row.detail:
            f_detail = draw.font(L_.F_DETAIL)
            d.text((L_.x0, top + L_.DETAIL_BASE),
                   draw.ellipsize(row.detail, f_detail, room), font=f_detail,
                   fill=theme.TEXT_70, anchor="ls")

    def _alert_list(self, d, a):
        """1c — three blocks in a list, the newest one's detail in the footer."""
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
        """1d — the three newest, the rest counted in the footer."""
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
        """The counter footer: how many more are waiting and what they are called.

        "+2 MORE" does not inflect, so it works for every count — unlike a noun phrase
        that would have to agree with the number.
        """
        x0, y0, x1, y1 = L_.footer
        draw.fill_rect(d, L_.footer, theme.SUNKEN)
        f_label = draw.font(L_.F_FOOT_LABEL)
        f_names = draw.font(L_.F_FOOT)
        base = y0 + L_.FOOT_BASE
        label = "+%d MORE" % len(a.rest)
        x = L_.x0 + draw.text_tracked(d, (L_.x0, base), label, f_label,
                                      theme.ACCENT_200, tracking=1, anchor="ls")
        x += L_.FOOT_GAP
        d.text((x, base), draw.ellipsize(", ".join(a.rest), f_names, L_.x1 - x),
               font=f_names, fill=theme.TEXT_62_SUNKEN, anchor="ls")

    def _alert_row(self, d, L_, top, row, machine_only=False):
        """One list row: the reason in a fixed column, the name with its meta line, the
        time to the right.

        The reason column is FIXED, not fitted to the caption: at three rows the eye
        reads the vertical edge of the names, not each row separately.
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
            # At four blocks the tool drops out: the machine name says WHERE to go, and
            # the tool only once there — at 480 px the first beats the second.
            d.text((L_.name_x, top + L_.META_BASE),
                   draw.ellipsize(row.machine, draw.font(L_.F_META), room),
                   font=draw.font(L_.F_META), fill=theme.TEXT_50, anchor="ls")
        else:
            self._alert_meta(d, (L_.name_x, top + L_.META_BASE), row,
                             draw.font(L_.F_META), room, colour=theme.TEXT_50,
                             named=False)

    def _alert_footer(self, d, L_, label, text):
        """Footer: one line about the newest block, or a counter for the rest."""
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
        """The tool and the machine, on the BASELINE given by the caller.

        `named` switches "machine laptop" to a bare "laptop": with one and with two
        blocks there is room for the word that says what that name means, and in the
        list there is not — there the column itself carries the context.

        `dim_dot` is layout 1a and only it. The mockup composes that line there out of
        THREE boxes with `gap: 7px` and gives the dot its own, darker shade — it reads
        as two pieces of information, not as one sentence. Layouts 1b and 1c have one
        run of text with ordinary spaces in the same place, so the dot is in the row's
        colour there. Drawing 1a's version everywhere lost the dot in the list:
        `TEXT_28` on the card's background is ~1.5:1.
        """
        x, y = xy
        name = ("machine %s" % row.machine) if named else row.machine
        if not dim_dot:
            parts = [c for c in (row.tool, name) if c]
            if parts:
                d.text((x, y), draw.ellipsize(" · ".join(parts), f, room), font=f,
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
        """The `Detail` tile — what Claude is asking about, not merely that it asks."""
        f_label = draw.font(L_.F_DETAIL_LABEL)
        f_text = draw.font(L_.F_DETAIL)
        inner = L_.x1 - L_.x0 - 2 * L_.DETAIL_PAD_X
        lines = draw.wrap_lines(detail, f_text, inner, L_.DETAIL_LINES)
        box = L_.detail_box(len(lines))
        draw.rounded(d, box, L_.DETAIL_RADIUS, fill=theme.SURFACE)
        x = box[0] + L_.DETAIL_PAD_X
        # Shades mixed with the TILE's background, not the card's: in the mockup
        # translucency lands on whatever is underneath, and here that is `SURFACE`.
        draw.text_tracked(d, (x, box[1] + L_.DETAIL_LABEL_BASE), "DETAIL", f_label,
                          theme.TEXT_45_SURFACE, tracking=1, anchor="ls")
        y = box[1] + L_.DETAIL_TEXT_BASE
        for line in lines:
            d.text((x, y), line, font=f_text, fill=theme.TEXT_78_SURFACE, anchor="ls")
            y += L_.DETAIL_LINE

    def _alert_mode(self, d, L_, mode):
        """The diagnostic strip: why this is a question at all."""
        f_label = draw.font(L_.F_MODE_LABEL)
        f_mode = draw.font(L_.F_MODE)
        draw.fill_rect(d, L_.mode, theme.SUNKEN)
        x = L.ALERT_PAD_X
        # The label and the value sit on a COMMON baseline, not each centred on its
        # own: with two type sizes the centre of the font box falls elsewhere than the
        # centre of the letters, and the caps look as if they had slipped down.
        y = L_.MODE_BASE
        # `+ 1` is the letter spacing AFTER the last letter: CSS leaves it in, while
        # `draw.text_tracked` subtracts it from the width it returns. Without it the
        # label has a box one pixel too narrow and the value creeps up against its tail.
        x += draw.text_tracked(d, (x, y), "MODE", f_label, theme.TEXT_45_SUNKEN,
                               tracking=1, anchor="ls") + 1 + 8
        d.text((x, y), draw.ellipsize(mode, f_mode, L_.x1 - x), font=f_mode,
               fill=theme.TEXT_70_SUNKEN, anchor="ls")

    def _empty_band(self, d, b):
        f = draw.font(13)
        d.text((b.x0, b.top + b.height // 2), "second account not configured",
               font=f, fill=theme.TEXT_40, anchor="lm")

    def _band(self, d, b, band, state):
        if band.alert:
            # The bar sits in the margin field (PAD_X 14), so the band's layout does not
            # shift by a single pixel — and it has the band's FULL height, whatever the
            # number of rows the band has inside.
            draw.fill_rect(d, (0, b.top, L.MARK_W, b.bottom), theme.ACCENT)
        self._header(d, b, band, state)
        self._window(d, b, band, kind="session")
        self._window(d, b, band, kind="week")
        # We draw everything except "off and with no amounts" — there is nothing to show
        # there. Credits cut off by the organization DO have amounts (the last
        # measurement) and the row stays.
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
            # The reason sits ON THE PLAN'S LINE, not next to the name: the name gets
            # shortened at times, and this caption must not vanish with its tail.
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
        """A filled dot = live, a ring = reconnecting, a crossed ring = down.

        The difference is in the DRAWING, not in the colour: when the stream dies, the
        reading age grows on both accounts at once and that looks exactly like "the
        work stopped".
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

        # --- the percent column ---
        self._number(d, b, v, centre,
                     big=L.F_SES_NUM if session else L.F_WK_NUM,
                     tight=L.F_SES_NUM_TIGHT if session else L.F_WK_NUM,
                     small=L.F_SES_PCT if session else L.F_WK_PCT)

        # --- the label ---
        f_label = draw.font(L.F_LABEL)
        label = LABEL_SESSION if session else LABEL_WEEK
        colour = theme.ACCENT_200 if session else theme.TEXT_60
        draw.text_tracked(d, (label_box[0], label_box[1]), label, f_label,
                          colour, tracking=1)

        # --- the bar ---
        draw.bar(d, bar_box, v,
                 theme.ACCENT if session else theme.ACCENT_500)

        # --- the caption under the bar ---
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
        """The number and the % sign, aligned to the RIGHT edge of the narrow column.

        With `unknown` the % sign MUST vanish — "unknown %" is a real trap for a naive
        port, because there the percent is part of the template, not of the data.
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
            # The arrow: the week stands at 100%, so credits are now the rung that
            # does the limiting.
            draw.arrow_down_right(d, (lx - 11, cy - 4, lx - 4, cy + 2), theme.ACCENT_300)

        x = b.block_x0
        if c.state == "unknown":
            d.text((x, cy), "no data", font=f_limit, fill=theme.TEXT_45, anchor="lm")
            x += draw.text_width("no data", f_limit) + 8
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
