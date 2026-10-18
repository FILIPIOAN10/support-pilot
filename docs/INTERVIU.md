# SupportPilot — script de interviu

Repo: https://github.com/FILIPIOAN10/support-pilot · dovezi pas cu pas: `docs/JURNAL.md` · teoria: `docs/STUDIU.md`.

**Regula de aur:** „L-am construit și testat local și în containere, cu CI. Nu e în producție și nu a rulat cu un model
real.” Spui exact ce ai verificat și ce nu.

> **Progres (2026-10-18): pașii 1–28 din 32.** Cifrele marcate *(referință)* vin din rularea de verificare a ghidului;
> le înlocuiesc cu ale mele când ajung la pasul respectiv. Până atunci **nu le spun ca fiind ale mele**.

---

## 1. Pitch de 30 de secunde

**EN:** "I built SupportPilot, a customer-support agent on LangChain and LangGraph. It chooses its own tools: it looks
up orders, answers policy questions through my RAG service from my first project, and checks whether an order can be
refunded. The refund itself pauses and waits for a human to approve it, and because the agent's state is checkpointed
in MongoDB, the approval works even after a restart. External calls have timeouts, retries and a circuit breaker, and
customers are rate-limited with Redis."

**RO:** „Am construit SupportPilot, un agent de suport pe LangChain și LangGraph. Alege singur uneltele: caută comenzi,
răspunde la întrebări despre politici prin serviciul RAG din primul meu proiect și verifică dacă o comandă poate fi
rambursată. Rambursarea se oprește și așteaptă aprobarea unui om, iar pentru că starea agentului e salvată în MongoDB,
aprobarea merge și după un restart. Apelurile externe au timeout, retry și circuit breaker, iar clienții sunt limitați
cu Redis.”

## 2. Povestea de 2 minute (EN)

1. **Problem.** "An agent with tools can change things, here money. A model can be manipulated, so 'the prompt says
   don't' is not a control."
2. **Least privilege.** "The model never gets the customer's identity. The server puts it in the run config from the
   authenticated request, and the tools read it there. The model only sees an order id, so it can't read someone
   else's order, and 'not found' and 'not yours' look the same."
3. **Rules in code.** "The refund tool re-checks ownership, delivery status and the 30-day window when it actually runs,
   after the human approved."
4. **Human in the loop.** "A middleware interrupts the graph before the refund tool. The state is checkpointed in
   MongoDB, an admin sees the exact arguments, and approving resumes the same run. Two admins clicking at once: an
   atomic claim lets only one through, and the refund call carries an idempotency key."
5. **Guardrails.** "Six model calls and five tool calls per run at most, and emails are redacted before the model."
6. **Integration.** "Explicit timeouts, retries only on transient errors with backoff and jitter, a circuit breaker per
   dependency. When the order system is down, the agent answers politely instead of failing."
7. **Honest limits.** "I tested it with a scripted model and with the real OpenAI client against a local fake server.
   Whether a real model picks the right tools needs its own evaluation, which is the next step."

## 3. Cifrele tale (și în ce condiții)

