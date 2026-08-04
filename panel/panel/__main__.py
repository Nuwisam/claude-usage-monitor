"""CLI panelu.

    python -m panel                 petla (to samo, co robi zadanie harmonogramu)
    python -m panel --list          jakie moduly widac i na ktorych portach
    python -m panel --identify 0    namaluj wielki znak na wskazanym module
    python -m panel --probe         karta testowa: kolory, paski, ogonki
    python -m panel --once          jedna klatka z prawdziwych danych i wyjscie
"""
import argparse
import sys
import time

from . import config as C, device, draw, drivers, log as logmod, render, theme
from .drivers.base import DriverError
from .app import AlreadyRunning


def _options(args):
    """(cfg, driver options). The configuration is optional here: these commands
    have to work on a machine where panel.json is missing or broken - that is
    often exactly why someone is running them."""
    try:
        cfg = C.load()
    except C.ConfigError:
        cfg = C.Config({})
    opts = device.options_for(cfg)
    if args.dll:
        opts["dll"] = args.dll
    return cfg, opts


def _parse_id(text):
    """"ax206#0" -> ("ax206", 0). A bare number means ax206#N - a documented
    default for the single-driver era, not a guess about what is plugged in."""
    text = str(text)
    if "#" in text:
        backend, _, index = text.partition("#")
    else:
        backend, index = "ax206", text
    try:
        return backend, int(index)
    except ValueError:
        raise DriverError("nie rozumiem wskazania %r (oczekuje np. ax206#0)" % text)


def cmd_list(args):
    cfg, opts = _options(args)
    total = 0
    lines = []
    for name in drivers.known():
        why = drivers.REGISTRY[name].unavailable(opts)
        if why:
            print("%s — niedostepny: %s" % (name, why))
            continue
        targets = device.list_targets(name, opts)
        try:
            print("%s: %d" % (name, len(targets)))
            for t in targets:
                print("  %s" % t.describe())
                lines.append('    {"backend": "%s", "port_path": "%s"}'
                             % (name, t.port_path or "?"))
            total += len(targets)
        finally:
            device.release(targets)
    if not total:
        print("\n(nic — czy ekran jest wpiety i ma sterownik?)")
        return 1
    if total > 1:
        # The port chain comes from the same enumeration as the handle, so nothing
        # here is guessed. `--identify` exists only so you can see WITH YOUR EYES
        # which screen on the desk is which - no read can settle that.
        print("\nKtory to ktory na biurku rozrozni tylko wzrok:")
        for name in drivers.known():
            for t in device.list_targets(name, opts):
                print("  python -m panel --identify %s" % t.id)
    print("\nDo panel.json:")
    print('  "panels": [\n%s\n  ]' % ",\n".join(lines))
    return 0


