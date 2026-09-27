#!/usr/bin/env python3
"""
Asistent de paza SmartBiz: verifica backend + frontend + SOLDUL la furnizori +
CODUL (agentul de vanzari si traducerea au toate functiile? rutele exista?
Twilio raporteaza erori de telefonie?), anunta pe Telegram cand ceva pica sau
isi revine, si (optional) cere automat un redeploy pe Render.

Fara dependinte (doar stdlib). Ruleaza pe GitHub Actions din 5 in 5 minute.
Tine minte LISTA problemelor (nu un singur cuvant de stare) si anunta doar ce
s-a SCHIMBAT: o problema noua (chiar daca alta, veche, e tot acolo) sau una
rezolvata. Starea e pastrata intre rulari prin cache-ul GitHub Actions.
"""
import html
import json
import os
import re
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

# Proba de traducere adevarata (/api/health/produs): 7 traduceri gpt-4o-mini
# pe chemare, deci NU la fiecare bataie. O data pe ora cand iese curat; cand
# iese prost, din nou peste 15 minute (ca sa vedem repede si ca s-a vindecat).
PRODUS_LA_MIN = float(os.environ.get("PRODUS_LA_MIN", "60"))
PRODUS_RAU_LA_MIN = float(os.environ.get("PRODUS_RAU_LA_MIN", "15"))
# O problema disparuta se anunta "rezolvata" abia dupa atatea runde la rand
# fara ea - altfel o sughitatura de o runda da doua mesaje (a picat / a revenit).
RUNDE_PANA_LA_REZOLVAT = int(os.environ.get("RUNDE_PANA_LA_REZOLVAT", "2"))

# Praguri "bani putini" - anunta INAINTE sa ramai fara
TWILIO_LOW_USD = float(os.environ.get("TWILIO_LOW_USD", "5"))       # ~100 min RO
ELEVENLABS_LOW_PCT = float(os.environ.get("ELEVENLABS_LOW_PCT", "10"))  # sub 10% ramas
FISH_LOW_USD = float(os.environ.get("FISH_LOW_USD", "5"))

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
    # Un '<' ramas in textul venit de la server (o eroare, o traducere) face
    # Telegram sa refuze TOT mesajul in modul HTML. A doua incercare pleaca
    # fara formatare - o alerta urata e mai buna decat una pierduta.
    for mod in ("HTML", None):
        try:
            camp = {"chat_id": TG_CHAT, "disable_web_page_preview": "true",
                    "text": text if mod else re.sub(r"</?b>", "", text)}
            if mod:
                camp["parse_mode"] = mod
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                data=urllib.parse.urlencode(camp).encode())
            urllib.request.urlopen(req, timeout=20)
            print("Telegram trimis.")
            return
        except Exception as e:
            print(f"Telegram a esuat ({mod or 'text simplu'}):", e)


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
                # Fara email nu intra nimeni in cont (linkul de intrare) si nu
                # pleaca chitantele. Doar cand serverul spune EXPLICIT false:
                # un server vechi fara camp nu e o problema.
                if j.get("email") is False:
                    bad.append("email (nu pleaca linkurile de intrare si chitantele)")
                if bad:
                    return "DEGRADED", f"backend pornit ({marker}) dar servicii cazute: {', '.join(bad)}"
                return "OK", f"backend OK ({marker})"
            last_err = f"HTTP {code}"
        except Exception as e:
            last_err = str(e)
        time.sleep(20)
    return "DOWN", f"backend NU raspunde — {last_err}"


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
        problems.append(f"🔴 Twilio nu raspunde — {tw.get('error','?')}")

    oa = p.get("openai", {})
    if not oa.get("ok"):
        problems.append(f"🔴 <b>OpenAI</b> (traducerea) nu merge — {oa.get('error','?')}")

    el = p.get("elevenlabs", {})
    if el.get("ok") and el.get("chars_limit"):
        left, limit = el.get("chars_left", 0), el["chars_limit"]
        pct = (left / limit * 100) if limit else 100
        if pct < ELEVENLABS_LOW_PCT:
            problems.append(f"💳 <b>ElevenLabs: cote pe terminate</b> — au ramas {left} caractere ({pct:.0f}%). Reincarca abonamentul.")
    elif not el.get("ok"):
        problems.append(f"🔴 ElevenLabs (vocea) nu raspunde — {el.get('error','?')}")

    # Fish Audio (vocea ieftina). Pe 27 sep serverul NU il raporteaza inca in
    # /api/health/providers; de-aia tacem cat timp lipseste campul "fish", iar
    # cand apare citim creditul din oricare nume ii da serverul.
    fs = p.get("fish")
    if isinstance(fs, dict):
        credit = next((fs[k] for k in ("balance_usd", "credit_usd", "credit", "balance")
                       if isinstance(fs.get(k), (int, float))), None)
        if fs.get("ok") and credit is not None and credit < FISH_LOW_USD:
            problems.append(f"💳 <b>Fish: credit putin</b> — au ramas ${credit:.2f} (sub ${FISH_LOW_USD:.0f}). Pune bani la fish.audio.")
        elif fs.get("ok") is False:
            problems.append(f"🟠 Fish (vocea) nu raspunde — {fs.get('error','?')}")

    # Groq transcrie podcasturile pentru seful de cabinet. Nu tine apelurile in
    # viata, deci nu e urgenta ca Twilio - dar daca tace, biblioteca de strategii
    # se opreste fara sa se vada nicaieri. Cat timp cheia nu e pusa, tacem.
    gq = p.get("groq", {})
    if gq.get("configured") and not gq.get("ok"):
        problems.append(f"🟠 <b>Groq</b> (transcrierile) nu merge — {gq.get('error','?')}. "
                        f"Biblioteca de strategii sta pe loc. Cheie noua: console.groq.com/keys")

    sb = p.get("supabase", {})
    if not sb.get("ok"):
        problems.append(f"🔴 Supabase (baza de date) nu raspunde — {sb.get('error','?')}")

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
            return [f"🟠 verificarea de cod nu merge — raspuns {code}"]
        d = json.loads(body)
    except Exception as e:
        return [f"🟠 nu pot verifica codul — {e}"]

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
            return [f"🟠 nu pot incerca functiile — raspuns {code}"]
        d = json.loads(body)
    except Exception as e:
        return [f"🟠 nu pot incerca functiile — {e}"]

    merg = ", ".join(d.get("merg") or []) or "niciuna"
    print(f"  functii: {'TOATE MERG' if d.get('ok') else 'PROBLEME'} | merg: {merg}")
    return list(d.get("probleme") or [])


