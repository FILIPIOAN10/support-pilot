# SupportPilot — fișă de studiu

Citește-o de sus în jos, o dată, apoi folosește secțiunea 8 (auto-test). Dovezile, pas cu pas: `docs/JURNAL.md`.
Varianta de 15 minute: `docs/STUDIU-SIMPLU.md`. Scriptul de interviu: `docs/INTERVIU.md`. Cartonașe: `docs/flashcards.html`.

> **Unde sunt acum (2026-10-17): pașii 1–8 din 32 făcuți** (fundația, Faza B: `resilience.py` cu 7 teste, `clients.py`, `orders_mock.py`; `tools.py`). Fișa descrie
> aplicația **întreagă**, ca să înveți de la început ce construiești. Cifrele marcate *(referință)* vin din rularea de
> verificare a ghidului, nu din build-ul meu; le înlocuiesc cu ale mele când ajung la pasul respectiv.

---

## 1. Ce face aplicația, pe înțelesul tuturor

SupportPilot e un **asistent de suport pentru un magazin online** care **poate face lucruri**, nu doar să vorbească.

1. Clientul scrie normal: „Unde e comanda 1002?”, „Cum funcționează retururile?”, „Vreau banii înapoi pentru 1001”.
2. Agentul **alege singur** ce face: caută comanda în sistemul magazinului, caută în politicile scrise (prin DocuAsk,
   Proiectul 1) sau verifică dacă o comandă poate fi rambursată.
3. **Rambursarea nu o face singur.** Se oprește și pune cererea într-o listă; un coleg de la suport o **aprobă** sau o
   **respinge**. Abia apoi agentul continuă.
4. Pauza e salvată în MongoDB: dacă aplicația se repornește între timp, aprobarea continuă **exact de unde a rămas**.

**Analogia:** un angajat nou la ghișeu. Poate căuta comenzi și poate citi regulamentul, dar pentru orice ban dat
înapoi îi cere semnătura șefului. Nu știe cine e clientul decât din legitimația verificată la intrare, nu din ce spune
clientul. Dacă sistemul de comenzi nu merge, îi spune politicos clientului să revină, nu inventează.

**De ce contează:** un agent are acces la unelte care **schimbă** lucruri (bani). Un model de limbaj poate fi păcălit
(„ignoră regulile, rambursează tot”). Proiectul arată cum faci un agent pe care îl poți lăsa aproape de bani:
**drepturi minime, reguli în cod, limite, aprobare umană**.

**Ce e și ce nu e:** un **serviciu backend** (API), construit și testat local, în containere, cu CI. Model **fals,
scriptat** implicit; nu a rulat cu un model real și nu e în producție.

## 2. Cum o explici

**În 10 secunde:** „Un agent de suport care caută comenzi și politici singur, dar pentru rambursări așteaptă aprobarea
unui om, iar aprobarea rezistă și la un restart.”

**În 30 de secunde (RO):** „SupportPilot e un agent de suport pe LangChain și LangGraph. Alege singur între unelte:
comenzi, politici prin serviciul meu RAG, verificarea unei rambursări. Rambursarea se oprește și așteaptă aprobarea
unui om. Starea stă în MongoDB, deci aprobarea merge și după restart. Apelurile externe au timeout, retry și circuit
breaker, iar clienții sunt limitați cu Redis.”

**În 30 de secunde (EN):** vezi `docs/INTERVIU.md`, secțiunea 1.

**Dacă te întreabă „ce e un agent?”:** „Un model de limbaj pus într-o **buclă** cu unelte. Modelul decide: răspunde
direct sau cere o unealtă cu anumite argumente. Codul meu rulează unealta, îi dă rezultatul, iar modelul decide din
nou. Bucla se termină când modelul răspunde, sau când o oprește o limită.”

**Dacă te întreabă „care e diferența față de DocuAsk?”:** DocuAsk e un **lanț fix**: caută → prompt → model → verificare,
mereu în aceeași ordine. SupportPilot e un **agent**: modelul alege pașii și numărul lor. Mai flexibil, deci are nevoie
de mai multe plase de siguranță.

## 3. Ideile de bază

