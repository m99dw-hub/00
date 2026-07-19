# System agentów kodujących — produkcja aplikacji Android przez WhatsApp

Orchestrator (Python + LangGraph) dzieli zadania zlecane na WhatsApp między
agentów (UX, Architekt, Developer, Reviewer, QA, DevOps), pilnuje zgodności
z `REQUIREMENTS.md`, weryfikuje kod dwustopniowo (max 3 próby poprawek) i
wersjonuje aplikację (semver + tagi Git).

Pełny opis architektury i decyzji projektowych — patrz historia rozmowy, w
której ten projekt powstał. Ten plik to instrukcja **od zera do działającego
systemu**.

## 0. Czego będziesz potrzebować

- Serwer Mikrus z dostępem root (SSH)
- Numer telefonu z aktywnym WhatsApp (do zeskanowania QR — najlepiej dedykowany,
  niekoniecznie Twój główny numer)
- Konto GitHub + osobne repozytorium na aplikację Android
- Konto OpenRouter

## 1. Konto OpenRouter

1. Załóż konto na https://openrouter.ai
2. Doładuj konto kredytami (Settings → Credits) — bez tego płatne modele nie zadziałają
3. Utwórz klucz API: Settings → Keys → **Create Key**
4. **Ustaw miesięczny limit na tym kluczu**: edytuj klucz → *Credit limit* → wpisz
   kwotę → *Limit reset* → `monthly`. To Twoja twarda granica wydatków.
5. Sprawdź na https://openrouter.ai/models dokładne identyfikatory modeli, które
   chcesz przypisać agentom, i zaktualizuj `orchestrator/config.py` →
   `AGENT_MODELS` (wartości w repo to placeholdery — zweryfikuj aktualne sluggi).

## 2. Repozytorium aplikacji Android

1. Utwórz nowe, puste repozytorium na GitHubie (np. `twoj-user/moja-apka`)
2. Skopiuj do niego zawartość `android-app-template/` z tego repo (zawiera
   `REQUIREMENTS.md`, `CHANGELOG.md`, `.github/workflows/ci.yml`) jako punkt
   startowy — dołóż do tego standardowy szkielet projektu Android/Gradle
   (Android Studio: File → New Project, potem `git init` na wierzchu)
3. Utwórz GitHub Personal Access Token (Settings → Developer settings →
   Fine-grained tokens) z uprawnieniami `contents: write`, `actions: write`
   dla tego repozytorium

## 3. Serwer Mikrus

```bash
ssh root@twoj-mikrus
# skopiuj cala zawartosc tego repo na serwer, np. przez git clone (najpierw
# wrzuc ten projekt do WLASNEGO repo GitHub) albo scp
bash deploy/setup_mikrus.sh
```

Skrypt instaluje Pythona, Node.js, JDK 17, Android SDK cmdline-tools, oraz
zależności obu usług. Na końcu wypisze kroki ręczne (uzupełnienie `.env`,
rejestracja usług systemd, skanowanie QR).

## 4. Konfiguracja `.env`

```bash
cp .env.example .env
nano .env
```

Uzupełnij: `AI_API` (krok 1), `GITHUB_TOKEN` + `GITHUB_REPO` (krok 2),
`ALLOWED_WHATSAPP_JID` — numer w formacie `48xxxxxxxxx@s.whatsapp.net`
(dowiesz się go z logów przy pierwszej wiadomości testowej, albo wylicz z
własnego numeru w formacie międzynarodowym bez `+`).

## 5. Pierwsze uruchomienie

```bash
cp deploy/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now orchestrator whatsapp-bridge

journalctl -u whatsapp-bridge -f
```

W logach pojawi się kod QR — zeskanuj go w WhatsApp: **Ustawienia → Urządzenia
połączone → Połącz urządzenie**. Po połączeniu wyślij testową wiadomość na ten
numer, np. „cześć” — powinieneś dostać odpowiedź od orchestratora.

## 6. Pierwsze prawdziwe zadanie

Wyślij np.: *„Dodaj ekran logowania z polami email i hasło, walidacją i
przyciskiem logowania”*. Jeśli polecenie jest zbyt ogólne, orchestrator
dopyta zamiast zgadywać.