def check_produs(stare, acum=None):
    """
    CHIAR IESE TRADUCEREA? Nu "raspunde OpenAI" (asta o face deja
    /api/health/functii cu un "Say OK"), ci o traducere adevarata, cu cifre,
    ore si numere de camion care trebuie sa treaca neatinse, verificata pe
    server de /api/health/produs (aceeasi proba ca bateria de la 07:41).

    Fara ea, daca traducerea incepe sa iasa prost dupa 07:41, afla un client
    inaintea lui Lucian. Costa 7 traduceri gpt-4o-mini, deci se cheama rar
    (vezi PRODUS_LA_MIN) si rezultatul se tine minte intre runde in `stare`.

    Un model de limba mai scapa uneori cate o proba. Ca sa nu tipam degeaba,
    cand iese prost se mai cere o data pe loc; alarma suna doar daca pica
    AMANDOUA.
    """
    if not (BACKEND and HEALTH_KEY):
        return []
    acum = time.time() if acum is None else acum
    ultim = stare.get("produs") or {}
    pauza = PRODUS_LA_MIN if not ultim.get("probleme") else PRODUS_RAU_LA_MIN
    if ultim and acum - float(ultim.get("cand", 0)) < pauza * 60:
        return list(ultim.get("probleme") or [])

    def o_proba():
        code, body = http_get(
            f"{BACKEND}/api/health/produs?key={urllib.parse.quote(HEALTH_KEY)}",
            timeout=100)
        if code != 200:
            raise RuntimeError(f"raspuns {code}")
        return json.loads(body)

    probleme = []
    try:
        d = o_proba()
        if not d.get("ok"):
            d = o_proba()   # a doua parere, inainte de alarma
        if d.get("ok"):
            print(f"  traducere: curata ({d.get('mesaje_probate')} mesaje, "
                  f"{d.get('cifre_verificate')} cifre)")
        else:
            nec = [str(n) for n in (d.get("necazuri") or [])]
            primul = html.escape(nec[0][:140], quote=False) if nec else "fara detalii"
            probleme.append(
                f"🔴 <b>Traducerea iese GRESIT</b> (proba pe server, picata de 2 ori la rand) — "
                f"{len(nec)} necazuri, primul: {primul}")
    except Exception as e:
        probleme.append(f"🟠 nu pot proba traducerea — {html.escape(str(e)[:100], quote=False)}")

    stare["produs"] = {"cand": acum, "probleme": probleme}
    return probleme