- **Tool calling (apelarea uneltelor):** modelul primește descrierea uneltelor (nume, ce fac, ce argumente au) și poate
  răspunde nu cu text, ci cu „apelează `get_order` cu `order_id=1002`”. **Modelul nu rulează nimic**: codul tău rulează.
- **LangGraph:** motorul care rulează bucla ca pe un graf de pași (model → unelte → model) și îi **salvează starea** după
  fiecare pas, printr-un **checkpointer**.
- **Checkpointer (`MongoDBSaver`):** salvează starea conversației în MongoDB, sub un `thread_id` (la mine = id-ul
  conversației). Datorită lui, o pauză poate fi reluată oricând, de orice proces.
- **Interrupt (pauza):** un pas care oprește graful și așteaptă o decizie din afară. Reluarea se face cu
  `Command(resume=...)`, pe același `thread_id`.
- **Middleware:** straturi care învelesc bucla: pot modifica mesajele înainte de model, pot număra apelurile, pot opri
  graful înaintea unei unelte.

## 4. Cum am implementat agentul

### 4.1 Drumul unei cereri

```text
client (prin gateway)  ──►  POST /v1/chat   X-API-Key + X-Customer-Id
                               │
                    rate limit (Redis, Lua)  ──► 429 + Retry-After dacă prea multe
                               │
                    SupportService.chat
                               │  conversație nouă / existentă (Mongo); a altuia? → 404; în așteptare? → 409
                               ▼
                    ┌────────────  AGENT (LangChain create_agent pe LangGraph) ────────────┐
                    │ PIIMiddleware: e-mailurile din mesaj → [REDACTED_EMAIL]              │
                    │ buclă: model ─► cere unealtă ─► o rulăm ─► rezultat ─► model          │
                    │   max 6 apeluri de model, max 5 de unelte pe rulare                  │
                    │ HumanInTheLoop: request_refund ⇒ PAUZĂ (checkpoint în Mongo)          │
                    └───────────────────────────────────────────────────────────────────────┘
          get_order ─► Orders API      search_policies ─► DocuAsk (Proiectul 1)
          check_refund_eligibility     request_refund ─► Orders API (cu Idempotency-Key)
          (toate cu timeout + retry + circuit breaker; identitatea clientului din cerere, NU de la model)
                               │
                    pauzat?  ──► approvals (Mongo)  ──► un admin: POST /v1/admin/approvals/{id}
                               │                         Command(resume=...) continuă același run
                    răspuns ◄──┘
```

### 4.2 Scenariul rambursării, pas cu pas

| # | Ce se întâmplă | Unde în cod |
|---|---|---|
| 1 | Clientul `c-100` trimite „I want a refund for order 1001”. API-ul verifică cheia gateway-ului (comparare în timp constant) și formatul `X-Customer-Id` | `api.py` → `customer_id()` |
| 2 | Rate limit: contor Redis pe client, 20/minut. Peste limită → **429 + `Retry-After`** | `ratelimit.py` → `RateLimiter.hit()` |
| 3 | Se creează conversația în Mongo (`status: open`, tokeni 0) | `store.py` → `new_conversation()` |
| 4 | Agentul rulează cu `config = {thread_id: conversația, customer_id: "c-100"}` | `service.py` → `chat()`, `_config()` |
| 5 | Modelul cere `check_refund_eligibility(order_id="1001")`. Unealta ia `customer_id` **din config**, cere comanda, verifică proprietarul, starea `delivered` și fereastra de **30 de zile** | `tools.py` |
| 6 | Rezultat „eligibil” → modelul cere `request_refund(order_id, reason)` | `fake.py` / modelul real |
| 7 | `HumanInTheLoopMiddleware` **oprește graful înaintea uneltei**. Starea se salvează în Mongo | `agent.py` |
| 8 | `result.interrupts` nu e gol → se salvează o aprobare `pending` cu **argumentele exacte**; conversația trece în `awaiting_approval`; clientul primește un **mesaj fix**, nu din model | `service.py` → `_after_run()`, `store.py` → `open_approval()` |
| 9 | Un mesaj nou în aceeași conversație → **409** până decide cineva | `service.py` → `ApprovalPending` |
| 10 | Adminul vede lista: `GET /v1/admin/approvals` (cheia de admin) | `api.py` |
| 11 | Adminul aprobă. **Revendicare atomică**: `find_one_and_update(status pending → deciding)`. Doi admini simultan: unul câștigă, celălalt primește 404 | `store.py` → `claim_approval()` |
| 12 | `Command(resume={"decisions": [{"type": "approve"}]})` **reia aceeași rulare** din checkpoint | `service.py` → `decide()` |
| 13 | `request_refund` rulează abia acum și **reverifică** proprietarul și eligibilitatea, apoi cere rambursarea cu `Idempotency-Key = thread_id:order_id` | `tools.py`, `clients.py` |
| 14 | Aprobarea primește `done`, cine a decis și când (audit). Dacă reluarea a eșuat, aprobarea revine în `pending` | `store.py` → `finish_approval()` / `release_approval()` |

