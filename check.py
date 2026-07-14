#!/usr/bin/env python3
"""
Caine de paza AiCall: verifica backend + frontend, anunta pe Telegram cand
ceva pica sau isi revine, si (optional) cere automat un redeploy pe Render.

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
STATE_FILE = "watchdog-state.txt"

# Serviciile fara de care AiCall nu poate traduce un apel
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
    # alerta FALSA, clar marcata, fara sa atinga AiCall real, fara redeploy,
    # fara sa modifice starea. Ca userul sa vada cu ochii lui ca alarma suna.
    sim = os.environ.get("SIMULATE", "no").strip().lower()
    if sim in ("down", "degraded"):
        fake = ("🔴 backend NU raspunde" if sim == "down"
                else "🟠 un serviciu (ex. OpenAI) ar fi cazut")
        notify("🧪 <b>TEST caine de paza AiCall</b>\n\n"
               "Asa arata o alerta reala cand ceva pica:\n\n" + fake +
               "\n\n(Doar test — AiCall functioneaza normal, nu s-a repornit nimic.)")
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

    if not problems:
        status = "OK"
    elif b_status == "DOWN" or not f_ok:
        status = "DOWN"
    else:
        status = "DEGRADED"

    print(f"prev={prev} -> status={status}")
    for p in problems:
        # ASCII-safe pt console Windows (cp1252) - emoji raman doar in alerta
        print("  ", p.encode("ascii", "ignore").decode().strip())

    if status != "OK" and prev == "OK":
        extra = try_auto_repair() if status == "DOWN" else ""
        notify("⚠️ <b>AiCall are o problema</b>\n\n" + "\n".join(problems) + extra)
    elif status == "OK" and prev != "OK":
        notify("✅ <b>AiCall functioneaza din nou</b> — totul e verde.")
    else:
        print("stare neschimbata, fara notificare")

    with open(STATE_FILE, "w") as f:
        f.write(status)


if __name__ == "__main__":
    main()
