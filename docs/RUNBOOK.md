# Runbook

Host: `192.0.2.10` (`usage.example.org`). Katalog: `/var/lib/claude-usage-monitor`
== `Z:/projects/claude-usage-monitor`. **Kopia robocza jest wdrożeniem.**

UI: <https://usage.example.org/claude-usage/> — serwowane przez **ten sam kontener
backendu** (statyki z etapu `node` w `backend/Dockerfile`), więc nie ma osobnego kontenera
frontendu i nie ma nic do restartowania osobno.

## Codzienna obsługa

```bash
cd /var/lib/claude-usage-monitor
docker compose ps
docker compose logs -f --tail 50 claude_usage_monitor_backend
git pull && docker compose up -d --build      # zwykły deploy
```

## Czy system żyje

```bash
# 1) kontenery
docker compose ps                       # oba healthy

# 2) czy dane przyrastają (uruchom dwa razy w odstępie kilku minut PRACY z Claude Code)
DB=$(grep -oP '^MARIADB_DATABASE=\K.*' .env); RP=$(grep -oP '^MARIADB_ROOT_PASSWORD=\K.*' .env)
docker exec claude_usage_monitor_mariadb mariadb -uroot -p"$RP" "$DB" -N -e \
 "select concat('batchy=',count(*),' probek=',(select count(*) from limit_samples),
   ' ostatni: ',timestampdiff(second,max(received_at),utc_timestamp()),' s temu')
  from ingest_batches;"

# 3) czy pomiary są ŚWIEŻE, a nie tylko obecne
docker exec claude_usage_monitor_mariadb mariadb -uroot -p"$RP" "$DB" -N -e \
 "select measurement_source, count(*), round(avg(cache_age_s)), round(avg(fresh_age_s))
  from ingest_batches where received_at > utc_timestamp() - interval 1 hour
  group by measurement_source;"
```

Kodów HTTP tu nie ma i nie będzie — od wersji 3 sonda nie wysyła żadnego żądania do
Anthropic. Zapytanie wyżej pilnuje czegoś innego i ważniejszego: **`cli_merged` znaczy, że
doszły świeże procenty ze stdout `/usage`, a `cli_usage_cache` — że poszedł sam cache Claude
Code, który bywa do 5 minut stary.** Przewaga tego drugiego to cicha awaria: dane nadal
płyną, wykres wygląda normalnie, tylko rozdzielczość spadła z minuty do pięciu. Najczęstsza
przyczyna to brak `claude` w `PATH` procesu hooka — sprawdź `spawn_error` w logu lokalnym.

**Uwaga przy interpretacji:** dane przyrastają tylko wtedy, gdy **pracujesz** z Claude Code,
i tylko raz na 60 s (throttle). `PostToolUse` odpala się **po** zakończeniu narzędzia, więc
podczas jednego długiego wywołania nic nie przyjdzie — to poprawne zachowanie, nie awaria.

## Diagnostyka po stronie klienta

```bash
python client/analyze-samples.py        # tempo zmian, błędy, przełączenia konta
```

Log lokalny: `%LOCALAPPDATA%\claude-usage-monitor\usage-samples.jsonl`.
Jeśli log rośnie, a baza nie — problem jest w wysyłce; sprawdź `spool.jsonl` (rośnie przy
nieudanych POST-ach) i sekcję o certyfikatach w `client/README.md`.

## Typowe problemy