*(referință)* Rezultatul real al scenariului: `awaiting_approval` → adminul aprobă → `"Done. Refund RF-0002 is
registered and pending."`. După `docker compose restart api`, o respingere a mers pe aceeași conversație.

### 4.3 Cele patru unelte

| Unealtă | Ce face | Ce o face sigură |
|---|---|---|
| `get_order(order_id)` | Starea comenzii | Doar comenzile clientului curent; câmpurile interne (`customer_id`, `internal_note`) nu ajung la model; „nu există” și „nu e a ta” au **același răspuns** |
| `search_policies(question)` | Întreabă DocuAsk | Răspuns **cu surse** sau „politicile nu acoperă asta, nu ghici” |
| `check_refund_eligibility(order_id)` | Livrată și în 30 de zile? | Doar citește |
| `request_refund(order_id, reason)` | Cere rambursarea | Pauză pentru om; **reverifică** la rulare; cheie de idempotență |

`config: RunnableConfig` din semnătura uneltelor **nu apare în schema** trimisă modelului: LangChain îl injectează.
Modelul vede doar `order_id` (și `reason` / `question`).

### 4.4 Cele patru middleware-uri

| Middleware | Ce face | De ce |
|---|---|---|
| `PIIMiddleware("email", strategy="redact")` | E-mailurile din mesaje devin `[REDACTED_EMAIL]` **înainte** de model | Minimizarea datelor trimise unui furnizor (GDPR) |
| `ModelCallLimitMiddleware(run_limit=6)` | Oprește după 6 apeluri de model pe rulare | Un agent care se învârte costă bani |
| `ToolCallLimitMiddleware(run_limit=5)` | Oprește după 5 apeluri de unelte | Plafon de efecte |
| `HumanInTheLoopMiddleware({"request_refund": ...})` | Pauză înainte de `request_refund`; decizii `approve` / `reject` | Singura unealtă cu efect (bani) cere un om |

### 4.5 Integrarea cu alte servicii (ce am construit deja, Pasul 5)

```text
 closed ──(5 eșecuri la rând)──► open ──(trec 30 s)──► half-open ──(1 apel de probă)──┬─ reușit ─► closed
                                                                                    └─ eșuat ──► open
```

| Răspuns | Retry? | Breaker | Ce vede restul aplicației |
|---|---|---|---|
| 5xx, 429, eroare de rețea | da: 3 încercări, backoff ~0,2 / 0,4 / 0,8 s + jitter | eșec | `ServiceUnavailable` |
| 404 | nu | succes | `NotFound` → `None` („nu există”) |
| 401, 400 | nu (greșeala mea) | succes | `ServiceUnavailable("... rejected")` |
| breaker deschis | — | — | `ServiceUnavailable("... temporarily")`, **fără apel** |

Timeout explicit: 2 s conectare, 8 s citire (DocuAsk: 28 s, doar 2 încercări, fiindcă un răspuns LLM e lent și costă).
Fiecare serviciu are **breaker-ul lui**. Unealta transformă `ServiceUnavailable` în **text** („sistemul e indisponibil”),
deci agentul răspunde politicos în loc să cadă.

### 4.6 Unde stau datele

