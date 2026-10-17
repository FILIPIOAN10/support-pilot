# SupportPilot — jurnal de construcție

Agent de suport pentru un magazin online: LangChain `create_agent` (pe LangGraph) + unelte, MongoDB, Redis, aprobare
umană pentru rambursări. Construit pas cu pas după ghidul `03-Proiect-2-SupportPilot-Agent.pdf` (32 de pași, faze A–J).
Jurnalul e pentru mine, la interviu: pentru fiecare pas am scris **ce am făcut, de ce, ce am verificat (cu rezultate
reale)** și **cum o spun**.

Regula de onestitate: spun „construit și testat local”, nu „în producție”. Ce nu a rulat încă e marcat explicit.

Pentru învățat: `docs/STUDIU.md` (fișa completă), `docs/STUDIU-SIMPLU.md` (15 minute), `docs/INTERVIU.md` (script de
interviu, RO + EN), `docs/flashcards.html` (35 de cartonașe; se deschide în browser). Le actualizez pe măsură ce avansez.

---

## Faza A — Fundația

### Pas 1 — Proiectul și dependențele · 2026-10-17

#### Ce am făcut
- Repo nou `supportpilot/` cu `git init -b main`, layout `src/supportpilot`, `tests`, `shop-docs`, `docs`.
- `.python-version` = `3.12` (scris cu `Set-Content -Encoding ascii`, nu cu `echo >`, care în PowerShell 5.1 scrie UTF-16).
- `pyproject.toml` cu dependențele, apoi `uv sync` → `.venv` cu Python 3.12.12 și `uv.lock` (80 de pachete).

#### Dependențele și de ce
| Pachet | La ce folosește | Nou față de DocuAsk? |
|---|---|---|
| `fastapi`, `uvicorn` | API-ul HTTP (`POST /v1/chat`, aprobările de admin) | nu |
| `pydantic-settings` | Configurația din mediu, validată la pornire | nu |
| `httpx` | Client HTTP spre sistemul de comenzi și spre DocuAsk | da |
| `tenacity` | Retry cu backoff exponențial + jitter | da |
| `pymongo` | MongoDB: conversații și aprobări | da |
| `redis` | Rate limit: contor atomic cu expirare | da |
| `langchain` | `create_agent` + middleware (PII, limite, aprobare umană) | altă parte din el |
| `langchain-openai` | Modelul real (OpenAI/Azure), doar dacă e ales | nu |
| `langgraph` | Motorul buclei agentului: model → unealtă → model | da |
| `langgraph-checkpoint-mongodb` | `MongoDBSaver`: starea agentului (și pauza pentru aprobare) în MongoDB | da |
| `testcontainers[mongodb,redis]` (dev) | MongoDB și Redis reale în teste | alte containere |

#### Decizii și de ce
| Decizie | De ce |
|---|---|
| `uv.lock` commitat | Aceleași versiuni pe laptop, în Docker și în CI |
| Layout `src/` + hatchling | Importul merge doar din pachetul instalat, prinde greșeli de import |
| Grup `dev` separat | Teste, Testcontainers și ruff nu intră în imaginea de producție |
| Agent pe LangGraph cu checkpointer | Starea se salvează, deci o pauză pentru aprobare umană e reală și rezistă la restart |

#### Ce am verificat (rezultate reale)
- `uv run python -c "from langchain.agents import create_agent; from langgraph.checkpoint.mongodb import MongoDBSaver; print('ok')"` → `ok`
- Versiuni rezolvate: `langchain 1.4.6`, `langgraph 1.2.16`, `pymongo 4.19.0`, `redis 8.1.0`, `fastapi 0.143.3`.
- `pyproject.toml` identic cu codul de referință (singura diferență: lipsește linia goală de la final, fără efect).

#### Cum o spun la interviu
- „Pentru agent am folosit `create_agent` din LangChain, care rulează pe LangGraph. L-am ales pentru că starea agentului
  se salvează printr-un checkpointer, la mine în MongoDB, și asta face posibilă o pauză reală pentru aprobare umană,
  care rezistă și la un restart.”
- „Dependențele sunt blocate în `uv.lock`, deci CI-ul și imaginea Docker instalează exact aceleași versiuni.”

### Pas 2 — `config.py` · 2026-10-17

#### Ce am făcut
- `src/supportpilot/config.py`: o clasă `Settings` (pydantic-settings) cu toate cheile, URL-urile și limitele, citite din
  mediu sau din `.env`, plus `get_settings()` cu `@lru_cache` (citită o singură dată).

#### Decizii și de ce
| Decizie | De ce |
|---|---|
| Două chei: `gateway_api_key` și `admin_api_key` | Două roluri: magazinul (care a autentificat clientul și trimite `X-Customer-Id`) și colegii care aprobă rambursări |
| `SecretStr` pentru chei | La `print`/log apare `**********`; valoarea se scoate doar explicit cu `get_secret_value()` |
| `llm_provider: Literal[...] = "fake"` | Fail fast la o valoare greșită; implicit fără cheie, fără rețea, fără bani |
| `max_model_calls = 6`, `max_tool_calls = 5` | Un agent e o buclă care decide singur câte ture face; are nevoie de plafon |
| `http_connect_timeout = 2`, `http_read_timeout = 8` | Timeout-uri mereu explicite spre servicii externe |
| `breaker_failures = 5`, `breaker_reset_seconds = 30` | Când se deschide circuit breaker-ul și cât stă deschis (Pas 5) |
| Porturi 27018 (Mongo), 6380 (Redis) | Nu se ciocnesc cu instanțe locale pe porturile standard |
| `extra="ignore"` | Variabile necunoscute în `.env` nu opresc aplicația |

