#!/usr/bin/env python3
"""
Asistent de paza SmartBiz: verifica backend + frontend + SOLDUL la furnizori +
CODUL (agentul de vanzari si traducerea au toate functiile? rutele exista?
Twilio raporteaza erori de telefonie?), anunta pe Telegram cand ceva pica sau
isi revine, si (optional) cere automat un redeploy pe Render.

Fara dependinte (doar stdlib). Ruleaza pe GitHub Actions din 5 in 5 minute.
Notifica DOAR la SCHIMBAREA starii (anti-spam): OK->problema si problema->OK.
Starea precedenta e pastrata intre rulari prin cache-ul GitHub Actions.
"""
import json
import os
import time
import urllib.parse
import urllib.request

BACKEND = os.environ.get("BACKEND_URL", "").rstrip("/")
FRONTEND = os.environ.get("FRONTEND_URL", "").rstrip("/")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
DEPLOY_HOOK = os.environ.get("RENDER_DEPLOY_HOOK", "")
HEALTH_KEY = os.environ.get("HEALTH_KEY", "")  # verificare sold furnizori
STATE_FILE = "watchdog-state.txt"

# Praguri "bani putini" - anunta INAINTE sa ramai fara
TWILIO_LOW_USD = float(os.environ.get("TWILIO_LOW_USD", "5"))       # ~100 min RO
ELEVENLABS_LOW_PCT = float(os.environ.get("ELEVENLABS_LOW_PCT", "10"))  # sub 10% ramas

# Serviciile fara de care SmartBiz nu poate traduce un apel
CRIT_DEPS = ["supabase", "openai", "elevenlabs", "twilio"]


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "aicall-watchdog"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.getcode(), r.read().decode("utf-8", "replace")


