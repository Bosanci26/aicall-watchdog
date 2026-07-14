# Asistent AiCall (monitorizare)

Asistentul care vegheaza AiCall. Ruleaza pe GitHub Actions din 5 in 5 minute,
verifica backend-ul (Render), site-ul (Vercel) si **soldul la furnizori**, si:

- te **anunta pe Telegram** cand ceva pica sau isi revine (doar la schimbarea starii, fara spam);
- te anunta cand **raman bani putini** la Twilio / OpenAI / ElevenLabs — inainte sa pice apelurile;
- **incearca automat un redeploy** pe Render cand backend-ul nu raspunde (daca e configurat un Deploy Hook).

Nu contine cod din aplicatie. Cheile furnizorilor stau pe server (backend), nu aici;
asistentul citeste doar un rezumat de stare protejat de o cheie.

## Secrete (Settings > Secrets and variables > Actions)

| Secret | Obligatoriu | Ce e |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | da (notificari) | tokenul botului de Telegram (@BotFather) |
| `TELEGRAM_CHAT_ID` | da (notificari) | ID-ul chat-ului unde vin alertele |
| `HEALTH_KEY` | pt sold furnizori | cheia pt `/api/health/providers` (derivata din secretul Supabase) |
| `RENDER_DEPLOY_HOOK` | optional (auto-reparare) | URL-ul Deploy Hook din Render |

Praguri optionale (variabile de mediu in workflow): `TWILIO_LOW_USD` (implicit 5), `ELEVENLABS_LOW_PCT` (implicit 10).

## Ce repara singur si ce nu

- **Repara:** server blocat/crashat/adormit -> redeploy automat.
- **Anunta (nu repara):** bani putini la furnizori (reincarci tu), erori de cod (le rezolva un om), pene externe (OpenAI/Twilio pica global).

Test alarma (fara sa atinga productia): tab **Actions** > AiCall Watchdog > **Run workflow** > `simulate: down`.