#### Ce am verificat (rezultate reale)
- Valorile implicite:
  ```text
  fake mongodb://localhost:27018 redis://localhost:6380/0
  ********** dev-admin-key
  6 5 2.0 8.0
  ```
- `$env:LLM_PROVIDER="banana"` → `ValidationError ... Input should be 'fake', 'openai' or 'azure'` (fail fast, confirmat).
- Fișierul e identic cu referința; `ruff check` curat. `ruff format --check` a cerut linia goală finală, adăugată cu
  `uv run ruff format src`.

#### Capcane
- Variabila de mediu setată pentru test rămâne în terminal: `Remove-Item Env:LLM_PROVIDER` imediat după.
- Editorul nu pune automat linia goală de la final → CI-ul cu `ruff format --check` ar pica. Rulez `ruff format`
  înainte de commit.

#### Cum o spun la interviu
- „Am două chei pentru două roluri: gateway-ul magazinului, care a autentificat deja clientul, și adminii care aprobă
  rambursările. Identitatea clientului vine dintr-un header pus de gateway; într-un sistem real ar fi un token semnat.”
- „Agentul are plafoane configurabile, 6 apeluri de model și 5 de unelte pe rulare: un agent e o buclă și fără limită
  poate consuma bani la nesfârșit.”
- „Timeout-urile spre serviciile externe sunt mereu explicite: 2 secunde conectare, 8 citire.”

### Pas 3 — `.env.example`, `.gitignore`, `.dockerignore` · 2026-10-17

#### Ce am făcut
- `.env.example` (în git): documentează fiecare setare; numele = câmpurile din `config.py` cu majuscule
  (`MAX_TOOL_CALLS` → `max_tool_calls`). Cheile reale apar doar comentate.
- `.gitignore`: `.venv/`, `__pycache__/`, cache-uri, `.coverage`, **`.env`**.
- `.dockerignore`: în plus față de `.gitignore` scoate `.git`, `tests` și `shop-docs` din imagine.
- `.env` local, copiat din `.env.example` (nu intră în git).

#### Decizii și de ce
| Decizie | De ce |
|---|---|
| `.env` ignorat, `.env.example` commitat | Setările sunt documentate, secretele nu ajung în istoric (repo-ul e public) |
| Valori de dev = valorile implicite din `config.py` | Aplicația pornește și fără `.env`; fișierul doar arată ce se poate schimba |
| `.dockerignore` separat | Imaginea nu primește `.venv` (sute de MB), `.git`, `.env` cu chei sau testele |

#### Ce am verificat (rezultate reale)
- După `Copy-Item .env.example .env`, `git status --short` arată doar `.dockerignore`, `.env.example`, `.gitignore`:
  `.env` și `__pycache__/` sunt ignorate (`git check-ignore -v` → `.gitignore:7:.env`, `.gitignore:2:__pycache__/`).
- Comentariile de pe același rând din `.env` sunt ignorate la citire: `admin_api_key` → `'dev-admin-key'`.
- Conținutul e identic cu referința (am adăugat doar linia goală finală).

#### Capcane
- `.gitignore` nu scoate un fișier deja urmărit de git: ar trebui `git rm --cached .env`, iar cheia, fiind publicată,
  **rotită** (rămâne în istoric).

#### Cum o spun la interviu
- „Secretele stau doar în `.env`, ignorat de git și de Docker. În repo e `.env.example`, care documentează fiecare
  setare. Dacă un secret ajunge în git, nu e de ajuns să-l ștergi: îl rotești, pentru că rămâne în istoric.”

### Pas 4 — MongoDB și Redis pentru dezvoltare · 2026-10-17

#### Ce am făcut
- `docker run -d --name sp-mongo -p 27018:27017 mongo:8`
- `docker run -d --name sp-redis -p 6380:6379 redis:7-alpine`
- Porturile de pe calculator (27018, 6380) sunt exact cele din `config.py` (`mongo_url`, `redis_url`).

#### Decizii și de ce
| Decizie | De ce |
|---|---|
| Porturi nestandard 27018 / 6380 | Nu se ciocnesc cu alte instanțe locale pe 27017 / 6379 |
| Containere fără volum | Sunt doar pentru dezvoltare; testele își pornesc containerele lor (Testcontainers) |
| Fiecare bază are un motiv | Postgres+pgvector (Proiectul 1): documente și vectori. MongoDB: conversații și aprobări (documente întregi, formă care se schimbă, fără JOIN). Redis: contoare atomice cu expirare (rate limit) |