| Bază | Ce ține | De ce aceasta |
|---|---|---|
| **MongoDB** | `conversations` (client, stare, tokeni), `approvals` (acțiuni, stare, cine a decis), checkpoint-urile LangGraph | Documente întregi, formă care se schimbă, fără JOIN-uri; `find_one_and_update` atomic |
| **Redis** | `rl:{client}`: contor cu expirare | Contor atomic, rapid, care expiră singur |
| **Postgres + pgvector** (Proiectul 1) | documentele și vectorii politicilor | Integritate între tabele, căutare vectorială |

### 4.7 Harta fișierelor

| Fișier | Rolul lui | Stare |
|---|---|---|
| `config.py` | setări din mediu, validate la pornire; două chei, limite, timeout-uri | ✅ Pas 2 |
| `resilience.py` | `CircuitBreaker` + `ResilientHttp` (timeout, retry, breaker) | ✅ Pas 5 |
| `clients.py` | `OrdersClient`, `DocuAskClient` peste `ResilientHttp` | ✅ Pas 6 |
| `orders_mock.py` | sistemul de comenzi simulat, cu „chaos mode” | ✅ Pas 7 |
| `tools.py` | cele 4 unelte; autorizarea e **aici**, nu în prompt | ✅ Pas 8 |
| `fake.py`, `llm.py` | model fals scriptat; fabrica de modele (fake / openai / azure) | Pașii 10–11 |
| `agent.py` | `create_agent` + cele 4 middleware-uri + promptul de sistem | Pas 12 |
| `store.py`, `ratelimit.py`, `service.py` | Mongo, Redis, cazurile de utilizare | Pașii 14–16 |
| `schemas.py`, `api.py`, `main.py` | contractul HTTP, autentificarea, rutele, pornirea | Pașii 17–19 |
| `tests/` | *(referință)* 57 de teste pe MongoDB și Redis reale, 95 % acoperire | Pașii 9, 13, 21–24 |
| `Dockerfile`, `docker-compose.yml`, `ci.yml` | livrare și CI | Pașii 25–29 |

## 5. Dicționar de termeni

| Termen | Pe scurt |
|---|---|
| **Agent** | model de limbaj într-o buclă cu unelte; modelul alege pașii |
| **Tool calling** | modelul răspunde cu „apelează unealta X cu argumentele Y”; codul o rulează |
| **LangGraph** | rulează bucla ca graf de pași și îi salvează starea |
| **Checkpointer** | unde se salvează starea grafului (la mine `MongoDBSaver`) |
| **thread_id** | cheia sub care se salvează starea unei conversații |
| **Interrupt / `Command(resume=...)`** | pauza grafului / reluarea ei cu o decizie |
| **Middleware** | strat în jurul buclei: redactare, limite, aprobare |
| **Human-in-the-loop** | un om aprobă acțiunea înainte să ruleze |
| **IDOR** | citești datele altcuiva schimbând un id; evitat: identitatea nu e argument al modelului |
| **Prompt injection** | text care încearcă să schimbe regulile modelului; apărarea reală e în cod |
| **Idempotență** | același apel repetat are același efect ca unul singur (`Idempotency-Key`) |
| **Circuit breaker** | după N eșecuri nu mai apelezi o vreme; eșuezi imediat |
| **Backoff + jitter** | pauze crescătoare între reîncercări, cu un adaos aleatoriu |
| **Fail-open** | dacă limitatorul (Redis) cade, cererile trec; serviciul nu se oprește |
| **Fereastră fixă** | contor pe interval de 60 s care se resetează; permite un vârf la graniță |
| **Revendicare atomică** | `find_one_and_update`: o singură operație citește și schimbă starea; doi admini nu pot câștiga amândoi |

## 6. Ce NU face și ce NU am verificat

- Nu a rulat cu un **model real**. Modelul fals e scriptat (expresii regulate): arată că **sistemul** e corect (bucla,
  uneltele, limitele, pauza), **nu** că un model real alege bine uneltele.
- Identitatea clientului e un **header de la un gateway de încredere**, nu un token semnat.
- Fără streaming, fără tracing distribuit, rate limit cu fereastră fixă.
- Nu e în producție. Workflow-ul de CI: de verificat când ajung la Pasul 29.

## 7. Răspunsul scurt la „cum ai construit agentul?” (EN, ~60–90 s)