| Objaw | Przyczyna i co zrobić |
|---|---|
| `/api/status` daje **503** `sso-unreachable` | Backend nie dosięga oauth2-proxy. Sprawdź, czy jest w sieci `identity-proxy_default` i czy `identity_proxy` żyje |
| `/api/status` daje **403** `email-not-allowed` | `ALLOWED_EMAILS` w `.env` nie zawiera Twojego adresu SSO |
| Ingest daje **403**, a token jest dobry | Brak nagłówka `X-Ingest-Key` albo rozjazd z `INGEST_EDGE_KEY` w vhoście Apache |
| Ingest daje **401** | Zły token maszyny. Porównaj `config.json` klienta z `INGEST_TOKENS` |
| `samplesWritten: 0`, ale `ok: true` | **Poprawne.** Dedup — wartości się nie zmieniły i nie minął heartbeat (5 min). Pomiar mimo to podbija `last_confirmed_at`, więc UI pokazuje go jako świeży |
| Seria `live`, ale `capturedAt` sprzed godzin | **Poprawne i zamierzone.** `capturedAt` to ostatnia zapisana PRÓBKA, świeżość liczy się z `confirmedAt`. Wartość po prostu się nie zmienia — UI podpisuje to „bez zmian od” |
| Wszystkie serie nagle `stale` | Brak potwierdzenia od 5 min. Od v3 **nie jest to już artefakt dedupu**, ale też nie musi być awaria: hooki odpalają się tylko przy pracy, więc kwadrans przerwy daje ten sam objaw. **W UI tego nie zobaczysz** — `stale` wygląda jak `live`, a rośnie tylko etykieta wieku odczytu |
| Seria w stanie `unknown` w `/api/status` | Klient raportuje, ale brak próbek dla tej serii. **Nie ignoruj** — to jedyny stan oznaczający awarię. **UI go nie nazywa i nie ma już banera**: pokazuje ostatni zmierzony procent z rosnącym wiekiem odczytu, więc awarię widać po tym, że wiek rośnie mimo pracy. Do potwierdzenia: `curl -s -b "$COOKIE" .../api/status \| jq '.accounts[].series[] \| select(.freshness=="unknown") \| {label, rawUtilization, confirmedAt}'`, potem `events` i log lokalny |
| UI pokazuje liczbę, a sondy nie ma od dni | **Poprawne.** Wartość jest ostatnim POMIAREM, nie zgadywaniem, a jej wiek stoi obok („potwierdzone w śr. o 11:58 · 3 d 4 h temu”). Zero byłoby tu kłamstwem — zasada 4 z `AGENTS.md` |
| `docker compose up` → *„all predefined address pools have been fully subnetted"* | Docker wyczerpał pule (limit ~31 sieci). Usuń osieroconą sieć: `docker network ls`, sprawdź `docker network inspect <n> -f '{{len .Containers}}'` |
| Zdarzenia `clock_skew` | Zegar klienta rozjechany >5 min wobec serwera. **Czysta diagnostyka — na zapis nie wpływa.** Pomiar datuje serwer (`received_at − wiek`), a wiek to różnica w obrębie zegara klienta, więc rozjazd go nie psuje. Warto mimo to zsynchronizować zegar |
| Zdarzenia `clock_backwards` | Pomiar datowany **po** `sent_at`, czyli zegar klienta cofnął się między zapisem a wysyłką. Cały wpis odrzucony (surowy payload jest w `raw_payloads`), bo inaczej wylądowałby na czasie odebrania i nadpisał stan starym odczytem. Powtarzalne = zepsuta synchronizacja czasu na tej maszynie |
| Zdarzenia `no_captured_at` | Payload bez czasu pomiaru. Obserwacje pominięte — **nie** podstawiamy „teraz", bo to zamieniałoby niewiedzę w pewnie wyglądającą świeżość |
| Zdarzenia `schema_drift` | Anthropic zmienił kształt odpowiedzi. Payload jest zapisany w całości; obejrzyj `GET /api/batches/{id}/raw` i zaktualizuj `app/parsing.py` + fixture |
| UI daje **404** na `/claude-usage/` | Obraz zbudowany bez etapu `node` albo `dist` pusty. `docker exec claude_usage_monitor_backend ls /app/static` — powinien być `index.html` i `assets/`. Jeśli pusto: `docker compose build --no-cache` |
| UI ładuje się, ale **pusta strona** i błąd w konsoli | Rozjazd `base` Vite z regułą Apache. Assety muszą wisieć pod `/claude-usage/assets/…`; `APP_BASE_PATH` w `.env`, `VITE_BASE_PATH` w build-argu i `ProxyPass` muszą mówić to samo |
| Nagłówek UI pokazuje **`kontrakt vN ≠ vM`** | Backend i frontend rozjechały się wersją kontraktu. Najpierw sprawdź, czy `CONTRACT_VERSION` w `app/services/status.py` i w `frontend/src/api/types.ts` są **równe w kodzie** — podbicie samego backendu daje dokładnie ten objaw przy poprawnych danych. Dopiero potem `docker compose up -d --build` (różne commity w obrazie) |
| Historia daje **500**, w logu `can't subtract offset-naive and offset-aware datetimes` | Parametr `from`/`to` przyszedł ze strefą (przeglądarka wysyła `toISOString()`), a backend liczy na naiwnym UTC. Parametry dat muszą mieć typ `NaiveUtcDt` z `app/schemas.py`, nie `datetime` |
| **Liczba próbek rośnie przy każdym pomiarze** mimo braku zmian | Zepsuty dedup. Sprawdź, czy porównanie `resets_at` idzie przez `same_reset_window` (tolerancja), a nie na równość — granica okna kołysze się o ~2 s i porównanie dosłowne wyłącza dedup, guard monotoniczności i granice resetu naraz |