#### Ce am verificat (rezultate reale)
- `docker exec sp-redis redis-cli ping` → `PONG`
- `docker exec sp-mongo mongosh --quiet --eval "db.hello().ok"` → `1`
- Din aplicație, cu URL-urile din `get_settings()`: MongoDB **8.3.13** `{'ok': 1.0}`, Redis **7.4.11** `True`.

#### Capcane
- Portul 6380 e mapat și de containerele oprite `ticketbox-redis` și `ecommerce-redis`: nu le porni în același timp cu
  `sp-redis` (`port is already allocated`).
- `docker run` se face o singură dată; după un restart: `docker start sp-mongo sp-redis`.

#### Cum o spun la interviu
- „Fiecare bază de date e acolo pentru un motiv: Postgres cu pgvector pentru documente și vectori, MongoDB pentru
  conversații și aprobări, Redis pentru contoarele de rate limit.”

### ✅ Checkpoint A — fundația
- [x] importul din Pas 1 afișează `ok`
- [x] Mongo răspunde `1`, Redis răspunde `PONG`
- [x] commit-uri pentru pașii 1–3 (urcate pe GitHub), Pas 4 = doar jurnal

---

## Faza B — Servicii externe fără bătăi de cap

### Pas 5 — `resilience.py`: timeout, retry, circuit breaker · 2026-10-17

#### Ce am făcut
- `src/supportpilot/resilience.py`: trei excepții (`Transient`, `NotFound`, `ServiceUnavailable`), un
  `CircuitBreaker` și `ResilientHttp` (client `httpx` cu timeout explicit + retry `tenacity` + breaker), câte o instanță
  pe fiecare serviciu extern.
- `tests/test_resilience.py`: 7 teste cu `httpx.MockTransport` (server în memorie) și un ceas fals.

#### Cum funcționează
```text
 closed ──(N eșecuri la rând)──► open ──(trec reset_seconds)──► half-open ──(1 apel de probă)──┬─ reușit ─► closed
                                                                                                └─ eșuat ──► open
```
| Răspuns | Retry? | Breaker | Ce vede aplicația |
|---|---|---|---|
| 5xx, 429, eroare de rețea | da, backoff exponențial + jitter (~0,2 / 0,4 / 0,8 s, max 2 s) | eșec | `ServiceUnavailable("orders is unavailable")` |
| 404 | nu | succes (serviciul e sănătos) | `NotFound` → clientul întoarce `None` |
| alt 4xx (401, 400) | nu (greșeala e a mea) | succes | `ServiceUnavailable("... rejected the request")` |
| breaker deschis | — | — | `ServiceUnavailable("... temporarily unavailable")` **fără apel de rețea** |

#### Decizii și de ce
| Decizie | De ce |
|---|---|
| Timeout explicit `Timeout(8, connect=2)` | Un serviciu blocat nu-mi ține thread-urile la nesfârșit |
| Retry doar pe erori tranzitorii | Repetarea unui 401 sau 404 nu poate ajuta |
| Jitter la backoff | Clienții nu reîncearcă toți în aceeași milisecundă (thundering herd) |
| Half-open lasă **un singur** apel de probă | Un serviciu abia repornit nu e lovit de toate cererile odată |
| `threading.Lock` în breaker | FastAPI rulează funcțiile sincrone pe mai multe thread-uri |
| Ceas injectabil | Testul „sare” 31 s fără `sleep` |
| Mesaje de eroare sigure | Fără detalii interne spre model sau client |

#### Ce am verificat (rezultate reale)
- `uv run pytest tests/test_resilience.py -q` → `7 passed in 0.18s`
- `ruff check` curat, `ruff format` aplicat.
- Codul e identic cu referința ca **sens** (AST identic); diferă doar împărțirea pe rânduri (vezi Capcane).

#### Capcane
- **Retry pe un POST neidempotent dublează efectul** (două rambursări). Remediu: `Idempotency-Key` la `create_refund`
  (Pas 6).
- Extensia Ruff din VS Code (format on save) a rupt rândurile la 88 de caractere, deși proiectul are `line-length = 120`:
  fișierul a fost deschis cu `code fișier` într-o fereastră care nu era folderul proiectului, deci extensia nu a văzut
  `pyproject.toml`. Ruff păstrează apoi rândurile rupte din cauza virgulei finale („magic trailing comma”). Fără efect
  asupra codului; remediu: deschid folderul proiectului cu `code .`.

#### Cum o spun la interviu
- „Fiecare apel extern are timeout explicit. Fac retry doar pe erori tranzitorii, cu backoff exponențial și jitter. Un
  404 e un răspuns de business, nu un eșec: nu-l reîncerc și nu deschide breaker-ul. Un 401 e greșeala mea, deci nu-l
  reîncerc. Peste toate e un circuit breaker: după 5 eșecuri la rând eșuez imediat timp de 30 de secunde, apoi las un
  singur apel de probă.”
- „Retry-ul pe un POST cu efect e periculos; de aceea rambursarea trimite o cheie de idempotență.”

### Pas 6 — `clients.py`: clienții pentru sistemul de comenzi și DocuAsk · 2026-10-17

#### Ce am făcut
- `OrdersClient` cu `get_order` (404 → `None`) și `create_refund` (POST cu header `Idempotency-Key`).
- `DocuAskClient` cu `ask` (`POST /v1/ask` spre Proiectul 1).
- `make_orders_client` / `make_docuask_client`: construiesc fiecare client din `Settings`, cu `ResilientHttp` și
  **breaker propriu**.