"It's a LangChain `create_agent` running on LangGraph, with four tools: look up an order, search the policies through
my RAG service, check refund eligibility, and request a refund. The model chooses the tool and the arguments, but it
never gets the customer's identity: the server puts it in the run configuration from the authenticated request, and the
tools read it from there, so a manipulated model still can't read someone else's order. Authorization lives in the
tools, not in the prompt.

Around the loop there are four middlewares: email redaction before the model, a cap of six model calls and five tool
calls per run, and a human-in-the-loop pause in front of the refund tool. The pause is a real LangGraph interrupt saved
in MongoDB, so an admin can approve after a restart. The approval is claimed atomically, so two admins clicking at once
create one refund, and the refund call carries an idempotency key. Every external call has explicit timeouts, retries
on transient errors with backoff and jitter, and a circuit breaker."

## 8. Auto-test (răspunde în gând, apoi verifică la final)

1. Ce e un agent și cum diferă de lanțul din DocuAsk?
2. De ce `customer_id` nu e argument al uneltelor?
3. De ce `request_refund` reverifică eligibilitatea, deși modelul a apelat deja `check_refund_eligibility`?
4. Ce face fiecare dintre cele 4 middleware-uri?
5. Cum funcționează pauza pentru aprobare și de ce rezistă la restart?
6. Ce se întâmplă dacă doi admini apasă „aprobă” în aceeași secundă?
7. De ce `Idempotency-Key` la rambursare?
8. Ce se reîncearcă și ce nu? De ce nu deschide 404 breaker-ul?
9. Ce face un circuit breaker în cele trei stări?
10. De ce un script Lua pentru rate limit și de ce fail-open?
11. De ce MongoDB aici și Postgres în Proiectul 1?
12. Ce NU dovedește modelul scriptat?

### Răspunsuri
1. Un model într-o buclă cu unelte, care alege singur pașii și numărul lor. DocuAsk e un lanț fix (caută → prompt →
   model → verificare), mereu la fel.
2. Ca un model păcălit să nu poată cere comenzile altcuiva (IDOR). Identitatea vine din cererea autentificată, prin
   configurația rulării; modelul vede doar `order_id`.
3. Regulile stau în cod, nu în prompt: modelul poate sări pasul, iar starea comenzii se poate schimba cât așteaptă
   aprobarea. Reverificarea rulează **după** aprobare.
4. Redactează e-mailurile înainte de model; oprește după 6 apeluri de model; oprește după 5 apeluri de unelte; pune
   pauză înainte de `request_refund` până decide un om.
5. `HumanInTheLoopMiddleware` oprește graful (interrupt), iar checkpointer-ul salvează starea în MongoDB sub
   `thread_id`. Orice proces o reia cu `Command(resume=...)` pe același `thread_id`.
6. `find_one_and_update` mută atomic aprobarea din `pending` în `deciding`: unul primește documentul, celălalt `None` →
   404. Plus cheia de idempotență la sistemul de comenzi. Rezultat: o singură rambursare.
7. Un retry sau un dublu-click ar crea a doua rambursare; cu aceeași cheie (`thread_id:order_id`) sistemul întoarce
   aceeași rambursare.
8. Se reîncearcă 5xx, 429 și erorile de rețea. Nu se reîncearcă 404 (răspuns de business, serviciul e sănătos) și
   alte 4xx (greșeala mea, repetarea nu ajută). 404 nu e un eșec, deci nu deschide breaker-ul.
9. Closed: apelurile trec. Open: eșuezi imediat, fără apel. Half-open: un singur apel de probă; reușit → closed, eșuat
   → open încă o perioadă.
10. `INCR` + `EXPIRE` într-un singur pas atomic: nu se strecoară alt client între ele și nu rămâne un contor fără
    expirare. Fail-open: un limitator stricat nu trebuie să oprească serviciul; limitele de apeluri de model rămân.
11. O conversație e un document întreg, cu formă care se schimbă, fără JOIN-uri. În Proiectul 1 contează integritatea
    între documente și bucăți, plus vectorii.
12. Că un model real alege bine uneltele. Dovedește doar garanțiile sistemului: bucla, drepturile, limitele, pauza.
