<#
    Instalacja panelu AX206 jako zadania harmonogramu.

    .\deploy\install-task.ps1              instaluje / aktualizuje
    .\deploy\install-task.ps1 -Uninstall   usuwa zadanie (venv i konfiguracje zostawia)

    Co robi:
      1. tworzy venv POZA repo (%LOCALAPPDATA%\claude-usage-monitor\panel-venv)
      2. instaluje z requirements.txt
      3. klade przekierowanie panel-run.pyw obok konfiguracji, wskazujace na repo
      4. rejestruje zadanie "Claude Panel AX206" na logowanie uzytkownika
      5. uruchamia je i czeka, az log potwierdzi, ze panel rysuje

    Uwagi:
      * "Uruchom tylko gdy uzytkownik jest zalogowany" jest WYMAGANE: dysk sieciowy to dysk
        mapowany per sesja, a dostep do USB wymaga sesji interaktywnej.
      * ExecutionTimeLimit MUSI byc zerowy. Domyslne "zatrzymaj po 3 dniach"
        zabijaloby panel co tydzien, bez sladu w logu.
      * pythonw.exe, nie python.exe - inaczej przy kazdym logowaniu wyskakuje konsola.
      * Ten plik trzymamy w CZYSTYM ASCII. PowerShell 5.1 czyta plik bez BOM jako
        ANSI, wiec myslnik U+2014 rozpada sie na trzy bajty, z ktorych 0x94 to
        cudzyslow zamykajacy U+201D - a parser uznaje go za koniec stringa i
        przewraca sie kilkadziesiat linii dalej, w miejscu bez zwiazku z
        przyczyna. Zaden myslnik nie jest tego wart.
      * REJESTRACJA TO NIE URUCHOMIENIE. Wyzwalacz jest na logowanie, wiec przy
        instalacji na juz zalogowanej sesji zadanie po prostu stoi. Skrypt, ktory
        konczyl sie na "zarejestrowane", zostawial przy ciemnym ekranie bez
        zadnej wskazowki - i kusil, zeby odpalic go drugi raz.
      * Uchwyt do panelu jest WYLACZNY. Gdy trzyma go inny program, klient probuje co
        30 s i pisze o tym w logu RAZ (nie co sekunde), a na szkle zostaje cudzy
        obraz. Z zewnatrz wyglada to identycznie jak nieudana instalacja, wiec
        skrypt czyta log po starcie i nazywa ten stan wprost.
#>
[CmdletBinding()]
param(
    [switch]$Uninstall,
    [string]$TaskName = "Claude Panel AX206"
)

$ErrorActionPreference = "Stop"

$PanelDir = Split-Path -Parent $PSScriptRoot
$AppDir   = Join-Path $env:LOCALAPPDATA "claude-usage-monitor"
$VenvDir  = Join-Path $AppDir "panel-venv"
$Pythonw  = Join-Path $VenvDir "Scripts\pythonw.exe"
$Launcher = Join-Path $AppDir "panel-run.pyw"

function Get-PanelProcess {
    # Po wierszu polecen, nie po nazwie: pythonw.exe uruchamia
    # takze inne rzeczy, a zabicie cudzego procesu byloby gorsze niz problem,
    # ktory rozwiazujemy. Instancji sa zwykle dwie - stub venv i jego dziecko.
    @(Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*panel-run.pyw*" })
}

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "usunieto zadanie '$TaskName'"
    } else {
        Write-Host "zadania '$TaskName' nie bylo"
    }
    Write-Host "venv i panel.json zostaja w $AppDir"
    return
}

Write-Host "panel: $PanelDir"
New-Item -ItemType Directory -Force $AppDir | Out-Null

# --- 1. venv --------------------------------------------------------------
if (-not (Test-Path $Pythonw)) {
    Write-Host "tworze venv w $VenvDir"
    $base = (Get-Command python).Source
    & $base -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "nie udalo sie utworzyc venv" }
}
Write-Host "instaluje zaleznosci"
& (Join-Path $VenvDir "Scripts\python.exe") -m pip install --quiet --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip: aktualizacja pip nie przeszla" }
& (Join-Path $VenvDir "Scripts\python.exe") -m pip install --quiet -r (Join-Path $PanelDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install -r requirements.txt nie przeszedl" }

# --- 2. przekierowanie ----------------------------------------------------
# Ta sama konwencja co przy sondzie: w repo zostaje zrodlo, tutaj lezy kilka
# linijek, ktore je uruchamiaja. Edycja w repo dziala natychmiast.
$launcherBody = @"
# Wygenerowane przez panel/deploy/install-task.ps1 - nie edytuj recznie.
# Panel zyje w repo; tutaj jest tylko przekierowanie.
import os, runpy, sys, time

SRC = r"$PanelDir"

# Dysk sieciowy bywa niezamapowany, gdy zadanie odpala sie tuz po zalogowaniu.
waited = 0
while not os.path.isdir(SRC) and waited < 120:
    time.sleep(2)
    waited += 2

sys.path.insert(0, SRC)
runpy.run_path(os.path.join(SRC, "run.pyw"), run_name="__main__")
"@
Set-Content -Path $Launcher -Value $launcherBody -Encoding utf8
Write-Host "przekierowanie: $Launcher"

# --- 3. zadanie -----------------------------------------------------------
# Zatrzymanie PRZED rejestracja, nie po: -Force na dzialajacym zadaniu zostawia
# stary proces przy zyciu. Bez tego dalej biegloby to, co bylo, a nie to, co
# wlasnie zainstalowalismy - i to milczkiem, bo obraz na panelu wyglada tak samo.
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing -and $existing.State -eq "Running") {
    Write-Host "zatrzymuje poprzednia instancje"
    Stop-ScheduledTask -TaskName $TaskName
    # Czekamy na SMIERC PROCESU, nie na stan zadania. Harmonogram melduje
    # "Ready", gdy tylko wysle sygnal, a pythonw zyje jeszcze chwile i trzyma
    # blokade panel.lock. Nowa instancja weszlaby wtedy na zajeta blokade,
    # cicho by wyszla i dalej rysowalby STARY kod - czyli instalacja
    # wygladalaby na udana, nie zmieniajac niczego.
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline -and (Get-PanelProcess)) {
        Start-Sleep -Milliseconds 500
    }
    if (Get-PanelProcess) {
        Write-Host "  UWAGA: proces panelu nadal zyje po 20 s - nowa instancja moze nie wejsc"
    }
}