#### Decizii și de ce
| Decizie | De ce |
|---|---|
| Metode cu nume de business | Uneltele nu știu de HTTP, chei, retry sau breaker |
| `NotFound` → `None` în `get_order` | „Nu există” e un răspuns normal, nu o eroare |
| `Idempotency-Key` la `create_refund` | Un retry sau un dublu-click întoarce aceeași rambursare, nu una nouă |
| Breaker separat pe serviciu | Dacă pică DocuAsk, comenzile merg în continuare |
| DocuAsk: citire 28 s (8 + 20), 2 încercări | Un răspuns LLM e lent, iar repetarea lui costă bani |
| Clientul primește `ResilientHttp` gata construit | Dependency injection: în teste îi dai un transport fals, fără setări |

#### Ce am verificat (rezultate reale, pe codul meu)
Sistemul de comenzi nu exista încă (port 9000 închis) = „serviciul a picat”. Cu `BREAKER_FAILURES=2` și
`HTTP_CONNECT_TIMEOUT=0.5` (demonstrația scurtată din configurare, fără să schimb codul):
```text
orders timeout:  Timeout(connect=0.5, read=8.0, write=8.0, pool=8.0)
docuask timeout: Timeout(connect=0.5, read=28.0, write=28.0, pool=28.0)
call 1: orders is unavailable | breaker=closed | 3.70s
call 2: orders is unavailable | breaker=open | 3.83s
call 3: orders is temporarily unavailable | breaker=open | 0.00s
```
- Cererile 1–2: câte 3 încercări cu backoff (~4 s). După a doua, breaker-ul e `open`.
- Cererea 3: **0,00 s**, fără apel de rețea, mesaj „temporarily”: a răspuns breaker-ul.
- Cu valorile implicite (5 eșecuri, 2 s) aceeași demonstrație durează ~67 s.
- Fișierul are același sens ca referința (AST identic); `ruff check` curat, `ruff format` aplicat.

#### Capcane
- Pe Windows, un port închis pe `localhost` dă `ConnectTimeout` (se așteaptă tot timeout-ul), nu un refuz imediat.
- Header-ele HTTP nu țin cont de litere mari/mici: `X-Api-Key` (comenzi) și `X-API-Key` (DocuAsk) merg amândouă.

#### Cum o spun la interviu
- „Fiecare serviciu extern are clientul lui, cu metode cu nume de business, timeout-urile și breaker-ul lui. Serviciul
  RAG are timp de citire mai lung și mai puține retry-uri, pentru că un apel LLM e lent și costă. Am demonstrat
  breaker-ul: după două cereri eșuate, a treia a fost respinsă în zero secunde, fără apel de rețea.”

### Pas 7 — `orders_mock.py`: sistemul de comenzi simulat · 2026-10-17

#### Ce am făcut
- Un serviciu FastAPI separat (`uvicorn supportpilot.orders_mock:app --port 9000`) cu 4 comenzi, cheie API
  (`X-Api-Key`), rambursări idempotente după `Idempotency-Key` și un „chaos mode” (`/chaos`: `fail` = 503, `slow` = 10 s,
  `ok`).

| Comandă | Client | Stare | Ce testează |
|---|---|---|---|
| 1001 | c-100 | livrată acum 5 zile | rambursare permisă |
| 1002 | c-100 | expediată, cu `internal_note` | starea comenzii; nota internă nu trebuie să ajungă la model |
| 1003 | c-200 | livrată acum 45 de zile | în afara ferestrei de 30 de zile; „comanda altcuiva” pentru c-100 |
| 1004 | c-200 | plasată | nelivrată → nu se rambursează |

#### Decizii și de ce
| Decizie | De ce |
|---|---|
| Un serviciu HTTP real, nu un mock în memorie | Retry-urile, timeout-ul și breaker-ul se văd pe rețea, ca în realitate |
| Date relative la pornire (`_ago(5)`) | „Acum 5 zile” rămâne adevărat oricând pornesc serviciul |
| Chaos mode | Pot strica serviciul la cerere ca să demonstrez reziliența |
| Idempotența pe server, după cheie | Partea de server a ce trimite clientul din Pas 6 |

#### Ce am verificat (rezultate reale)
- `curl.exe -H "X-Api-Key: dev-orders-key" localhost:9000/orders/1002` → comanda întreagă, **inclusiv**
  `customer_id` și `internal_note` (serviciul extern trimite tot; filtrarea se face în unealtă, Pas 8).
- Fără cheie → `{"detail":"bad key"}` (401).
- Clienții mei (Pas 6) contra simulatorului:
  ```text
  1002: shipped
  9999: None
  refund 1: {'refund_id': 'RF-0001', 'order_id': '1001', 'status': 'pending'}
  refund 2: {'refund_id': 'RF-0001', 'order_id': '1001', 'status': 'pending'}
  refund 3: {'refund_id': 'RF-0002', 'order_id': '1001', 'status': 'pending'}
  orders failed after retries: Transient
  chaos=fail: orders is unavailable after 0.77s
  chaos=ok, 1002: shipped
  ```
  - 404 → `None`; **aceeași cheie de idempotență → aceeași rambursare** (`RF-0001` de două ori); altă cheie → `RF-0002`.
  - Cu `fail`: 3 încercări (trei `503` în log-ul serverului), apoi `ServiceUnavailable` în 0,77 s.