| Ce | Cifră | Condiție / sursă |
|---|---|---|
| Teste făcute de mine | **57** (27 fără containere + 20 pe MongoDB și Redis reale + 10 prin HTTP real și contractul OpenAI), ~30 s | build-ul meu, Pas 23 |
| Breaker în fața unui serviciu căzut | cererile 1–2: ~3,7–3,8 s fiecare (3 încercări); cererea 3: **0,00 s**, fără apel | build-ul meu, Pas 6 (`BREAKER_FAILURES=2`, `HTTP_CONNECT_TIMEOUT=0.5`) |
| Idempotența rambursării | aceeași cheie de două ori → același `RF-0001`; altă cheie → `RF-0002` | build-ul meu, Pas 7 (clienți reali contra simulatorului) |
| Serviciu care întoarce 503 | 3 încercări, apoi „unavailable” în 0,77 s | build-ul meu, Pas 7 (`/chaos fail`) |
| Sistemul de comenzi căzut, în containere | cererile 1–5: **~0,9 s** (retry + răspuns politicos); după 5 eșecuri breaker deschis → **0,08 s**; închis la loc după 30 s | build-ul meu, Pas 27 |
| Limitele agentului | 6 apeluri de model, 5 de unelte pe rulare | `config.py` |
| Timeout-uri | 2 s conectare, 8 s citire (DocuAsk 28 s, 2 încercări) | `config.py`, `clients.py` |
| Rate limit | 20 de mesaje / minut / client, fail-open, timeout Redis 0,3 s | `config.py`, `api.py` |
| Redis căzut | fail-open în **0,62 s** cu clientul aplicației; **~9,8 s** cu `redis.Redis(...)` (retry implicit în redis-py 8) | build-ul meu, Pas 15 (măsurat) |
| Acoperire | **95 %** (649 de instrucțiuni, 34 neatinse: fabricile reale, pornirea, ramura Azure) | build-ul meu, Pas 24 |
| Aprobare după restart | reușită după **`docker compose restart api`** (respingere „Item was worn”, procesul nou a reluat rularea din MongoDB) și după un restart simulat în cod | build-ul meu, Pașii 28 și 16 |
| Doi admini simultan | **o singură rambursare** (test cu două thread-uri și o barieră, Mongo real); revendicarea: exact 1 câștigător | build-ul meu, Pașii 14 și 22 |

## 4. Deciziile pe care le poți apăra

1. **Identitatea din configurația rulării, nu de la model** (anti-IDOR).
2. **Autorizarea în unelte, nu în prompt.** Promptul ghidează, codul garantează.
3. **Unealtă separată de eligibilitate**, înainte de `request_refund`: omul nu e deranjat cu cereri care ar eșua.
4. **Pauză reală** (interrupt + checkpoint în Mongo), nu un flag într-un tabel.
5. **Revendicare atomică + cheie de idempotență**: o singură rambursare, oricâte click-uri sau retry-uri.
6. **Reziliență per dependență**: timeout explicit, retry doar pe erori tranzitorii, breaker pentru fiecare serviciu.
7. **Erorile devin text pentru agent** („sistemul e indisponibil”), nu excepții: agentul răspunde politicos.
8. **Rate limit cu fail-open**: un Redis căzut nu oprește serviciul; plafonul de cost rămâne în limitele agentului.
9. **Fiecare bază pentru un motiv**: Postgres+pgvector pentru documente, MongoDB pentru conversații, Redis pentru contoare.
10. **Model fals scriptat implicit**: teste deterministe, CI fără chei și fără cost.

## 5. Întrebări probabile, cu răspunsuri scurte (EN)

**What's the difference between a chain and an agent?** "A chain runs fixed steps in a fixed order, like my RAG
service. An agent lets the model choose the next step and how many steps, so it's more flexible and needs more
guardrails."

**How do you prevent prompt injection from causing damage?** "I assume it will happen. The model has no identity
parameter, the tools enforce ownership and the refund rules, the run is capped, and the one action with a side effect
needs a human. The system prompt also marks tool output and user text as data, but that's guidance, not a control."

**Why does `request_refund` check eligibility again?** "Because the model might skip the check, and the order may
change while the approval waits. The check that counts runs when the action runs."

**How does human approval work technically?** "`HumanInTheLoopMiddleware` interrupts the graph before the refund tool.
The checkpointer saves the state in MongoDB under the conversation's thread id. I store the pending action, the admin
approves or rejects over the API, and I resume with `Command(resume=...)` on the same thread id."

**What if two admins approve at the same moment?** "I claim the approval with `find_one_and_update` from pending to
deciding, which is atomic, so only one request gets it; the other gets a 404. The refund call also sends an
idempotency key, `conversation:order`, so even a retry returns the same refund."

**What if resuming fails, for example the model provider is down?** "The approval goes back to pending, so it can be
retried, instead of staying stuck in 'deciding'."

**How do you handle a slow or failing dependency?** "Explicit connect and read timeouts, retries only on 5xx, 429 and
network errors with exponential backoff and jitter, and a circuit breaker per dependency that fails fast after five
failures in a row and lets one trial call through after 30 seconds. A 404 is a business answer, so it's not retried
and doesn't trip the breaker."

