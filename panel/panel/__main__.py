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

from . import ax206, config as C, device, draw, log as logmod, render, theme
from .app import AlreadyRunning


def cmd_list(args):
    panels = device.list_panels(args.dll)
    print("moduly %04x:%04x widziane przez libusb: %d"
          % (ax206.VID, ax206.PID, len(panels)))
    for p in panels:
        print("  %s" % p.describe())
        print("      instancja=%s  address=%s  bus=%s devnum=%s"
              % (p.instance, p.address, p.bus, p.devnum))
    if not panels:
        print("  (nic — czy modul jest wpiety i ma sterownik libusb-win32?)")
        return 1
    if len(panels) > 1 and not all(p.paired for p in panels):
        print("\nUWAGA: przy wiecej niz jednym module powiazanie z portem jest")
        print("ZGADNIETE (libusb nie podaje sciezki portu). Potwierdz je:")
        for p in panels:
            print("  python -m panel --identify %d" % p.index)
    print("\nDo panel.json:")
    print('  "device": {"location": "%s"}' % (panels[0].location or "?"))
    return 0


def cmd_identify(args):
    panels = device.list_panels(args.dll)
    picked = device.select(panels, {"index": args.identify})
    dev = ax206.AX206(finder=lambda: picked.found, dll_path=args.dll).open()
    try:
        dev.set_brightness(7)
        img, d = draw.new_canvas((dev.width, dev.height))
        f_big = draw.font(190)
        f_small = draw.font(16)
        d.text((dev.width // 2, dev.height // 2 - 10), str(picked.index),
               font=f_big, fill=theme.ACCENT, anchor="mm")
        d.text((dev.width // 2, dev.height - 40),
               picked.location or picked.filename, font=f_small,
               fill=theme.TEXT_60, anchor="mm")
        dev.blit(ax206.image_to_rgb565(img))
        print("na module #%d powinna byc wielka %d (%s)"
              % (picked.index, picked.index, picked.location))
    finally:
        dev.close()
    return 0


def cmd_probe(args):
    """Karta testowa. Odpowiada na pytania, ktore trzeba miec zamkniete, zanim
    zaufa sie ukladowi: czy panel jest wolny, czy typografia jest czytelna
    z biurka i czy ogonki nie sa obciete."""
    from .view import SeriesView

    panels = device.list_panels(args.dll)
    print("moduly: %d" % len(panels))
    for p in panels:
        print("  %s" % p.describe())
    finder = device.finder_for(args.device_selector(), args.dll)
    dev = ax206.AX206(finder=finder, dll_path=args.dll).open()
    try:
        print("otwarty: serial=%s  geometria=%dx%d" % (dev.serial, dev.width, dev.height))

        img, d = draw.new_canvas((dev.width, dev.height))
        f_head = draw.font(18)
        f_body = draw.font(13)
        d.text((14, 10), "PANEL AX206 — karta testowa", font=f_head, fill=theme.TEXT)

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
        for i in range(0, dev.width, 40):
            d.line([(i, dev.height - 12), (i, dev.height - 1)], fill=theme.NEUTRAL_800)

        payload = ax206.image_to_rgb565(img)
        for level in (0, 3, 7):
            dev.set_brightness(level)
            t0 = time.perf_counter()
            status = dev.blit(payload)
            print("  jasnosc %d: status=%s  %.0f ms"
                  % (level, status, (time.perf_counter() - t0) * 1000))
            time.sleep(0.6)
        print("missed_csw: %d  (0 = kazda klatka potwierdzona)" % dev.missed_csw)
        # Na samym koncu: ta komenda bywa zawodna i nieudana proba psuje
        # NASTEPNA transakcje, wiec nie moze stac przed karta testowa.
        print("probe_geometry(): %s  (zawodne — patrz naglowek ax206.py)"
              % (dev.probe_geometry(),))
    finally:
        dev.close()
    return 0


def cmd_once(args):
    cfg = C.load()
    logmod.setup(cfg.log_file, cfg.log_level, console=True)
    problems = cfg.validate()
    if problems:
        print("konfiguracja: %s" % "; ".join(problems), file=sys.stderr)
        return 2
    from .app import App
    got, frame = App(cfg).run_once()
    print("pierwsza ramka: %s" % ("odebrana" if got else "NIE doczekalem sie"))
    if args.out:
        frame.image.save(args.out)
        print("zapisano %s" % args.out)
    return 0 if got else 1


def build_parser():
    ap = argparse.ArgumentParser(prog="panel", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="pokaz widoczne moduly")
    ap.add_argument("--identify", type=int, metavar="N",
                    help="namaluj numer N na module o indeksie N")
    ap.add_argument("--probe", action="store_true", help="karta testowa na panelu")
    ap.add_argument("--once", action="store_true",
                    help="jedna klatka z prawdziwych danych i wyjscie")
    ap.add_argument("--out", help="dodatkowo zapisz klatke do PNG (z --once)")
    ap.add_argument("--dll", help="sciezka do libusb0.dll")
    ap.add_argument("--index", type=int, help="wymus modul o tym indeksie")
    ap.add_argument("--location", help="wymus modul na tym porcie")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)

    def device_selector():
        if args.location:
            return {"location": args.location}
        if args.index is not None:
            return {"index": args.index}
        try:
            return C.load().device
        except C.ConfigError:
            return None
    args.device_selector = device_selector

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
    except ax206.AX206Error as e:
        print("panel: %s" % e, file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
