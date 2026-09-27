#!/usr/bin/env python3
"""
PROBA PAGINILOR SI A RUTELOR PUBLICE SmartBiz.

De ce exista, scris pe 27 septembrie 2026, in ajunul lansarii: paginile si
rutele n-aveau nicio proba. Pe site, o adresa gresita NU da 404 - Vercel
trimite orice cale necunoscuta la aplicatie (index.html), cu cod 200. Deci
"raspunde 200" nu spune nimic: o pagina stearsa din build arata la fel de
verde ca una buna. De-aia fiecare pagina se recunoaste dupa un TEXT al ei,
nu dupa cod.

Ce verifica (numai cereri GET, nimic nu se schimba pe productie):
  1. fiecare pagina publica: cod asteptat + textul ei + ca NU e aplicatia
     trimisa in locul ei;
  2. pe paginile publice nu se vorbeste de apel sau de Twilio (apelul tradus
     e oprit pentru clienti, Twilio a fost scos de tot);
  3. adresele vechi duc unde trebuie (301/302);
  4. extensia de pe site = versiunea din ~/aicall-yc/manifest.json;
  5. APK-ul de Android exista si e chiar un APK;
  6. sitemap-ul: fiecare adresa din el raspunde;
  7. serverul: "/" raspunde cu email pornit, /api/text/stare, pretul de 29 EUR.

Pe calculatorul lui Lucian mai verifica si ca lista de aici se potriveste cu
vercel.json, vite.config.js si *.html din ~/aicall (o pagina noua nepusa aici
iese la iveala). Pe GitHub (depozitul asta e PUBLIC) acele fisiere nu exista
si partea aia se sare - fara nicio cheie, fara nimic secret.

Folosire:
    python3 proba_paginilor.py                  # productia
    python3 proba_paginilor.py --site URL       # alt site (ex. o proba stricata)
Iese cu 1 cand ceva e stricat, 0 cand e bine.
Din alt script: `from proba_paginilor import ruleaza` -> {"rau","destiut","bine"}.
"""
import argparse
import html as _html
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
import zipfile

SITE = "https://www.smartbiz.llc"
BACKEND = "https://aicall-backend-1dwi.onrender.com"
ACASA = os.path.expanduser("~")
DEPOZIT_SITE = os.path.join(ACASA, "aicall")
MANIFEST_EXTENSIE = os.path.join(ACASA, "aicall-yc", "manifest.json")

# Paginile publice: cale -> texte care TREBUIE sa fie pe ea (in HTML-ul brut).
# Textul e ales sa NU fie si in aplicatie (index.html), ca o pagina pierduta
# din build - pe care Vercel o inlocuieste tacut cu aplicatia - sa pice.
PAGINI = {
    "/":                   ["29 €/lună", 'id="app"'],
    "/welcome":            ["<b>29 €</b>", "Partenerul citește"],
    "/traducere-whatsapp": ["Traducere mesaje WhatsApp"],
    "/termeni":            ["Termeni și condiții", "29 EUR"],
    "/firma":              ["AMATEQ S.R.L.", "Date de identificare"],
    "/confidentialitate":  ["<h1>Confidențialitate"],
    "/extensie":           ['href="/smartbiz-extensie.zip"', "Chrome"],
    "/android":            ['.apk"'],
    "/ajutor":             ["Scrie-ne ce nu merge"],
    "/incearca":           ["Primele 10 mesaje sunt gratuite"],
    "/i":                  ["Primele 10 mesaje sunt gratuite"],
}

# Adresele care duc in alta parte: cale -> (cod, unde ajunge).
REDIRECTARI = {
    "/join":                          (302, "/"),
    "/join.html":                     (302, "/"),
    "/traducere-apel-telefonic":      (301, "/"),
    "/traducere-apel-telefonic.html": (301, "/"),
}

# Fisiere publice care trebuie sa existe: cale -> text obligatoriu.
FISIERE = {
    "/robots.txt":    "Sitemap:",
    "/sitemap.xml":   "<urlset",
    "/manifest.json": '"name"',
}