**Why is retrying a POST dangerous?** "If it isn't idempotent, a retry can repeat the effect, two refunds. That's why
the refund carries an idempotency key."

**Why MongoDB and not Postgres?** "A conversation is one self-contained document, read and written whole, with a shape
that changes as the agent grows, and no joins. LangGraph also has a MongoDB checkpointer. Postgres stays in the RAG
project, where integrity between documents and chunks matters."

**Why a Lua script for the rate limit?** "INCR and EXPIRE must happen as one step: otherwise a counter could be left
without an expiry and block a customer forever. Redis runs a Lua script atomically."

**Why fail open?** "A broken limiter shouldn't become an outage. The per-run call limits still cap the cost."

**How do you know the agent chooses the right tools?** "With the scripted model I only prove the system's guarantees.
For a real model I'd build a small evaluation: 15 to 20 customer messages with the expected tool sequence, plus
adversarial ones, and measure how many sequences match, the tokens per conversation and how often it hits the limits."

**How do you track cost?** "Token usage is collected with a callback on every run and added to the conversation in
MongoDB with an atomic increment."

## 6. Ce NU spui

| Nu spune | Spune |
|---|---|
| „E în producție.” | „L-am construit și rulat local și în containere, cu CI.” |
| „L-am testat cu GPT-…” | „L-am testat cu un model scriptat și cu clasa OpenAI contra unui server local; evaluarea cu un model real e pasul următor.” |
| „Agentul rezolvă singur orice.” | „Agentul are limite, drepturi minime și aprobare umană la acțiunea cu bani.” |
| „E imun la prompt injection.” | „Pornesc de la ideea că va fi păcălit; garanțiile sunt în cod, nu în prompt.” |
| cifrele *(referință)* ca fiind ale tale | doar ce ai rulat tu (vezi secțiunea 3) |

## 7. Slăbiciuni pe care să le recunoști singur

1. Identitatea clientului e un header de la un gateway de încredere, nu un token semnat (JWT/OIDC).
2. Fără izolare pe client în DocuAsk: aceleași documente pentru toți.
3. O singură acțiune cu pauză; mai multe acțiuni într-o tură nu au un tratament special.
4. Fără streaming al răspunsului agentului.
5. Fără tracing distribuit; doar loguri.
6. Rate limit cu fereastră fixă (vârf la granița dintre ferestre).
7. Comportamentul unui model real nu e măsurat.

## 8. Cum spui cinstit cum l-ai construit

L-ai construit pas cu pas, după un plan scris, cu ajutor de la un asistent AI: tu ai scris codul în repo, ai rulat și
ai verificat fiecare pas, iar asistentul a explicat, a comparat cu o referință și a scris jurnalul.

> "I built it step by step from a written plan, with AI assistance for explanations and review. I typed and ran every
> step, and I can walk through any file and the reason behind it."

Ca să fie adevărat, **să poți explica fiecare fișier**. Dacă n-ai un răspuns, spune „nu am testat asta”, nu inventa.

## 9. Demo de 5 minute (când e gata, Faza H)

1. `docker compose up -d --build` (și DocuAsk pornit, cu `shop-docs/` încărcate).
2. „Where is my order 1002?” ca `c-100` → starea din sistemul de comenzi.
3. „How do returns work?” → răspuns cu sursa adusă de DocuAsk.
4. „Where is my order 1003?” ca `c-100` → „nu există” (e a lui `c-200`).
5. „I want a refund for order 1001” → `awaiting_approval`; `GET /v1/admin/approvals` arată argumentele exacte.
6. `docker compose restart api`, apoi aprobi → rambursarea apare în conversație.
7. `/chaos {"mode":"fail"}` → agentul răspunde politicos, apoi breaker-ul răspunde instant.

## 10. Întrebări de pus tu

- „Which actions do your agents take today, and which of them require human approval?”
- „How do you evaluate tool selection and agent behaviour before a release?”
- „How do you pass user identity and permissions to tools in your agents?”
- „Do you use LangGraph checkpointing, and where do you store agent state?”