## Backup

**Weekly `data-backup` NIE obejmuje tej bazy.** Jego `mysqldump --all-databases` łączy się
do natywnej MariaDB hosta (`127.0.0.1:3306`), a nasza działa w kontenerze. Żadna linia
`dir-backuper.py` nie dotyczy tego katalogu.

Do stracenia jest **historia zużycia** — nieodtwarzalna, bo Anthropic nie udostępnia przeszłości.
Stan bieżący odbuduje się przy pierwszym pomiarze.

```bash
./scripts/backup.sh                              # zrzut do backups/, retencja 14 dni
./scripts/restore.sh backups/claude_usage_*.sql.gz   # DOMYŚLNIE do bazy scratch
TARGET=prod ./scripts/restore.sh <plik>          # nadpisanie produkcji, pyta o potwierdzenie
```

`restore.sh` celowo odtwarza do bazy `*_scratch`. Backup, którego nigdy nie próbowano odtworzyć,
nie jest backupem — a odtwarzanie „na próbę" wprost na produkcję to najlepszy sposób, żeby ją
stracić.

Żeby włączyć automat, dopisz jedną linię do `/etc/cron.weekly/data-backup`:
```
/var/lib/claude-usage-monitor/scripts/backup.sh
```

## Zmiana klienta na maszynie

**Nic nie kopiujesz.** Pod ścieżką z hooków leży przekierowanie, które wykonuje
`client/usage-probe.py` prosto z repo, więc edycja działa od następnego przebiegu. Nie trzeba
nic restartować — hook czyta skrypt i `config.json` przy każdym uruchomieniu. Pamiętaj tylko,
że **pierwszy przebieg nie mierzy**: sonda nie czeka na `claude -p "/usage"`, wynik konsumuje
następny cykl.

## Wydanie sondy dla maszyn zdalnych

Maszyna zdalna nie widzi tego repo — bierze sondę z `repozytorium skilli`
(`kopia wydania usage-probe.py`) razem ze skillem. To jest **wydanie** i ma prawo
być starsze od HEAD; publikacja jest decyzją, nie skutkiem ubocznym pushu.

```
polecenie publikujace wydanie      # pokazuje diff, kopiuje, commituje w repo skilli
```

Przypomina o tym `pre-push`: **odmawia**, gdy sonda się zmieniła bez podbicia `SCRIPT_VERSION`,
i **ostrzega**, gdy wydanie zostało w tyle. Która maszyna chodzi na której wersji — widać
w `/api/machines` po `scriptVersion`.

Gdy hook nie odzywa się mimo zmiany sondy, sprawdź w tej kolejności:
`git config core.hooksPath` (ma być `scripts/git-hooks`) oraz `ls -l scripts/git-hooks/pre-push`
po stronie hosta — **bez bitu `+x` git na Linuksie pomija hook bez słowa**, a bit z indeksu
gita nie przechodzi przez Sambę do katalogu roboczego.

## Wyłączenie zbierania

```powershell
# jedna maszyna: usuń albo zmień nazwę config.json  -> tryb tylko lokalny
Rename-Item "$env:LOCALAPPDATA\claude-usage-monitor\config.json" config.json.off
# albo całkiem: usuń bloki PostToolUse i Stop z ~/.claude/settings.json
```