def cmd_identify(args):
    """Paint a big number on one screen. Goes through the same driver path the
    client uses, so rotation and byte order are exercised, not bypassed."""
    cfg, opts = _options(args)
    backend, index = _parse_id(args.identify)
    spec = C.PanelSpec(backend, {"index": index})
    dev = device.open_panel(spec, opts)
    try:
        caps = dev.caps
        dev.set_brightness(caps.brightness.hi)
        width, height = caps.canvas
        img, d = draw.new_canvas((width, height))
        d.text((width // 2, height // 2 - 10), str(index),
               font=draw.font(190), fill=theme.ACCENT, anchor="mm")
        d.text((width // 2, height - 40), "%s#%d" % (backend, index),
               font=draw.font(16), fill=theme.TEXT_60, anchor="mm")
        frame = render.Frame(img)
        native_w, native_h = caps.native
        dev.write(frame.rgb565(caps.byte_order, caps.rotate),
                  (0, 0, native_w, native_h))
        print("na ekranie %s#%d powinna byc wielka %d" % (backend, index, index))
    finally:
        dev.close()
    return 0


def cmd_probe(args):
    """Karta testowa. Odpowiada na pytania, ktore trzeba miec zamkniete, zanim
    zaufa sie ukladowi: czy panel jest wolny, czy typografia jest czytelna
    z biurka i czy ogonki nie sa obciete."""
    from .view import SeriesView

    cfg, opts = _options(args)
    spec = args.panel_spec(cfg)
    dev = device.open_panel(spec, opts)
    try:
        caps = dev.caps
        print("otwarty: %s  plotno=%dx%d  natywnie=%dx%d  %s  %s  %s"
              % (spec.tag, caps.canvas[0], caps.canvas[1], caps.native[0],
                 caps.native[1], caps.byte_order,
                 "prostokaty" if caps.rect_updates else "tylko pelne klatki",
                 "z potwierdzeniem" if caps.acked else "BEZ potwierdzen"))

        width, height = caps.canvas
        img, d = draw.new_canvas((width, height))
        f_head = draw.font(18)
        f_body = draw.font(13)
        d.text((14, 10), "%s — karta testowa" % caps.name.upper(),
               font=f_head, fill=theme.TEXT)

        bars = [("wartosc jest", SeriesView(measured=True, bar_pct=62, full=False,
                                            hatch=False, stub=False,
                                            ghost=False, ghost_pct=0)),
                ("100% — koniec", SeriesView(measured=True, bar_pct=100, full=True,
                                             hatch=False, stub=False,
                                             ghost=False, ghost_pct=0)),
                ("reset wywnioskowany", SeriesView(measured=False, bar_pct=0, full=False,
                                                   hatch=False, stub=True,
                                                   ghost=False, ghost_pct=0)),
                ("nie wiem", SeriesView(measured=False, bar_pct=0, full=False,
                                        hatch=True, stub=False,
                                        ghost=True, ghost_pct=42))]
        y = 44
        for label, v in bars:
            d.text((14, y), label, font=f_body, fill=theme.TEXT_60)
            draw.bar(d, (170, y, 466, y + 12), v, theme.ACCENT)
            y += 26

        d.text((14, y + 6), "Zażółć gęślą jaźń ĄĆĘŁŃÓŚŹŻ", font=draw.font(10),
               fill=theme.TEXT)
        d.text((14, y + 24), "Zażółć gęślą jaźń ĄĆĘŁŃÓŚŹŻ", font=draw.font(12),
               fill=theme.TEXT)
        d.text((14, y + 44), "Zażółć gęślą jaźń", font=draw.font(15), fill=theme.TEXT)
        d.text((14, y + 68), "100 %", font=draw.font(42), fill=theme.TEXT)
        for i in range(0, width, 40):
            d.line([(i, height - 12), (i, height - 1)], fill=theme.NEUTRAL_800)

        native_w, native_h = caps.native
        full = (0, 0, native_w, native_h)
        payload = render.Frame(img).rgb565(caps.byte_order, caps.rotate)
        scale = caps.brightness
        for level in (scale.lo, (scale.lo + scale.hi) // 2, scale.hi):
            dev.set_brightness(level)
            t0 = time.perf_counter()
            status = dev.write(payload, full)
            print("  jasnosc %s: status=%s  %.0f ms"
                  % (level, status, (time.perf_counter() - t0) * 1000))
            time.sleep(0.6)

        if caps.rect_updates:
            # Turning a hand measurement into a repeatable step: rectangles must
            # land where we say and leave the rest of the glass alone. On a screen
            # that acknowledges nothing this is the only check there is.
            print("prostokaty: cztery kwadraty 24x24 w rogach obszaru")
            _probe_rects(dev, caps)

        if caps.acked:
            print("missed_csw: %d  (0 = kazda klatka potwierdzona)" % dev.missed_csw)
            # Last: this command is unreliable and a failed attempt spoils the NEXT
            # transaction, so it cannot stand before the test card.
            print("probe_geometry(): %s  (zawodne — patrz naglowek ax206.py)"
                  % (dev.probe_geometry(),))
        else:
            print("ten sterownik nic nie potwierdza — ocena tylko wzrokiem")
    finally:
        dev.close()
    return 0


def _probe_rects(dev, caps, size=24):
    """Four squares in the corners of the native framebuffer."""
    from .pixels import rgb565_bytes
    width, height = caps.native
    spots = [(0, 0, (255, 0, 0)), (width - size, 0, (0, 255, 0)),
             (0, height - size, (0, 0, 255)),
             ((width - size) // 2, (height - size) // 2, (255, 255, 0))]
    for x, y, colour in spots:
        payload = rgb565_bytes(*colour, order=caps.byte_order) * (size * size)
        t0 = time.perf_counter()
        dev.write(payload, (x, y, x + size, y + size))
        print("  (%d,%d) %s  %.1f ms"
              % (x, y, colour, (time.perf_counter() - t0) * 1000))
        time.sleep(0.3)


def cmd_once(args):
    cfg = C.load()
    logmod.setup(cfg.log_file, cfg.log_level, console=True)
    problems = cfg.validate()
    if problems:
        print("konfiguracja: %s" % "; ".join(problems), file=sys.stderr)
        return 2
    from .app import App
    got, frame, drew = App(cfg).run_once()
    print("pierwsza ramka: %s" % ("odebrana" if got else "NIE doczekalem sie"))
    for tag, ok in drew:
        print("  %s: %s" % (tag, "narysowal" if ok else "NIE narysowal"))
    if args.out:
        frame.image.save(args.out)
        print("zapisano %s" % args.out)
    # Both halves matter: the stream can be alive while every screen is held by
    # another program, and that used to exit 0.
    return 0 if got and all(ok for _, ok in drew) else 1


def build_parser():
    ap = argparse.ArgumentParser(prog="panel", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="pokaz widoczne ekrany")
    ap.add_argument("--identify", metavar="ID",
                    help="namaluj numer na wskazanym ekranie, np. ax206#0")
    ap.add_argument("--probe", action="store_true", help="karta testowa na panelu")
    ap.add_argument("--once", action="store_true",
                    help="jedna klatka z prawdziwych danych i wyjscie")
    ap.add_argument("--out", help="dodatkowo zapisz klatke do PNG (z --once)")
    ap.add_argument("--dll", help="sciezka do libusb-1.0.dll")
    ap.add_argument("--backend", help="wymus sterownik, np. ax206")
    ap.add_argument("--index", type=int, help="wymus urzadzenie o tym indeksie")
    ap.add_argument("--port-path", dest="port_path",
                    help="wymus urzadzenie o tym lancuchu portow, np. 3.4")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)

    def panel_spec(cfg):
        """Which panel a diagnostic command works on.

        Flags win over the file; without either, the FIRST configured panel - the
        configuration is the only place that says which screen is ours, and taking
        anything else could mean drawing over a screen another program owns.
        """
        selector = {}
        if args.port_path:
            selector["port_path"] = args.port_path
        if args.index is not None:
            selector["index"] = args.index
        panels = cfg.panels
        if selector or args.backend:
            backend = args.backend or (panels[0].backend if panels else "ax206")
            return C.PanelSpec(backend, selector)
        if not panels:
            raise DriverError(
                "nie wiem, ktory ekran wziac: brak `panels` w panel.json. "
                "Uruchom `python -m panel --list`")
        return panels[0]
    args.panel_spec = panel_spec

    try:
        if args.list:
            return cmd_list(args)
        if args.identify is not None:
            return cmd_identify(args)
        if args.probe:
            return cmd_probe(args)
        if args.once:
            return cmd_once(args)
        from .app import main as run
        run()
        return 0
    except AlreadyRunning as e:
        print("%s" % e, file=sys.stderr)
        print("Zatrzymaj zadanie, jesli chcesz uruchomic recznie:", file=sys.stderr)
        print("  Stop-ScheduledTask -TaskName 'Claude Panel AX206'", file=sys.stderr)
        return 4
    except C.ConfigError as e:
        print("konfiguracja: %s" % e, file=sys.stderr)
        print("\nWzor pliku %s:\n%s" % (C.CONFIG_PATH, C.example()), file=sys.stderr)
        return 2
    except (DriverError, OSError) as e:
        # OSError alongside DriverError for the same reason link.DEVICE_ERRORS has
        # it: transports report through it (raw ctypes for libusb, pyserial for a
        # busy COM port). A driver is expected to wrap its own failures, this is
        # the net that keeps a diagnostic command from ending in a traceback.
        print("panel: %s" % e, file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