## Struktura repozytorium

```
orchestrator/          # Python + LangGraph + FastAPI — mózg systemu
  agents/base.py        # klient OpenRouter + licznik kosztów
  prompts/               # system prompty per rola
  tools/                  # git, GitHub Actions, monitoring kosztów
  graph.py                # graf stanów LangGraph (logika przepływu)
  main.py                 # serwer FastAPI + kolejka zadań

whatsapp-bridge/       # Node.js + Baileys — połączenie z WhatsApp

android-app-template/  # punkt startowy dla NOWEGO repo aplikacji Android
  .github/workflows/ci.yml   # testy jednostkowe/instrumentalne na GitHub Actions

deploy/                # systemd units + skrypt setupu Mikrusa
docs/                   # kopie REQUIREMENTS.md / CHANGELOG.md (referencyjne)
```

## Jak to działa: zapis kodu i odporność na błędy formatu

- **Zapis kodu**: agent developera zwraca listę plików w formacie JSON
  (`[{"path": "...", "content": "..."}]` — patrz `prompts/system_prompts.py`).
  Gdy dane podzadanie przejdzie Review II, pliki są od razu zapisywane na
  dysk w `ANDROID_REPO_PATH` (`tools/fs_tools.py`, z walidacją ścieżek przed
  path traversal). Commit + tag w `versioning_node` obejmuje wszystkie tak
  zaakumulowane pliki ze wszystkich podzadań danego zlecenia, plus
  `CHANGELOG.md`.
- **Odporność na niepoprawny JSON**: `agents/base.py` ma funkcję
  `call_agent_json`, która automatycznie prosi model o poprawienie formatu
  (do 2 dodatkowych prób), zanim zgłosi błąd. Jeśli mimo to się nie uda:
  Reviewer/QA → traktowane jak zwykłe odrzucenie (feedback + `retry_count++`,
  normalna pętla poprawek); DevOps → zastosowany zostaje bezpieczny domyślny
  bump `patch`, żeby nie zgubić już zaakceptowanej pracy; Developer →
  artefakt trafia do Review I jako oznaczony błąd i naturalnie zostaje
  odrzucony przez recenzenta.
- **CI przed ostateczną akceptacją (push-before-QA)**: gdy kod developera
  przejdzie Review I, `push_for_ci_node` pcha go na branch roboczy
  `agent/{task_id}-{subtask_id}` w repo aplikacji Android. Push do
  `agent/**` automatycznie wyzwala `ci.yml` (testy jednostkowe, instrumentalne
  na emulatorze, lint). Orchestrator czeka na wynik i przekazuje go do
  agenta QA jako część kontekstu — QA ma twardy zakaz zaakceptowania kodu
  (`passed=true`), jeśli CI zwróciło `failure`, niezależnie od własnej oceny.
  Jeśli push/CI jest chwilowo niedostępne (np. brak sieci), zadanie nie jest
  blokowane — QA ocenia sam kod, z jawną adnotacją o braku wyniku CI.

## Znane ograniczenia / do dopracowania

- `github_actions.wait_for_ci_result` odpytuje najnowszy run na branchu —
  wystarczające przy jednej kolejce sekwencyjnej (tak jak jest ona
  zaprojektowana), ale kruche, gdyby kiedyś dodać przetwarzanie
  równoległe wielu zadań naraz.
- Branch roboczy `agent/{task_id}-{subtask_id}` używany do wyzwolenia CI nie
  jest automatycznie sprzątany po zadaniu (ani po sukcesie, ani po
  porażce) — warto dodać okresowe czyszczenie starych branchy `agent/**`
  w repo aplikacji.

## Kontrola kosztów

- **Twardy limit**: ustawiony na kluczu OpenRouter (krok 1) — OpenRouter sam
  odrzuci zapytania po przekroczeniu.
- **Alerty**: `tools/cost_monitor.py` sprawdza co godzinę wykorzystanie
  miesięcznego limitu i wysyła ostrzeżenie na WhatsApp po przekroczeniu progu
  `COST_ALERT_THRESHOLD` (domyślnie 80%), oraz gdy limit się wyczerpie.