def notify(text):
    if not TG_TOKEN or not TG_CHAT:
        print("NOTIFY (Telegram neconfigurat):\n" + text)
        return
    try:
        data = urllib.parse.urlencode({
            "chat_id": TG_CHAT, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": "true",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=data)
        urllib.request.urlopen(req, timeout=20)
        print("Telegram trimis.")
    except Exception as e:
        print("Telegram a esuat:", e)


def check_backend():
    """Returneaza (status, mesaj). status: OK | DEGRADED | DOWN.
    Toleranta la cold-start Render: cateva incercari inainte de a-l declara jos."""
    last_err = None
    for attempt in range(4):
        try:
            code, body = http_get(BACKEND + "/", timeout=35)
            if code == 200:
                try:
                    j = json.loads(body)
                except Exception:
                    return "DEGRADED", "backend raspunde dar nu cu JSON valid"
                marker = j.get("code_marker", "?")
                bad = [d for d in CRIT_DEPS if not j.get(d, False)]
                if bad:
                    return "DEGRADED", f"backend pornit ({marker}) dar servicii cazute: {', '.join(bad)}"
                return "OK", f"backend OK ({marker})"
            last_err = f"HTTP {code}"
        except Exception as e:
            last_err = str(e)
        time.sleep(20)
    return "DOWN", f"backend NU raspunde ({last_err})"


def check_frontend():
    try:
        code, _ = http_get(FRONTEND + "/", timeout=25)
        return code == 200, f"HTTP {code}"
    except Exception as e:
        return False, str(e)


def check_providers():
    """Verifica soldul la furnizori. Returneaza lista de probleme (text)."""
    if not HEALTH_KEY:
        return []
    try:
        code, body = http_get(
            BACKEND + "/api/health/providers?key=" + urllib.parse.quote(HEALTH_KEY),
            timeout=30)
        if code != 200:
            return []  # nu blocam - reachability o prinde alt check
        p = json.loads(body)
    except Exception as e:
        print("providers check a esuat:", e)
        return []

    problems = []
    tw = p.get("twilio", {})
    if tw.get("ok") and tw.get("balance_usd") is not None:
        bal = tw["balance_usd"]
        if bal < TWILIO_LOW_USD:
            problems.append(f"💳 <b>Twilio: bani putini</b> — au ramas ${bal:.2f} (sub ${TWILIO_LOW_USD:.0f}). Reincarca.")
    elif not tw.get("ok"):
        problems.append(f"🔴 Twilio nu raspunde: {tw.get('error','?')}")

    oa = p.get("openai", {})
    if not oa.get("ok"):
        problems.append(f"🔴 <b>OpenAI</b> (traducerea) nu merge: {oa.get('error','?')}")

    el = p.get("elevenlabs", {})
    if el.get("ok") and el.get("chars_limit"):
        left, limit = el.get("chars_left", 0), el["chars_limit"]
        pct = (left / limit * 100) if limit else 100
        if pct < ELEVENLABS_LOW_PCT:
            problems.append(f"💳 <b>ElevenLabs: cote pe terminate</b> — au ramas {left} caractere ({pct:.0f}%). Reincarca abonamentul.")
    elif not el.get("ok"):
        problems.append(f"🔴 ElevenLabs (vocea) nu raspunde: {el.get('error','?')}")

    # Groq transcrie podcasturile pentru seful de cabinet. Nu tine apelurile in
    # viata, deci nu e urgenta ca Twilio - dar daca tace, biblioteca de strategii
    # se opreste fara sa se vada nicaieri. Cat timp cheia nu e pusa, tacem.
    gq = p.get("groq", {})
    if gq.get("configured") and not gq.get("ok"):
        problems.append(f"🟠 <b>Groq</b> (transcrierile) nu merge: {gq.get('error','?')}. "
                        f"Biblioteca de strategii sta pe loc. Cheie noua: console.groq.com/keys")

    sb = p.get("supabase", {})
    if not sb.get("ok"):
        problems.append(f"🔴 Supabase (baza de date) nu raspunde: {sb.get('error','?')}")

    return problems


def check_code():
    """
    Verificare de COD, nu doar de "raspunde serverul": chiar exista tot ce
    trebuie ca sa poata suna? Pe 26 iulie, modulul agentului a pierdut jumatate
    din functii si fisierul a ramas cod valid - serverul raspundea vesel, dar
    orice apel ar fi crapat. Asta prinde exact asa ceva.
    Verifica si erorile de telefonie raportate de Twilio.
    """
    if not (BACKEND and HEALTH_KEY):
        return []
    try:
        code, body = http_get(f"{BACKEND}/api/health/code?key={HEALTH_KEY}", timeout=45)
        if code != 200:
            return [f"🟠 verificarea de cod raspunde {code}"]
        d = json.loads(body)
    except Exception as e:
        return [f"🟠 nu pot verifica codul: {e}"]

    ag = d.get("agent") or {}
    print(f"  cod: {'OK' if d.get('ok') else 'PROBLEME'} | "
          f"agent pornit={ag.get('pornit')} coada={ag.get('in_coada')} "
          f"apeluri={ag.get('apeluri')}")
    out = []
    for p in (d.get("probleme") or []):
        # lipsa de functii sau module care nu se incarca = rosu, restul portocaliu
        rosu = ("pierdut functia" in p or "NU se incarca" in p or "nu mai exista" in p)
        out.append(("🔴 " if rosu else "🟠 ") + p)
    for e in (d.get("erori_twilio") or [])[:3]:
        out.append(f"🔴 telefonie: eroare Twilio {e.get('cod')} — {e.get('text','')[:110]}")
    return out


def check_functii():
    """
    Apasa pe rand pe fiecare buton important si spune care nu raspunde.

    Lucian, 8 august: "aceasta este o problema mare si nu am fost anuntat...
    vreau sa ruleze peste tot si sa incerce toate functiile si sa ma anunte el
    ce nu merge".

    Pana acum verificam daca CASA e in picioare: raspunde backend-ul, mai sunt
    bani la furnizori. Dar vocea putea fi moarta de trei zile cu casa intreaga -
    si chiar a fost. Cheia ElevenLabs murise pe 5 august, iar el a aflat abia
    cand a incercat singur sa-si asculte vocea, dupa trei zile de apeluri fara
    voce. Nimeni nu i-a spus, fiindca nimeni nu apasa pe butoane.

    Aici se incearca: vocea, traducerea, telefonia, conturile si adresa de pe
    care se deschide aplicatia.
    """
    if not (BACKEND and HEALTH_KEY):
        return []
    try:
        code, body = http_get(f"{BACKEND}/api/health/functii?key={HEALTH_KEY}", timeout=60)
        if code != 200:
            return [f"🟠 nu pot incerca functiile (raspuns {code})"]
        d = json.loads(body)
    except Exception as e:
        return [f"🟠 nu pot incerca functiile: {e}"]

    merg = ", ".join(d.get("merg") or []) or "niciuna"
    print(f"  functii: {'TOATE MERG' if d.get('ok') else 'PROBLEME'} | merg: {merg}")
    return list(d.get("probleme") or [])


def try_auto_repair():
    if not DEPLOY_HOOK:
        return "\n\n(Fara auto-reparare: nu e configurat Render Deploy Hook.)"
    try:
        req = urllib.request.Request(DEPLOY_HOOK, data=b"{}",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=25)
        return "\n\n🔧 Am cerut automat un <b>redeploy</b> pe Render. Verific din nou la runda urmatoare."
    except Exception as e:
        return f"\n\n⚠️ Am incercat redeploy automat dar a esuat: {e}"


def main():
    # Buton de test (workflow_dispatch cu simulate=down/degraded): trimite o
    # alerta FALSA, clar marcata, fara sa atinga SmartBiz real, fara redeploy,
    # fara sa modifice starea. Ca userul sa vada cu ochii lui ca alarma suna.
    sim = os.environ.get("SIMULATE", "no").strip().lower()
    if sim in ("down", "degraded"):
        fake = ("🔴 backend NU raspunde" if sim == "down"
                else "🟠 un serviciu (ex. OpenAI) ar fi cazut")
        notify("🧪 <b>TEST caine de paza SmartBiz</b>\n\n"
               "Asa arata o alerta reala cand ceva pica:\n\n" + fake +
               "\n\n(Doar test — SmartBiz functioneaza normal, nu s-a repornit nimic.)")
        print("alerta de TEST trimisa:", sim)
        return

    prev = "OK"
    if os.path.exists(STATE_FILE):
        prev = (open(STATE_FILE).read().strip() or "OK")

    b_status, b_msg = check_backend()
    f_ok, f_msg = check_frontend()

    problems = []
    if b_status == "DOWN":
        problems.append("🔴 " + b_msg)
    elif b_status == "DEGRADED":
        problems.append("🟠 " + b_msg)
    if not f_ok:
        problems.append("🔴 site-ul (frontend) nu raspunde: " + f_msg)

    # Sold furnizori + verificare de cod (doar daca backend-ul e sus)
    if b_status != "DOWN":
        problems.extend(check_providers())
        problems.extend(check_code())
        # Si CHIAR functiile, nu doar daca serverul e sus.
        problems.extend(check_functii())

    if not problems:
        status = "OK"
    elif b_status == "DOWN" or not f_ok or any(p.startswith("🔴") for p in problems):
        status = "DOWN"
    else:
        status = "DEGRADED"

    print(f"prev={prev} -> status={status}")
    for p in problems:
        # ASCII-safe pt console Windows (cp1252) - emoji raman doar in alerta
        print("  ", p.encode("ascii", "ignore").decode().strip())

    if status != "OK" and prev == "OK":
        extra = try_auto_repair() if status == "DOWN" else ""
        notify("⚠️ <b>SmartBiz are o problema</b>\n\n" + "\n".join(problems) + extra)
    elif status == "OK" and prev != "OK":
        notify("✅ <b>SmartBiz functioneaza din nou</b> — totul e verde.")
    else:
        print("stare neschimbata, fara notificare")

    with open(STATE_FILE, "w") as f:
        f.write(status)


if __name__ == "__main__":
    main()
