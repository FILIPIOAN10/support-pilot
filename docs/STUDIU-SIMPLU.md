# SupportPilot — varianta simplă (de învățat în 15 minute)

Detaliile sunt în `docs/STUDIU.md`; aici ai doar esența. Progres: pașii 1–6 din 32 (2026-10-17).

---

## 1. Ideea, într-o frază

**SupportPilot e un agent de suport care caută singur comenzi și politici, dar pentru orice rambursare așteaptă
aprobarea unui om, iar aprobarea rezistă și la un restart.**

## 2. Analogia ghișeului

| În aplicație | La ghișeu |
|---|---|
| Agentul (modelul în buclă) | angajatul nou de la ghișeu |
| Uneltele | sertarele la care are acces: comenzi, regulament, formularul de rambursare |
| `customer_id` din cerere, nu de la model | legitimația verificată la intrare; nu contează ce spune clientul că e |
| Regulile în unelte | sertarul cu bani are lacăt, oricât de convingător e clientul |
| Aprobarea umană | semnătura șefului pe formularul de rambursare |
| Checkpoint în MongoDB | dosarul pus în sertar: îl reia oricine, și după pauza de masă |
| Limitele (6 / 5) | angajatul nu are voie să alerge la nesfârșit între sertare |
| Circuit breaker | dacă telefonul spre depozit sună ocupat de 5 ori, nu mai sună 30 de secunde |
| Rate limit | un client nu poate sta la ghișeu cu 100 de cereri pe minut |

## 3. Cele două drumuri

**Drumul 1: o întrebare obișnuită**
Mesaj → cheie + client verificate → rate limit → agentul alege unealta (comandă sau politici) → răspuns.

**Drumul 2: o rambursare**
Mesaj → agentul verifică eligibilitatea → cere rambursarea → **PAUZĂ** (salvată în Mongo) → clientul primește
„trimis spre aprobare” → un admin aprobă → agentul **continuă aceeași rulare** → unealta reverifică → rambursare creată
cu cheie de idempotență.

## 4. Cele patru plase de siguranță (le știi pe de rost)

1. **Identitatea nu vine de la model:** uneltele citesc clientul din cererea autentificată.
2. **Regulile stau în unelte:** proprietar + livrată + 30 de zile, reverificate după aprobare.
3. **Limite:** max 6 apeluri de model și 5 de unelte pe rulare; e-mailurile redactate înainte de model.
4. **Un om în fața banilor:** pauză reală, revendicare atomică (doi admini = o rambursare), cheie de idempotență.

## 5. Opt lucruri de ținut minte

| Ce | Valoarea |
|---|---|
| Uneltele | **4**: `get_order`, `search_policies`, `check_refund_eligibility`, `request_refund` |
| Limitele agentului | **6** apeluri de model, **5** de unelte pe rulare |
| Fereastra de rambursare | **30** de zile de la livrare |
| Timeout-uri | **2 s** conectare, **8 s** citire (DocuAsk 28 s) |
| Circuit breaker | se deschide după **5** eșecuri, stă deschis **30 s**, apoi **1** apel de probă |
| Rate limit | **20** de mesaje pe minut pe client, Redis, fail-open |
| Porturi | API 8001, comenzi 9000, Mongo **27018**, Redis **6380** |
| Teste | acum **7** (resilience); ținta *(referință)*: 57, 95 % acoperire |

## 6. Cinci răspunsuri de o frază (EN)

- **What is an agent?** "A language model in a loop with tools: it decides which tool to call, my code runs it, and the
  result goes back to the model until it answers or hits a limit."
- **How do you stop it from doing damage?** "Least privilege: identity comes from the request, not the model; the rules
  are in the tools; calls are capped; and the only tool that moves money waits for a human."
- **How does the approval survive a restart?** "It's a LangGraph interrupt, and the checkpointer saves the state in
  MongoDB, so any process can resume it with the same thread id."
- **What if two admins approve at the same time?** "The approval is claimed with an atomic find-one-and-update, and the
  refund call carries an idempotency key, so exactly one refund is created."
- **What doesn't it prove?** "The scripted model proves the system's guarantees, not that a real model picks the right
  tools; that needs its own evaluation."

## 7. Cum înveți în 15 minute

1. **2 minute:** citește analogia (secțiunea 2).
2. **5 minute:** pe o foaie, desenează de pe memorie Drumul 2 (rambursarea), cu pauza și reluarea.
3. **3 minute:** spune cu voce tare propoziția din secțiunea 1 și cele patru plase de siguranță.
4. **5 minute:** auto-testul de mai jos, fără să te uiți.

## 8. Auto-test

1. Ce face programul, într-o frază?
2. Care sunt cele patru unelte?
3. Care sunt cele patru plase de siguranță?
4. De unde știe unealta cine e clientul?
5. Ce se întâmplă cu sistemul de comenzi căzut?
6. Ce NU dovedește modelul fals?

### Răspunsuri
1. Un agent de suport care caută comenzi și politici singur, dar pentru rambursări așteaptă un om; aprobarea rezistă la
   restart.
2. `get_order`, `search_policies`, `check_refund_eligibility`, `request_refund`.
3. Identitatea din cerere, regulile în unelte, limitele (6/5 + redactare), omul în fața banilor (pauză + revendicare
   atomică + idempotență).
4. Din configurația rulării (`config["configurable"]["customer_id"]`), pusă de server din cererea autentificată.
5. Retry de 3 ori, apoi unealta întoarce „sistemul e indisponibil” și agentul răspunde politicos; după 5 eșecuri
   breaker-ul se deschide și răspunsul vine imediat, fără apel.
6. Că un model real alege bine uneltele; dovedește doar că sistemul (bucla, drepturile, limitele, pauza) e corect.