- Fișierul are același sens ca referința (AST identic), `ruff` curat.

#### Capcane
- `/chaos` nu are cheie: acceptabil doar într-un simulator de dezvoltare.
- Starea (rambursări, mod) e în memorie: la repornire, `RF-` începe iar de la `0001`.
- În PowerShell se scrie `curl.exe`, nu `curl` (alias pentru `Invoke-WebRequest`).

#### Cum o spun la interviu
- „Am un simulator al sistemului de comenzi, cu patru comenzi alese să acopere fiecare caz și un chaos mode care
  întoarce 503 sau răspunde lent. Așa demonstrez retry-ul, timeout-ul și breaker-ul pe un serviciu HTTP real. Am
  verificat și idempotența: aceeași cheie de două ori a întors aceeași rambursare.”

### ✅ Checkpoint B — servicii externe
- [x] pot desena circuit breaker-ul (closed → open → half-open)
- [x] pot explica de ce 404 nu se reîncearcă și nu deschide breaker-ul
- [x] simulatorul răspunde pe 9000
- [x] commit pe fiecare pas (5, 6, 7)

---

## Faza C — Uneltele agentului

### Pas 8 — `tools.py`: unde stă securitatea · 2026-10-17

#### Ce am făcut
- `build_tools(orders, docuask, now)` întoarce 4 unelte LangChain (`@tool`): `get_order`, `search_policies`,
  `check_refund_eligibility`, `request_refund`.
- Funcții ajutătoare: `own_order` (doar comenzile clientului curent), `_public` (doar câmpurile publice),
  `_eligibility` (livrată + 30 de zile).

#### Decizii și de ce
| Decizie | De ce |
|---|---|
| Identitatea din `config: RunnableConfig`, nu argument | Parametrul nu apare în schema văzută de model; un model păcălit nu poate cere comenzile altcuiva (IDOR) |
| „Nu există” = „nu e a ta” | Nu confirm că un număr de comandă există |
| `_public` scoate `customer_id`, `internal_note` | Minimizarea datelor: modelul nu vede ce nu trebuie |
| `request_refund` reverifică proprietarul și eligibilitatea | Modelul poate sări verificarea; comanda se poate schimba cât așteaptă aprobarea; regula stă unde se face acțiunea |
| Cheie de idempotență `thread_id:order_id` | O rambursare pe comandă pe conversație, oricâte reluări |
| `ServiceUnavailable` → text, nu excepție | Agentul răspunde politicos în loc să cadă |
| Docstring-ul = descrierea pentru model | Modelul îl citește ca să aleagă unealta („ALWAYS call this before request_refund”) |
| `now` injectabil | Regula de 30 de zile se testează fără să depind de ceas |

#### Ce am verificat (rezultate reale)
Uneltele rulate direct, fără model (eu am jucat rolul modelului), contra simulatorului de comenzi:
```text
what the model sees: {'get_order': ['order_id'], 'search_policies': ['question'], 'check_refund_eligibility': ['order_id'], 'request_refund': ['order_id', 'reason']}
1) c-100, get_order 1002: {"id": "1002", "status": "shipped", ..., "carrier": "FastCourier", "eta": "2026-10-19", ...}
2) c-100, get_order 1003: No order 1003 was found for this customer.
3) c-100, get_order 9999: No order 9999 was found for this customer.
4) c-100, eligibility 1001: {"order_id": "1001", "eligible": true, "reason": "delivered within the last 30 days"}
5) c-200, eligibility 1003: {"order_id": "1003", "eligible": false, "reason": "it was delivered more than 30 days ago"}
6) c-100, request_refund 1003: Order 1003 cannot be refunded.
7) search_policies: The system is unavailable right now. Tell the customer to try again in a few minutes.
```
- Schema: **nicio unealtă nu primește `customer_id` sau `config`**.
- 1): față de `curl` din Pas 7 lipsesc `customer_id` și `internal_note`.
- 2) = 3): comanda lui c-200 arată exact ca una inexistentă.
- 6): un „model păcălit” care cere direct rambursarea comenzii altcuiva → unealta refuză singură.
- 7): DocuAsk oprit → după 2 încercări, text clar, nu excepție.
- Fișierul are același sens ca referința (AST identic), `ruff` curat.

#### Capcane
- Nu da niciodată modelului un parametru de identitate.
- Un `RunnableConfig` în semnătură e completat de LangChain la rulare; dacă unealta e apelată fără `config`,
  `_customer` cade (testele și serviciul îl dau mereu).

#### Cum o spun la interviu
- „Modelul alege unealta și argumentele, dar nu are drepturi. Identitatea clientului vine din cererea autentificată,
  prin configurația rulării; am verificat schema și modelul vede doar `order_id`. Comanda altcuiva arată exact ca una
  inexistentă, câmpurile interne nu ajung la model, iar regulile de rambursare sunt reverificate când acțiunea chiar
  rulează.”