# Nimic despre apel sau Twilio in ce vede omul (text + meta descrieri).
INTERZIS = re.compile(
    r"\btwilio\b|\bapel\w*|\bconvorbir\w*|\btelefonic\w*|"
    r"\bcalls?\b|\bvoce(?:a)? clonat\w*",
    re.IGNORECASE)

# vercel.json are si rute care nu-s pagini (API, imagini, coduri de proba).
NU_SUNT_PAGINI = re.compile(r"^/(api/|healthz$|:cod|\(\.\*\)|.*\.apk$)")


def cere(url, timeout=60, urmeaza=True):
    """GET; intoarce (cod, antete, corp). Cu urmeaza=False nu merge dupa
    redirectari, ca sa putem citi codul si tinta lor."""
    class _Stai(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    deschizator = (urllib.request.build_opener() if urmeaza
                   else urllib.request.build_opener(_Stai))
    req = urllib.request.Request(url, headers={"User-Agent": "smartbiz-proba-paginilor"})
    try:
        with deschizator.open(req, timeout=timeout) as r:
            return r.getcode(), dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read() or b""


def text_vazut(pagina):
    """Ce citeste un om (si Google): textul fara scripturi si stiluri, plus
    titlul si descrierile din meta."""
    meta = " ".join(re.findall(
        r'<meta[^>]+(?:name|property)="(?:description|og:[^"]*|twitter:[^"]*)"'
        r'[^>]*content="([^"]*)"', pagina, re.I))
    corp = re.sub(r"<(script|style)\b.*?</\1>", " ", pagina, flags=re.S | re.I)
    corp = re.sub(r"<!--.*?-->", " ", corp, flags=re.S)
    corp = re.sub(r"<[^>]+>", " ", corp)
    return _html.unescape(meta + " " + corp)


def _versiune(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def ruleaza(site=SITE, backend=BACKEND):
    site, backend = site.rstrip("/"), backend.rstrip("/")
    rau, destiut, bine = [], [], []

    # --- 1+2. PAGINILE ---------------------------------------------------
    aplicatia = None
    try:
        cod, _, corp = cere(site + "/")
        if cod == 200:
            aplicatia = corp
    except Exception:
        pass

    for cale, texte in PAGINI.items():
        try:
            cod, _, corp = cere(site + cale)
        except Exception as e:
            rau.append(f"{cale}: nu raspunde ({str(e)[:80]})")
            continue
        if cod != 200:
            rau.append(f"{cale}: cod {cod} in loc de 200")
            continue
        pagina = corp.decode("utf-8", "replace")
        if cale != "/" and aplicatia is not None and corp == aplicatia:
            rau.append(f"{cale}: arata APLICATIA in locul paginii "
                       f"(pagina lipseste din build sau din vercel.json)")
            continue
        lipsa = [t for t in texte if t not in pagina]
        if lipsa:
            rau.append(f"{cale}: lipseste textul {lipsa[0]!r}")
            continue
        gasit = INTERZIS.search(text_vazut(pagina))
        if gasit:
            vazut = text_vazut(pagina)
            fragment = re.sub(r"\s+", " ", vazut[max(0, gasit.start() - 40):gasit.end() + 40])
            rau.append(f"{cale}: vorbeste de apel/Twilio: \"...{fragment.strip()}...\"")
            continue
        bine.append(f"{cale}: 200, textul e acolo, nimic despre apel")

    # --- 3. ADRESELE VECHI -----------------------------------------------
    for cale, (cod_asteptat, tinta) in REDIRECTARI.items():
        try:
            cod, antete, _ = cere(site + cale, urmeaza=False)
            loc = antete.get("Location") or antete.get("location") or ""
            loc_cale = re.sub(r"^https?://[^/]+", "", loc) or "/"
            if cod != cod_asteptat or loc_cale != tinta:
                rau.append(f"{cale}: {cod} -> {loc or '(nicaieri)'}, "
                           f"asteptam {cod_asteptat} -> {tinta}")
            else:
                bine.append(f"{cale}: {cod} -> {tinta}")
        except Exception as e:
            rau.append(f"{cale}: nu raspunde ({str(e)[:80]})")

    for cale, text in FISIERE.items():
        try:
            cod, _, corp = cere(site + cale)
            if cod != 200 or text not in corp.decode("utf-8", "replace"):
                rau.append(f"{cale}: cod {cod} sau fara {text!r}")
            else:
                bine.append(f"{cale}: 200")
        except Exception as e:
            rau.append(f"{cale}: nu raspunde ({str(e)[:80]})")

    # --- 4. EXTENSIA -----------------------------------------------------
    try:
        cod, _, corp = cere(site + "/smartbiz-extensie.zip")
        if cod != 200:
            rau.append(f"extensia: /smartbiz-extensie.zip da {cod}")
        else:
            with zipfile.ZipFile(io.BytesIO(corp)) as z:
                nume = [n for n in z.namelist() if n.rsplit("/", 1)[-1] == "manifest.json"]
                if not nume:
                    raise ValueError("pachetul n-are manifest.json")
                pe_site = json.loads(z.read(sorted(nume, key=len)[0])).get("version")
            asteptata = os.environ.get("EXTENSIE_VERSIUNE", "").strip()
            sursa = "EXTENSIE_VERSIUNE"
            if not asteptata and os.path.exists(MANIFEST_EXTENSIE):
                asteptata = json.load(open(MANIFEST_EXTENSIE, encoding="utf-8")).get("version", "")
                sursa = "~/aicall-yc/manifest.json"
            if not asteptata:
                bine.append(f"extensia pe site: {pe_site} (pe GitHub nu am cu ce s-o compar)")
            elif _versiune(pe_site) != _versiune(asteptata):
                rau.append(f"extensia de pe site e {pe_site}, dar {sursa} e {asteptata} "
                           f"- clientii descarca alta versiune decat cea lucrata")
            else:
                bine.append(f"extensia pe site = {pe_site}, la fel ca {sursa}")
    except Exception as e:
        rau.append(f"extensia: pachetul de pe site nu se deschide ({str(e)[:80]})")

    # --- 5. APK-ul -------------------------------------------------------
    try:
        _, _, corp = cere(site + "/android")
        legaturi = re.findall(r'href="([^"]+\.apk)"', corp.decode("utf-8", "replace"))
        for apk in sorted(set(legaturi)) or ["/smartbiz-android.apk"]:
            url = apk if apk.startswith("http") else site + apk
            cod, antete, corp = cere(url, timeout=120)
            tip = antete.get("Content-Type") or antete.get("content-type") or ""
            if cod != 200 or not corp.startswith(b"PK") or len(corp) < 1_000_000:
                rau.append(f"APK {apk}: cod {cod}, {len(corp)} octeti, {tip or 'fara tip'} "
                           f"- nu e un APK intreg")
            else:
                bine.append(f"APK {apk}: {len(corp) / 1e6:.1f} MB, {tip}")
    except Exception as e:
        rau.append(f"APK: nu pot verifica ({str(e)[:80]})")

    # --- 6. SITEMAP ------------------------------------------------------
    try:
        _, _, corp = cere(site + "/sitemap.xml")
        for loc in re.findall(r"<loc>([^<]+)</loc>", corp.decode("utf-8", "replace")):
            cale = re.sub(r"^https?://[^/]+", "", loc.strip()) or "/"
            if cale not in PAGINI:
                cod, _, _ = cere(site + cale)
                rau.append(f"sitemap: {cale} nu e in lista probei (cod {cod}) - adaug-o in PAGINI")
    except Exception as e:
        rau.append(f"sitemap: nu-l pot citi ({str(e)[:80]})")

    # --- 7. SERVERUL -----------------------------------------------------
    try:
        cod, _, corp = cere(backend + "/", timeout=90)
        j = json.loads(corp) if cod == 200 else {}
        if cod != 200:
            rau.append(f"serverul /: cod {cod}")
        else:
            cazute = [k for k in ("supabase", "openai") if not j.get(k)]
            if cazute:
                rau.append(f"serverul /: cazute {', '.join(cazute)}")
            if j.get("email") is False:
                rau.append("serverul /: email=false - nu pleaca emailurile (intrarea in cont, chitantele)")
            vechi = sorted(k for k in j if "twilio" in k.lower())
            if vechi:
                destiut.append(f"serverul / inca raporteaza {', '.join(vechi)} "
                               f"(dispare cand ajunge pe Render stergerea Twilio)")
            if not cazute and j.get("email") is not False:
                bine.append(f"serverul /: pornit ({j.get('code_marker', '?')}), email pornit")
    except Exception as e:
        rau.append(f"serverul /: nu raspunde ({str(e)[:80]})")

    for baza, eticheta in ((backend, "serverul"), (site, "prin site")):
        try:
            cod, _, corp = cere(baza + "/api/text/stare", timeout=90)
            json.loads(corp)
            if cod != 200:
                rau.append(f"{eticheta} /api/text/stare: cod {cod}")
            else:
                bine.append(f"{eticheta} /api/text/stare: 200")
        except Exception as e:
            rau.append(f"{eticheta} /api/text/stare: nu raspunde ({str(e)[:80]})")

    try:
        cod, _, corp = cere(backend + "/api/billing/plans", timeout=90)
        planuri = (json.loads(corp) or {}).get("plans") or [] if cod == 200 else []
        if not any(p.get("price_eur") == 29 for p in planuri):
            rau.append(f"/api/billing/plans: niciun plan de 29 EUR (cod {cod})")
        else:
            bine.append("/api/billing/plans: planul de 29 EUR e acolo")
        for p in planuri:
            if INTERZIS.search(json.dumps(p, ensure_ascii=False)):
                rau.append(f"/api/billing/plans: planul {p.get('name')!r} vinde apel")
    except Exception as e:
        rau.append(f"/api/billing/plans: nu raspunde ({str(e)[:80]})")

    # --- Pe calculatorul lui Lucian: lista de aici e completa? -------------
    if os.path.isdir(DEPOZIT_SITE):
        acoperite = set(PAGINI) | set(REDIRECTARI) | set(FISIERE)
        try:
            vj = json.load(open(os.path.join(DEPOZIT_SITE, "vercel.json"), encoding="utf-8"))
            for r in (vj.get("rewrites") or []) + (vj.get("redirects") or []):
                src = r.get("source", "")
                if not NU_SUNT_PAGINI.match(src) and src not in acoperite:
                    rau.append(f"vercel.json are {src}, dar proba nu o cere - adaug-o in PAGINI")
        except Exception as e:
            destiut.append(f"(nu pot citi vercel.json: {str(e)[:60]})")
        try:
            vite = open(os.path.join(DEPOZIT_SITE, "vite.config.js"), encoding="utf-8").read()
            construite = {
                m for linie in vite.splitlines() if not linie.strip().startswith("//")
                for m in re.findall(r"resolve\(__dirname, '([^']+\.html)'\)", linie)}
            servite = {"index.html": "/", "landing.html": "/welcome"}
            for f in sorted(construite):
                cale = servite.get(f, "/" + f[:-5])
                if cale not in acoperite:
                    rau.append(f"vite.config.js construieste {f}, dar proba nu cere {cale}")
            for f in sorted(os.listdir(DEPOZIT_SITE)):
                if f.endswith(".html") and f not in construite:
                    cale = "/" + f[:-5]
                    if cale not in acoperite:
                        destiut.append(f"{f} sta in depozit, dar nu se construieste (nu e pe site)")
        except Exception as e:
            destiut.append(f"(nu pot citi vite.config.js: {str(e)[:60]})")

    return {"rau": rau, "destiut": destiut, "bine": bine}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=SITE)
    ap.add_argument("--backend", default=BACKEND)
    a = ap.parse_args()
    r = ruleaza(a.site, a.backend)
    for b in r["bine"]:
        print("  ok   " + b)
    for d in r["destiut"]:
        print("  stiu " + d)
    for x in r["rau"]:
        print("  RAU  " + x)
    print(f"\n{len(r['bine'])} bune, {len(r['rau'])} stricate, {len(r['destiut'])} de stiut")
    sys.exit(1 if r["rau"] else 0)


if __name__ == "__main__":
    main()