def cheie(problema):
    """
    Numele STABIL al unei probleme, ca s-o recunoastem de la o runda la alta.
    Cifrele (sold, procente, coduri de raspuns) se schimba de la o runda la
    alta fara sa fie o problema noua; la fel detaliile de dupa ' — '.
    """
    t = re.sub(r"<[^>]+>", "", problema)
    t = t.split(" — ")[0]
    t = re.sub(r"\d+([.,]\d+)?", "#", t)
    t = re.sub(r"^[^\w]+", "", t)   # emoji / semne de la inceput
    return re.sub(r"\s+", " ", t).strip().lower()


def citeste_starea():
    """Starea veche era un singur cuvant (OK/DEGRADED/DOWN); cea noua e JSON
    cu lista de probleme. Le citim pe amandoua, ca prima rulare dupa
    schimbare sa nu se piarda."""
    if not os.path.exists(STATE_FILE):
        return {"status": "OK", "probleme": {}}
    brut = open(STATE_FILE).read().strip()
    try:
        s = json.loads(brut)
        if isinstance(s, dict):
            s.setdefault("status", "OK")
            s.setdefault("probleme", {})
            return s
    except Exception:
        pass
    return {"status": brut or "OK", "probleme": {}}


def compara(vechi, problems, backend_jos):
    """
    Pune lista de azi langa cea de data trecuta.
    Intoarce (noi, rezolvate, ramase_de_dinainte, probleme_de_tinut_minte).

    Cand backend-ul e jos, verificarile de sold/cod/functii nici nu ruleaza -
    lipsa lor NU inseamna ca s-au rezolvat, deci atunci nu numaram lipsa.
    """
    azi = {}
    for p in problems:
        azi.setdefault(cheie(p), p)
    noi = [azi[k] for k in azi if k not in vechi]
    ramase = [azi[k] for k in azi if k in vechi]
    rezolvate = []
    tinute = {k: {"text": v, "lipsa": 0} for k, v in azi.items()}
    for k, v in vechi.items():
        if k in azi:
            continue
        lipsa = int(v.get("lipsa", 0)) + (0 if backend_jos else 1)
        if lipsa >= RUNDE_PANA_LA_REZOLVAT:
            rezolvate.append(v.get("text", k))
        else:
            tinute[k] = {"text": v.get("text", k), "lipsa": lipsa}
    return noi, rezolvate, ramase, tinute


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

    stare = citeste_starea()
    prev = stare.get("status", "OK")

    b_status, b_msg = check_backend()
    f_ok, f_msg = check_frontend()

    problems = []
    if b_status == "DOWN":
        problems.append("🔴 " + b_msg)
    elif b_status == "DEGRADED":
        problems.append("🟠 " + b_msg)
    if not f_ok:
        problems.append("🔴 site-ul (frontend) nu raspunde — " + f_msg)

    # Sold furnizori + verificare de cod (doar daca backend-ul e sus)
    if b_status != "DOWN":
        problems.extend(check_providers())
        problems.extend(check_code())
        # Si CHIAR functiile, nu doar daca serverul e sus.
        problems.extend(check_functii())
        # Si CHIAR traducerea, cu un mesaj adevarat (rar - costa bani).
        problems.extend(check_produs(stare))

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

    noi, rezolvate, ramase, tinute = compara(
        stare.get("probleme") or {}, problems, b_status == "DOWN")

    # Redeploy doar cand serverul abia a cazut. Inainte se cerea doar la
    # trecerea OK->DOWN - si cu Twilio rosu de saptamani, un server cazut
    # n-ar mai fi primit niciodata redeploy.
    backend_abia_cazut = b_status == "DOWN" and any(p == "🔴 " + b_msg for p in noi)
    extra = try_auto_repair() if backend_abia_cazut else ""

    if not problems and (rezolvate or prev != "OK") and not tinute:
        notify("✅ <b>SmartBiz functioneaza din nou</b> — totul e verde.")
    elif noi or rezolvate:
        if noi and not ramase and prev == "OK":
            text = "⚠️ <b>SmartBiz are o problema</b>\n\n" + "\n".join(noi)
        elif noi:
            text = "⚠️ <b>Problema NOUA la SmartBiz</b>\n\n" + "\n".join(noi)
        else:
            text = "✅ <b>S-a rezolvat ceva la SmartBiz</b>"
        if noi and rezolvate:
            text += "\n\n✅ <b>S-a rezolvat:</b>\n" + "\n".join(rezolvate)
        elif rezolvate:
            text += "\n\n" + "\n".join(rezolvate)
        if ramase:
            text += ("\n\n<i>Ramase de dinainte (deja anuntate):</i>\n"
                     + "\n".join(ramase))
        notify(text + extra)
    else:
        print("nimic nou, fara notificare")

    stare["status"] = status
    stare["probleme"] = tinute
    with open(STATE_FILE, "w") as f:
        json.dump(stare, f, ensure_ascii=False)

if __name__ == "__main__":
    main()
