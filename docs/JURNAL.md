# SupportPilot — jurnal de construcție

Agent de suport pentru un magazin online: LangChain `create_agent` (pe LangGraph) + unelte, MongoDB, Redis, aprobare
umană pentru rambursări. Construit pas cu pas după ghidul `03-Proiect-2-SupportPilot-Agent.pdf` (32 de pași, faze A–J).
Jurnalul e pentru mine, la interviu: pentru fiecare pas am scris **ce am făcut, de ce, ce am verificat (cu rezultate
reale)** și **cum o spun**.

Regula de onestitate: spun „construit și testat local”, nu „în producție”. Ce nu a rulat încă e marcat explicit.

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