### Pas 9 — Testele uneltelor · 2026-10-17

#### Ce am făcut
- `tests/conftest.py` **minimal** (îl înlocuiesc în Faza G): fixturile `settings` (ignoră `.env`), `orders`
  (`FakeOrders`), `docuask` (`FakeDocuAsk`), `tools`. Serviciile false au aceleași metode ca clienții reali și un
  comutator `down` care aruncă `ServiceUnavailable`.
- `tests/test_tools.py`: 9 funcții de test (una cu `parametrize` pe 2 cazuri) = 10 teste.

#### Garanțiile testate
| Test | Garanția |
|---|---|
| internal fields | `internal_note` și `customer_id` nu ajung la model |
| someone else's order = missing one | același text, doar alt număr |
| identity is not an argument | schema lui `get_order` e exact `["order_id"]`; un parametru de identitate adăugat vreodată → testul pică |
| eligibility rules (×2) + 45 days | livrată în 30 de zile → da; expediată → nu; 45 de zile → nu |
| request_refund rechecks | un model păcălit care sare verificarea → **nicio** rambursare (`orders.refunds == {}`) |
| refunding twice | aceeași conversație → o rambursare, cheia `conv-1:1001` |
| policies | răspuns cu sursă; refuz explicit, nu ghicit |
| service down | toate cele 4 unelte întorc text „unavailable”, nu excepții |

#### Decizii și de ce
| Decizie | De ce |
|---|---|
| Unelte testate fără model și fără rețea | Rapid (0,2 s), determinist, exact pe regulile de securitate |
| Servicii false cu aceleași metode | Uneltele depind de metode de business, nu de HTTP (decizia din Pas 6) |
| `Settings(_env_file=None)` | Rezultatul nu depinde de `.env`-ul meu local |
| Test pe **schema** uneltei | Garanția anti-IDOR e verificată automat, nu doar promisă |

#### Ce am verificat (rezultate reale)
- `uv run pytest -q` → **`17 passed in 0.20s`** (7 resilience + 10 unelte), fără containere.
- Ambele fișiere au același sens ca referința (AST identic); `ruff check` curat, `ruff format` aplicat.

#### Capcane
- **Greșeală în ghid:** PDF-ul zice că `uv run pytest tests/test_tools.py -q` dă 17. Dă **10** (doar fișierul uneltelor);
  17 vine din `uv run pytest -q`.

#### Cum o spun la interviu
- „Testez uneltele direct, fără model, cu servicii false. Testele sunt lista mea de garanții: modelul nu vede câmpurile
  interne, comanda altcuiva arată ca una inexistentă, schema conține doar `order_id`, un model compromis nu poate crea o
  rambursare nepermisă, iar un serviciu căzut dă un mesaj clar, nu o excepție.”

### ✅ Checkpoint C — uneltele
- [x] testele uneltelor trec (17 în total)
- [x] pot explica de ce identitatea clientului nu e argument
- [x] pot explica de ce `request_refund` reverifică eligibilitatea
- [x] commit pe fiecare pas (8, 9)

---

## Faza D — Agentul

### Pas 10 — `fake.py`: un model scriptat, determinist · 2026-10-17

#### Ce am făcut
- `ScriptedSupportModel(BaseChatModel)`: un model de chat fals, din câteva expresii regulate, care răspunde ca un model
  real cu apelare de unelte (`AIMessage(content="", tool_calls=[...])`).
- Două situații: ultimul mesaj e **de la client** (alege unealta după număr de comandă + cuvinte cheie) sau e un
  **rezultat de unealtă** (scrie răspunsul din el, ori cere `request_refund` după „eligible: true”).
- Tokeni estimați (~1 token / 4 caractere) în `usage_metadata`, ca numărarea costului să poată fi testată.

#### Decizii și de ce
| Decizie | De ce |
|---|---|
| Model fals implicit | Determinist, gratuit, fără cheie și fără rețea: teste și CI stabile |
| Respectă `BaseChatModel` (`_generate`, `bind_tools`) | `create_agent` îl folosește exact ca pe `ChatOpenAI` |
| „refund” verificat înaintea lui „status” | „When will my refund for 1001 arrive?” duce la rambursare, nu la starea comenzii |
| `usage_metadata` plauzibil | Costul pe conversație se poate testa fără model real |

#### Ce am verificat (rezultate reale)
```text
'Where is my order 1002?'            -> tool_calls=[('get_order', {'order_id': '1002'})] content=''
'I want a refund for order 1001'     -> tool_calls=[('check_refund_eligibility', {'order_id': '1001'})] content=''
'How do returns work?'               -> tool_calls=[('search_policies', {'question': 'How do returns work?'})] content=''
'Hi there'                           -> tool_calls=[] content='Hello! I can check an order, explain our policies, or start a refund request.'
after eligible=true  -> [('request_refund', {'order_id': '1001', 'reason': 'I want a refund for order 1001'})]
usage: {'input_tokens': 28, 'output_tokens': 1, 'total_tokens': 29}
```
- Un model care cere o unealtă are `content=''` și `tool_calls`; un răspuns final are text și nicio unealtă.
- A doua tură: după „eligible: true”, cere `request_refund` (pasul unde agentul va face pauză pentru om).
- Fișierul are același sens ca referința (AST identic), `ruff` curat; 17 teste în continuare verzi.

