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

---
