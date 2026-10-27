# -*- coding: utf-8 -*-
"""
PAZA CONTABILILOR (27 oct 2026). Lucian: "notifica-ma pe telegram stadiul
celor 2 contabili... vreau sa ruleze fara sa tin deschis, eu plec la munca".

La fiecare rulare (din 5 in 5 minute, in orele de apel) intreaba serverul ce
au raspuns contabilii sunati azi si trimite pe Telegram ce e NOU. La DA pe
WhatsApp: un link wa.me care deschide WhatsApp-ul LUI cu mesajul lui gata
scris - trimiterea o face el, cu o apasare (nu automatizam WhatsApp-ul lui).
Nu scrie in jurnalul public nici numere, nici nume.
"""
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

BACKEND = os.environ.get("BACKEND_URL", "https://aicall-backend-1dwi.onrender.com")
STARE = "contabili-state.json"

# Mesajul final al lui Lucian (cel trimis pe 26 oct la SEP Contab), pentru
# trimitere imediata: "Buna ziua" si fara scuzele de intarziere.
MESAJ = (
    "Bună ziua, sunt directorul SmartBiz. V-a sunat asistentul nostru și ați spus că vă pot "
    "trimite detaliile.\n"
    "Pe scurt: SmartBiz este un program cu care firmele scriu românește în orice chat, iar "
    "clientul străin primește mesajul direct în limba lui. Nu mai e nevoie să copieze textul, "
    "să treacă în altă aplicație și să revină. De asemenea, în foarte scurt timp vom lansa și "
    "apelurile vocale traduse, cu vocea clonată.\n\n"
    "Pentru clienții dumneavoastră înseamnă mai multe vânzări: pot vinde în mai multe țări, "
    "pentru că le răspund cumpărătorilor în limba lor, iar un magazin care răspunde în limba "
    "clientului inspiră mai multă încredere.\n"
    "Colaborarea: dacă îl recomandați clienților dumneavoastră care vând în alte țări, primiți "
    "un procent din abonamentul fiecărui client adus, lunar, cât timp plătește. Procentul îl "
    "stabilim împreună. Vă ofer și un cont gratuit, ca să-l încercați.\n\n"
    "Puteți vedea aici cum arată: https://www.smartbiz.llc\n\n"
    "Dacă vă interesează, vă aștept cu întrebări."
)
VERDICT = {
    "da_whatsapp": "✅ A zis DA - vrea detaliile pe WhatsApp.",
    "da_email": "✅ A zis DA, dar vrea pe E-MAIL",
    "nu_a_raspuns": "📵 N-a răspuns (robotul mai încearcă o dată peste ~1 oră).",
    "altceva": "❌ N-a zis da.",
}


def telegram(text: str) -> None:
    tok, chat = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    date = urllib.parse.urlencode({"chat_id": chat, "text": text,
                                   "disable_web_page_preview": "true"}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", date, timeout=20).read()


def main():
    key = os.environ.get("HEALTH_KEY", "")
    url = f"{BACKEND}/api/health/contabili?key={urllib.parse.quote(key)}"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            lista = json.load(r).get("contabili") or []
    except Exception as e:
        print(f"serverul n-a raspuns ({type(e).__name__}) - incerc la urmatoarea rulare")
        return
    vazute = set(json.load(open(STARE))) if os.path.exists(STARE) else set()
    noi = 0
    for c in lista:
        cheie = f"{c.get('id')}|{c.get('called_at')}"
        if cheie in vazute:
            continue
        ora = (datetime.fromisoformat(str(c["called_at"]).replace("Z", "+00:00"))
               + timedelta(hours=2)).strftime("%H:%M")
        txt = (f"📞 CONTABIL sunat la {ora}: {c.get('firma')} ({c.get('oras')})\n"
               f"Tel: {c.get('telefon')}\n{VERDICT.get(c.get('verdict'), c.get('verdict'))}")
        if c.get("verdict") == "da_email":
            txt += f": {c.get('email') or '(adresa de pe site)'}\nSubiect: Colaborare SmartBiz"
        if c.get("a_spus"):
            txt += f"\nA spus: „{c['a_spus']}”"
        if c.get("verdict") == "da_whatsapp":
            nr = "".join(ch for ch in str(c.get("telefon")) if ch.isdigit())
            txt += ("\n\n👉 Apasă aici: se deschide WhatsApp cu mesajul tău gata scris, apoi Trimite:\n"
                    f"https://wa.me/{nr}?text={urllib.parse.quote(MESAJ)}")
        telegram(txt)
        vazute.add(cheie)
        noi += 1
    json.dump(sorted(vazute), open(STARE, "w"))
    print(f"contabili azi: {len(lista)}, anuntati acum: {noi}")


if __name__ == "__main__":
    main()