#### Capcane
- Modelul fals dovedește că **sistemul** e corect (bucla, unelte, limite, pauză), **nu** că un model real alege bine
  unealta. Aceea se măsoară separat (Pas 30).

#### Cum o spun la interviu
- „Am un model fals, scriptat, care respectă interfața de chat model din LangChain și întoarce `tool_calls` ca unul
  real. Îmi dă determinism: același mesaj produce mereu aceeași secvență de unelte. Dovedește garanțiile sistemului,
  nu calitatea alegerilor unui model real.”

### Pas 11 — `llm.py`: fabrica modelului · 2026-10-17

#### Ce am făcut
- `make_chat_model(settings)`: `fake` → `ScriptedSupportModel`, `openai` → `ChatOpenAI`, `azure` → `AzureChatOpenAI`.
- `_need(value, name)`: o setare lipsă → `ProviderConfigError` cu numele variabilei; scoate cheia din `SecretStr` doar
  când o dă clientului.

#### Decizii și de ce
| Decizie | De ce |
|---|---|
| Un singur loc construiește modelul | Restul aplicației primește doar `BaseChatModel`; schimb furnizorul dintr-o variabilă |
| Eroare cu numele variabilei, apelată la pornire | Fail fast: o cheie lipsă oprește procesul imediat, nu la primul client |
| `timeout=30`, `max_retries=2` | Și apelul LLM e o dependență externă |
| `base_url` opțional | Testul de contract rulează clasa reală `ChatOpenAI` contra unui server local (Faza G) |
| Azure cere `azure_deployment` | La Azure alegi deployment-ul tău, nu numele modelului |

#### Ce am verificat (rezultate reale)
Rulat de mine pe codul meu (în terminal am creat și șters `try_llm.py` fără să-l rulez):
```text
fake  : ScriptedSupportModel
openai: OPENAI_API_KEY is required for this LLM_PROVIDER
azure : AZURE_CHAT_DEPLOYMENT is required for this LLM_PROVIDER
openai: ChatOpenAI gpt-4.1-mini timeout 30.0 retries 2
```
- Cu o cheie de umplutură, `ChatOpenAI` se **construiește** fără niciun apel de rețea.
- Fișierul are același sens ca referința (AST identic), `ruff` curat, 17 teste verzi.

#### Capcane
- Modelul ales trebuie să știe **tool calling** (`gpt-4.1-mini`, `gpt-4o-mini` știu).
- O variabilă `OPENAI_API_KEY` globală în Windows ar face cazul „lipsește cheia” să nu mai dea eroare.

#### Cum o spun la interviu
- „Modelul se construiește într-un singur loc, dintr-o setare: fals pentru teste și CI, OpenAI sau Azure pentru rulare
  reală. Fabrica rulează la pornire, deci o cheie lipsă oprește procesul imediat, cu numele variabilei care lipsește.”

### Pas 12 — `agent.py`: agentul și plasele lui de siguranță · 2026-10-17

#### Ce am făcut
- `build_agent(model, tools, checkpointer, settings)` = `create_agent(...)` cu promptul de sistem și 4 middleware-uri:
  `PIIMiddleware("email", strategy="redact")`, `ModelCallLimitMiddleware(run_limit=6)`,
  `ToolCallLimitMiddleware(run_limit=5)` (ambele `exit_behavior="end"`), `HumanInTheLoopMiddleware` pe
  `request_refund` (`approve` / `reject`).
- `SYSTEM_PROMPT`: ce unealtă pentru ce, nimic din memorie, „trimis spre aprobare”, textul din unelte și din mesaj = date.

#### Decizii și de ce
| Decizie | De ce |
|---|---|
| Agent (buclă) în loc de lanț fix | Modelul alege uneltele și numărul de pași |
| Redactare e-mail înainte de model | Minimizarea datelor trimise unui furnizor (GDPR) |
| Limite 6 / 5 din setări | Un agent blocat într-o buclă costă bani; plafon de efecte |
| Pauză umană doar pe `request_refund` | Singura unealtă cu efect (bani); restul doar citesc |
| Checkpointer obligatoriu | Fără stare salvată, pauza nu poate fi reluată |
| Promptul ghidează, codul garantează | Un model păcălit tot se lovește de unelte, limite și aprobare |

#### Ce am verificat (rezultate reale, pe codul meu, cu modelul fals, `FakeOrders` și `InMemorySaver`)
```text
2) refund: runs until the pause
   AIMessage    [('check_refund_eligibility', {'order_id': '1001'})]
   ToolMessage  '{"order_id": "1001", "eligible": true, ...}'
   AIMessage    [('request_refund', {'order_id': '1001', 'reason': 'I want a refund for order 1001'})]
   interrupts: [[{'name': 'request_refund', 'args': {'order_id': '1001', 'reason': '...'}, ...}]]
   refunds created so far: {}
3) a human approves: the SAME run continues
   ToolMessage  '{"refund_id": "RF-0001", "status": "pending"}'
   AIMessage    'Done. Refund RF-0001 is registered and pending.'
   refunds created: {'t2:1001': {'refund_id': 'RF-0001', 'status': 'pending'}}
4) e-mail in the message
   what the model got: hi, I am [REDACTED_EMAIL]
```
- O întrebare de comandă: model → `get_order` → rezultat → răspuns („Order 1002 is shipped …”).
- Rambursarea: bucla merge singură până la `request_refund` și se **oprește înaintea uneltei**; `interrupts` are
  argumentele exacte; **nicio rambursare** până la aprobare.