Po stronie serwera unieważnienie pojedynczej maszyny to usunięcie jej wpisu z `INGEST_TOKENS`
w `.env` + `docker compose up -d`.

## Klient strumienia SSE (panel AX206, skrypty)

Przeglądarka nie potrzebuje niczego — `EventSource` idzie na ciasteczku SSO. Klient bez
sesji SSO potrzebuje własnego tokenu i **UUID-ów kont**, bo subskrypcja jest po UUID, nie
po adresie e-mail (jeden adres wskazuje kilka kont, a e-mail bywa nadpisywany).

```bash
# 1) token — OSOBNY od INGEST_TOKENS, ten drugi jest wyłącznie do zapisu
openssl rand -hex 32
# dopisz do .env:  STREAM_TOKENS=<hex>:panel      (potem: docker compose up -d)

# 2) UUID kont, na które panel ma być zapisany (z przeglądarki zalogowanej do SSO)
curl -s $B/api/accounts | jq -r '.[] | "\(.uuid)  \(.email)"'

# 3) próba na żywo — hello + snapshot natychmiast, potem ping co 15 s
curl -N -H "Authorization: Bearer $STREAM_TOKEN" \
     "$B/api/stream?account=<uuid>&account=<uuid>"
```

Jeśli `hello` zwraca UUID w polu `unknown[]`, to literówka albo konto jeszcze nie
raportowało — połączenie zostaje otwarte i ramki zaczną przychodzić, gdy konto się pojawi.

**Po `/login` na nowe konto panel wymaga dopisania jego UUID.** Przeglądarka dowiaduje się
sama (poll `/status` co 3 min przepina strumień), panel nie — bo nikt o ten UUID nie prosił.

**Ramki przychodzą zlepkami co kilkadziesiąt sekund zamiast natychmiast?** To buforowanie
Apache: sprawdź, czy reguła `/claude-usage/api/stream` stoi **przed** generyczną
`/claude-usage/api` i czy ma `SetEnv no-gzip 1`.

**Piszesz własnego klienta strumienia?** Czytaj z gniazda przez `read1()`, nie `read(n)` —
to drugie czeka na skompletowanie całych `n` bajtów, więc po pierwszej porcji kolejne karty
i pingi zostają w buforze, a klient stoi z pierwszą ramką **wyglądając na żywego**. Ta sama
pułapka złapała panel i kosztuje długie szukanie „gdzie ginie druga ramka".

## Panel AX206 na biurku

Klient: `panel/` w tym repo, szczegóły sprzętowe i diagnostyka w `panel/README.md`.

```powershell
cd Z:\projects\claude-usage-monitor\panel
python -m panel --list                 # który moduł na którym porcie USB
python -m panel --probe                # karta testowa: kolory, paski, ogonki
python -m panel --once                 # jedna klatka z prawdziwych danych
.\deploy\install-task.ps1              # venv poza repo + zadanie na logowanie
```

`install-task.ps1` jest idempotentny: zatrzymuje poprzednią instancję (czekając na **śmierć
procesu**, nie na stan zadania — inaczej nowa wchodzi na zajęty `panel.lock` i cicho wychodzi,
a rysuje dalej stary kod), rejestruje zadanie, **uruchamia je** i czeka, aż w logu pojawi się
`panel: otwarty`. Sama rejestracja nic nie uruchamia: wyzwalacz jest na logowanie, więc
instalacja na już zalogowanej sesji zostawiałaby ciemny ekran bez śladu przyczyny. Gdy zamiast
potwierdzenia zobaczysz `PANEL ZAJETY`, moduł trzyma inny program — zatrzymaj go, a klient podejmie
rysowanie sam w ciągu ~30 s, bez ponownej instalacji.

Konfiguracja: `%LOCALAPPDATA%\claude-usage-monitor\panel.json` — **osobny plik** od
`config.json` sondy, bo token strumienia ma inny zakres niż token ingestu, a
`/usage-monitor-enrollment` przepisuje plik sondy. Konta to dwa nazwane pola
(`account_1`, `account_2`), nie lista — układ ekranu ma dokładnie dwa pasy.