$action = New-ScheduledTaskAction -Execute $Pythonw -Argument "`"$Launcher`"" -WorkingDirectory $AppDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = "PT30S"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -RestartCount 99 `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)   # 0 = bez limitu; patrz naglowek

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force `
    -Description "Limity Claude na panelu USB AX206 (repo: $PanelDir)" | Out-Null

# --- 4. start i potwierdzenie --------------------------------------------
# Czekamy na LINIE W LOGU, nie na "State = Running". Zadanie potrafi byc
# uruchomione i jednoczesnie nic nie rysowac: zajety panel, zly token, brak
# sieci. "Running" powiedzialoby wtedy nieprawde.
$LogPath = Join-Path $AppDir "panel.log"
$before = 0
if (Test-Path $LogPath) { $before = @(Get-Content $LogPath).Count }

Start-ScheduledTask -TaskName $TaskName
Write-Host ""
Write-Host "zadanie '$TaskName' zarejestrowane i uruchomione"
Write-Host -NoNewline "czekam na potwierdzenie z logu "

$deadline = (Get-Date).AddSeconds(30)
$opened = $null; $busy = $null; $dup = $null; $bad = $null
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    Write-Host -NoNewline "."
    $new = @()
    if (Test-Path $LogPath) { $new = @(Get-Content $LogPath | Select-Object -Skip $before) }
    # Czekamy na PIERWSZA KLATKE, nie na "panel: otwarty". Uchwyt do urzadzenia
    # jeszcze niczego nie rysuje, a to jego brak byl objawem, ktory kazal
    # instalowac wszystko po raz drugi.
    $opened = $new | Where-Object { $_ -match "pierwsza klatka po otwarciu" }| Select-Object -Last 1
    $busy   = $new | Where-Object { $_ -match "zajety przez inny proces" }| Select-Object -Last 1
    $dup    = $new | Where-Object { $_ -match "panel juz dziala" }        | Select-Object -Last 1
    $bad    = $new | Where-Object { $_ -match "konfiguracja:" }           | Select-Object -Last 1
    if ($opened -or $busy -or $dup -or $bad) { break }
}
Write-Host ""
Write-Host ""

if ($opened) {
    Write-Host "OK - panel rysuje:"
    Write-Host "  $opened"
} elseif ($busy) {
    Write-Host "PANEL ZAJETY przez inny proces:"
    Write-Host "  $busy"
    Write-Host ""
    Write-Host "  Uchwyt do modulu jest wylaczny - albo panel, albo inny program."
    Write-Host "  Zatrzymaj tamten program (lub wskaz mu drugi modul). Klient probuje"
    Write-Host "  dalej co 30 s i podejmie rysowanie SAM, gdy panel sie zwolni;"
    Write-Host "  nie trzeba go restartowac ani instalowac ponownie."
} elseif ($dup) {
    Write-Host "PANEL JUZ DZIALAL - nowa instancja wyszla po blokadzie:"
    Write-Host "  $dup"
    Write-Host "  To nie jest blad. Rysuje ta, ktora byla wczesniej."
} elseif ($bad) {
    Write-Host "BLAD KONFIGURACJI - panel nie wystartuje:"
    Write-Host "  $bad"
    Write-Host "  Popraw $AppDir\panel.json i uruchom: Start-ScheduledTask -TaskName '$TaskName'"
} else {
    Write-Host "BRAK POTWIERDZENIA w 30 s. Zajrzyj do logu - ponizej jego ogon:"
    if (Test-Path $LogPath) { Get-Content $LogPath -Tail 8 | ForEach-Object { Write-Host "  $_" } }
}

Write-Host ""
Write-Host "  log:     $LogPath"
Write-Host "  config:  $AppDir\panel.json"
Write-Host "  stop:    Stop-ScheduledTask -TaskName '$TaskName'"
Write-Host "  start:   Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "  test:    python -m panel --probe   (najpierw zatrzymaj zadanie)"