- `Command(resume={"decisions": [{"type": "approve"}]})` pe același `thread_id` → abia acum rulează unealta; cheia
  `t2:1001`.
- Fișierul are același sens ca referința (AST identic), `ruff` curat, 17 teste verzi. (Scriptul de verificare l-am
  rulat eu pe codul meu; rezultatul din terminal nu a fost copiat.)

#### Capcane
- `invoke(..., version="v2")` e obligatoriu: fără el nu există `result.interrupts`.
- `InMemorySaver` pierde starea la restart; în Faza E îl înlocuiesc cu `MongoDBSaver`.

#### Cum o spun la interviu
- „Agentul e `create_agent` din LangChain, un graf LangGraph: model, unelte, model, până la un răspuns. În jurul buclei:
  redactarea e-mailurilor, maximum 6 apeluri de model și 5 de unelte, și o pauză human-in-the-loop înaintea
  rambursării. Pauza e un interrupt real salvat prin checkpointer; adminul o reia cu `Command(resume=...)` pe același
  thread id. Am verificat că până la aprobare nu se creează nicio rambursare.”

### Pas 13 — Testele agentului, fără containere · 2026-10-17

#### Ce am făcut
- `tests/test_agent.py`: 10 teste pe bucla reală (`create_agent`), cu modelul scriptat, `InMemorySaver` și serviciile
  false din `conftest.py`.
- Trei „modele de test” derivate din cel fals: `Rogue` (păcălit: cere rambursarea comenzii 1003 a altui client),
  `Looping` (blocat: cere `get_order` la nesfârșit), `Recording` (notează tot ce primește modelul).

#### Garanțiile testate
| # | Test | Garanția |
|---|---|---|
| 1 | order question | `get_order`, răspuns din rezultat, fără pauză |
| 2 | policy question | `search_policies`, răspuns cu „30 days” |
| 3 | refund | secvența exactă `[check_refund_eligibility, request_refund]`, pauză, **nimic creat** |
| 4 | approval | aceeași rulare continuă, rambursare cu cheia `r2:1001` |
| 5 | rejection | nicio rambursare, clientul află motivul („Used item.”) |
| 6 | ineligible (45 de zile) | nu ajunge la om, nicio pauză |
| 7 | **compromised model** | **omul aprobă și unealta tot refuză**: `orders.refunds == {}` |
| 8 | stuck in a loop | limitele opresc bucla (≤ 6 apeluri de model) |
| 9 | e-mail | modelul nu vede adresa, vede `[REDACTED_EMAIL]` |
| 10 | per thread | stările conversațiilor sunt separate pe `thread_id` |

#### Ce am verificat (rezultate reale)
- `uv run pytest tests/test_agent.py -v` → **10 PASSED**, `10 passed in 1.02s`.
- `uv run pytest -q` → **`27 passed in 1.07s`** (7 resilience + 10 unelte + 10 agent), fără containere.
- Fișierul are același sens ca referința (AST identic), `ruff` curat.

#### Decizii și de ce
| Decizie | De ce |
|---|---|
| Modele „rele” scrise anume (`Rogue`, `Looping`) | Un model real nu se poate forța să fie păcălit sau blocat la cerere; garanțiile se dovedesc cu el scriptat |
| `InMemorySaver` aici, `MongoDBSaver` în Faza E | Bucla se testează în ~1 s; persistența se testează separat, cu baza reală |
| Verific secvența exactă de unelte | Modelul fals e determinist, deci ordinea e o garanție, nu un noroc |

#### Capcane
- Testul 7 arată că **nici aprobarea umană nu e de ajuns**: omul poate aproba din greșeală. Ultima apărare e unealta,
  care verifică proprietarul la execuție (apărare în straturi).
- Câmpurile noi pe un model LangChain se declară pydantic (`Field(default_factory=list)`), nu în `__init__`.

#### Cum o spun la interviu
- „Testez agentul fără containere, cu un checkpointer în memorie și un model scriptat. Am și modele «rele» scrise
  anume: unul compromis, care cere rambursarea comenzii altcuiva — omul aprobă, iar unealta tot refuză — și unul
  blocat într-o buclă, pe care îl opresc limitele. Verific și că e-mailul nu ajunge la model și că până la aprobare nu
  se creează nimic.”

### ✅ Checkpoint D — agentul
- [x] `test_agent.py`: 10 teste trec (27 în total)
- [x] pot explica diferența dintre lanț (DocuAsk) și agent (SupportPilot)
- [x] pot spune la ce servește fiecare din cele 4 middleware-uri
- [x] pot explica ce NU dovedește un model scriptat
- [x] commit pe fiecare pas (10–13)

---