**Uchwyt do modułu jest wyłączny: albo panel, albo inny program.** Dopóki jest jeden
wyświetlacz, ten drugi program musi stać. Po dołożeniu drugiego oba programy chodzą równolegle, ale
wtedy **każdy trzeba przypiąć do konkretnego egzemplarza** — oba mają ten sam numer
seryjny `WCH32` (stała firmware'u), a Windows wyprowadza z niego zarówno ID instancji,
jak i `ContainerID`, więc te wartości też będą identyczne. Rozróżnia je wyłącznie łańcuch
portów z libusb-1.0 (`"device": {"port_path": "3.4"}`), wypisywany przez `python -m panel
--list`. Stare `"location": "Port_#0004.Hub_#0005"` daje teraz błąd konfiguracji:
człon `Hub_#` był licznikiem enumeracji i potrafił przeskoczyć bez ruszania wtyczki.
Który moduł jest który, potwierdzaj `--identify`.

### Dług: napisy panelu rozjechały się z WWW

**Etykiety czasu są już ujednolicone.** `panel/panel/fmt.py` ma port `at_stamp()` i szczebel
dobowy w `ago()`, `DAYS` to dokładnie tablica z `time.ts` (indeksowana od niedzieli przez
`_day_index()`, bo `weekday()` liczy od poniedziałku), a ręczna flaga `with_day` z `view.py`
zniknęła — o dopisaniu dnia decyduje jedno miejsce, tak jak w WWW. Panel i WWW piszą teraz
`· w pt. o 20:00` i `3 d 4 h temu` tak samo.

Co zostało: docstring `panel/panel/view.py` twierdzi, że „WWW rozroznia `live`/`stale` kolorem
wypelnienia i daje `unknown` wlasny rysunek" — to już nieprawda, WWW przejęło model panelu.

Reszta różnic panel/WWW jest **świadoma**, nie długiem: 480×320 pokazuje mniej i krócej.
Panel nie ma słowa `bez licznika`, rysunku `inferred_reset`, podpisu `potwierdzone …` ani
wieku per-seria. Nie ma też wspólnego strażnika parytetu napisów — przy zmianach w `time.ts`
port trzeba przenieść ręcznie, a `test_fmt_port.py` łapie tylko to, co ma wpisane.

## Pierwsze wdrożenie od zera

```bash
cd /var/lib/claude-usage-monitor
cp .env.example .env            # MARIADB_*, INGEST_TOKENS, INGEST_EDGE_KEY, ALLOWED_EMAILS
                                # STREAM_TOKENS tylko jeśli ma być klient bez SSO
docker compose up -d --build

EDGE=$(grep -oP '^INGEST_EDGE_KEY=\K.*' .env)
sed "s|__INGEST_EDGE_KEY__|$EDGE|" deploy/apache/claude-usage-monitor-include.conf.example \
    > /etc/apache2/sites-available/claude-usage-monitor-include.conf
chmod 600 /etc/apache2/sites-available/claude-usage-monitor-include.conf
# dopisz do sites-available/example_org-ssl.conf:
#   Include sites-available/claude-usage-monitor-include.conf
apachectl configtest && systemctl reload apache2
```

Weryfikacja API: 401 z `redirect_url` na `/api/status`, 403 na `/api/ingest` bez `X-Ingest-Key`,
401 z kluczem brzegowym ale bez tokenu, 200 z kompletem.

Weryfikacja UI (żadna nie wymaga sesji SSO):
```bash
B=https://usage.example.org/claude-usage
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' $B/          # 200 text/html
curl -s -o /dev/null -w '%{http_code}\n' $B/historia                  # 200 — fallback SPA
curl -s $B/api/nieistnieje                                            # 404 JSON, NIE index.html
curl -sI $B | grep -i location                                        # 301 na /claude-usage/
```

Ostatnie z nich jest istotne: fallback SPA łapie wszystko, czego nie dopasowały routery, więc
literówka w adresie endpointu może zacząć oddawać stronę HTML zamiast błędu. Pilnuje tego
`backend/tests/test_static_spa.py`.
