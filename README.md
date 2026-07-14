# AiCall Watchdog

Caine de paza pentru AiCall. Ruleaza pe GitHub Actions din 5 in 5 minute,
verifica backend-ul (Render) si site-ul (Vercel), si:

- te **anunta pe Telegram** cand ceva pica sau isi revine (doar la schimbarea starii, fara spam);
- **incearca automat un redeploy** pe Render cand backend-ul nu raspunde (daca e configurat un Deploy Hook).

Nu contine cod din aplicatie — doar verifica adrese publice. Cheile stau in GitHub Secrets, nu in cod.

## Secrete (Settings > Secrets and variables > Actions)

| Secret | Obligatoriu | Ce e |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | da (pt notificari) | tokenul botului de Telegram (de la @BotFather) |
| `TELEGRAM_CHAT_ID` | da (pt notificari) | ID-ul chat-ului unde vin alertele |
| `RENDER_DEPLOY_HOOK` | optional (pt auto-reparare) | URL-ul Deploy Hook din Render (Settings > Deploy Hook) |

Fara `RENDER_DEPLOY_HOOK` face doar detectie + notificare. Cu el, incearca si repornirea automata.

## Ce repara singur si ce nu

- **Repara:** server blocat/crashat/adormit -> redeploy automat.
- **Nu repara (doar anunta):** erori de cod (le rezolva un om), pene la furnizori externi (OpenAI/Twilio pica global).

Test manual: tab **Actions** > AiCall Watchdog > **Run workflow**.
