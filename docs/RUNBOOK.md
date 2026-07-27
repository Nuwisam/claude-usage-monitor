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
| Wszystkie serie nagle `stale` | Brak potwierdzenia od 5 min. Od v3 **nie jest to już artefakt dedupu**, ale też nie musi być awaria: hooki odpalają się tylko przy pracy, więc kwadrans przerwy daje ten sam objaw. Awarię odróżnisz po tym, czy przychodzą batche — jeśli tak, a serii brak, stan przejdzie w `unknown` |
| Seria w stanie `unknown` w `/api/status` | Klient raportuje, ale brak próbek dla tej serii. Sprawdź `events` i log lokalny. **Nie ignoruj** — to jedyny stan oznaczający awarię |
| `docker compose up` → *„all predefined address pools have been fully subnetted"* | Docker wyczerpał pule (limit ~31 sieci). Usuń osieroconą sieć: `docker network ls`, sprawdź `docker network inspect <n> -f '{{len .Containers}}'` |
| Zdarzenia `clock_skew` | Zegar klienta rozjechany >5 min. Backend użył czasu serwera — dane są poprawne, ale warto zsynchronizować zegar |
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

```powershell
Copy-Item Z:\projects\claude-usage-monitor\client\usage-probe.py `
          X:\Projekty\repozytorium skilli\tools\usage-probe.py
```

Źródłem prawdy jest repo na `Z:`, kopia wykonywana **musi** leżeć na dysku lokalnym.
Nie trzeba nic restartować — hook czyta skrypt i `config.json` przy każdym uruchomieniu.

## Wyłączenie zbierania

```powershell
# jedna maszyna: usuń albo zmień nazwę config.json  -> tryb tylko lokalny
Rename-Item "$env:LOCALAPPDATA\claude-usage-monitor\config.json" config.json.off
# albo całkiem: usuń bloki PostToolUse i Stop z ~/.claude/settings.json
```

Po stronie serwera unieważnienie pojedynczej maszyny to usunięcie jej wpisu z `INGEST_TOKENS`
w `.env` + `docker compose up -d`.

## Pierwsze wdrożenie od zera

```bash
cd /var/lib/claude-usage-monitor
cp .env.example .env            # MARIADB_*, INGEST_TOKENS, INGEST_EDGE_KEY, ALLOWED_EMAILS
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
