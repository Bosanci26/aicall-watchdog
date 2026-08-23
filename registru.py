"""
CUTIA NEAGRA a probelor SmartBiz.

De ce exista: pana la 23 august, tot ce ramanea dupa o proba era o cifra
intr-o lista ({"lung+curat": [45, 55, 73, 66]}). Cand nota cadea nu se putea
afla NICI cand, NICI pe ce cod, NICI ce anume s-a stricat. Si mai rau: o
proba care nu a reusit sa masoare arata exact ca una care a iesit prost.

Ce face: din jurnalul brut al unei probe scoate TOT ce se poate masura si
scrie un rand intr-un registru (registru.jsonl in acest repo). Un rand =
o rulare, cu data, scenariul, conditia, nota, cele patru sub-note,
latentele, acoperirea judecatorului si AMPRENTA - adica versiunea de cod si
reglajele care au produs-o.

Ce NU face, intentionat: nu da alarme, nu propune reveniri, nu schimba
nimic. Pragurile de alarma se scriu abia cand registrul are destule randuri
cat sa stim cat sare nota intre doua rulari pe ACELASI cod - astazi nu
exista in tot istoricul nici macar o pereche de rulari pe acelasi cod, deci
orice prag ar fi o cifra scoasa din burta.
"""

import json
import re


def _cauta(jurnal: str, tipar: str, grup: int = 1, implicit=None):
    m = re.search(tipar, jurnal)
    return m.group(grup) if m else implicit


def rand_din_jurnal(jurnal_randuri: list, scenariu: str, conditie: str,
                    amprenta: dict, cand: str) -> dict:
    """
    Transforma jurnalul brut al unei probe intr-un rand de registru.

    'valid' e cheia intregului mecanism: spune daca rularea are voie sa fie
    comparata cu altele. O rulare in care judecatorul a cazut sau nu a
    acoperit toate replicile NU e o nota proasta - e o masuratoare ratata,
    si trebuie sa se vada altfel.
    """
    j = "\n".join(jurnal_randuri)

    nota = _cauta(j, r"Nota general\S*:\s*(\d+)")
    nota = int(nota) if nota else None

    # sub-notele, din randul formulei fixe
    esenta = _cauta(j, r"esenta pastrata (\d+)%")
    cifre = _cauta(j, r"cifre corecte (\d+)% din (\d+)")
    cifre_din = _cauta(j, r"cifre corecte \d+% din (\d+)")
    ajunse_pct = _cauta(j, r"replici ajunse (\d+)%")
    viteza = _cauta(j, r"viteza (\d+)% la ([\d.]+)s")
    astept = _cauta(j, r"viteza \d+% la ([\d.]+)s")

    # randul CIFRE (masuratori brute, nu note)
    ajunse = _cauta(j, r"CIFRE: (\d+)/(\d+) replici ajunse")
    total = _cauta(j, r"CIFRE: \d+/(\d+) replici ajunse")
    suprapuse = _cauta(j, r"; (\d+)/\d+ suprapuse")

    # acoperirea judecatorului (adaugata in backend pe 23 aug)
    voturi = _cauta(j, r"judecata: (\d+)/3 evaluari valide")
    acoperite = _cauta(j, r"evaluari valide, (\d+)/(\d+) replici acoperite")
    acoperite_din = _cauta(j, r"evaluari valide, \d+/(\d+) replici acoperite")

    incompleta = "MASURATOARE INCOMPLETA" in j
    intrerupt = "APEL INTRERUPT" in j or "evaluatorul a cazut" in j

    # o rulare e valida (deci comparabila) doar daca a produs o nota SI
    # judecatorul a lucrat intreg. Daca backendul e mai vechi si nu scrie
    # inca randul de acoperire, nu presupunem ca e bine: marcam 'necunoscut'.
    if nota is None or intrerupt:
        valid = False
        motiv = "fara nota" if nota is None else "apel intrerupt"
    elif voturi is None:
        valid = False
        motiv = "backend vechi, fara randul de acoperire"
    elif incompleta:
        valid = False
        motiv = "judecatorul nu a acoperit tot"
    else:
        valid = True
        motiv = ""

    return {
        "cand": cand,
        "scenariu": scenariu,
        "conditie": conditie,
        "nota": nota,
        "valid": valid,
        "motiv": motiv,
        "sub": {
            "esenta_pct": int(esenta) if esenta else None,
            "cifre_pct": int(cifre) if cifre else None,
            "cifre_din": int(cifre_din) if cifre_din else None,
            "ajunse_pct": int(ajunse_pct) if ajunse_pct else None,
            "viteza_pct": int(viteza) if viteza else None,
        },
        "masurat": {
            "ajunse": int(ajunse) if ajunse else None,
            "total": int(total) if total else None,
            "suprapuse": int(suprapuse) if suprapuse else None,
            "asteptare_mediana_s": float(astept) if astept else None,
        },
        "judecata": {
            "voturi_valide": int(voturi) if voturi else None,
            "replici_acoperite": int(acoperite) if acoperite else None,
            "replici_total": int(acoperite_din) if acoperite_din else None,
        },
        "amprenta": amprenta,
        "stricate": [r.strip() for r in jurnal_randuri
                     if r.strip().startswith('- "')][:8],
    }


def serie(randuri: list, scenariu: str, conditie: str, amprenta_cod: str = None):
    """
    Notele valide ale unei perechi scenariu+conditie, cele mai noi la urma.
    Daca se da amprenta_cod, doar rularile facute pe acel cod - asta e
    singurul fel corect de a compara doua rulari intre ele.
    """
    out = []
    for r in randuri:
        if not r.get("valid") or r.get("nota") is None:
            continue
        if r.get("scenariu") != scenariu or r.get("conditie") != conditie:
            continue
        if amprenta_cod and (r.get("amprenta") or {}).get("cod") != amprenta_cod:
            continue
        out.append(r)
    return out


def imprastiere(note: list):
    """
    Cat sare nota intre rulari - mediana, cea mai mica, cea mai mare si
    departarea medie fata de mediana. Fara asta nu se poate spune daca o
    scadere e o stricaciune sau doar zgomot, si de aceea nu scriem inca
    niciun prag de alarma.
    """
    if not note:
        return None
    s = sorted(note)
    med = s[len(s) // 2]
    return {
        "cate": len(s),
        "mediana": med,
        "minim": s[0],
        "maxim": s[-1],
        "intindere": s[-1] - s[0],
        "abatere_medie": round(sum(abs(n - med) for n in s) / len(s), 2),
    }


if __name__ == "__main__":
    # proba pe un jurnal facut de mana, ca sa se vada ca citirea e corecta
    exemplu = [
        "proba automata: transport + galagie",
        "CIFRE: 4/6 replici ajunse; 0/4 suprapuse (vocea porneste inainte de final); "
        "asteptarea dupa tacere mediana 2.52s",
        "0) judecata: 3/3 evaluari valide, 6/6 replici acoperite",
        "1) Nota generală: 39/100 — formula fixa (aceleasi fapte = aceeasi cifra): "
        "esenta pastrata 25% (pondere 40), cifre corecte 33% din 3 replici cu numere (25), "
        "replici ajunse 67% (15), viteza 53% la 2.52s asteptare mediana (20)",
        '- "am nevoie de 15 tone pana vineri..." -> cifra auzita gresit',
        "— gata —",
    ]
    r = rand_din_jurnal(exemplu, "transport", "galagie",
                        {"cod": "ga-realtime-v45"}, "2026-08-23T06:00:00Z")
    assert r["nota"] == 39, r
    assert r["valid"] is True, r
    assert r["sub"]["esenta_pct"] == 25 and r["sub"]["viteza_pct"] == 53, r
    assert r["masurat"]["ajunse"] == 4 and r["masurat"]["total"] == 6, r
    assert r["masurat"]["asteptare_mediana_s"] == 2.52, r
    assert r["judecata"]["replici_acoperite"] == 6, r
    assert len(r["stricate"]) == 1, r

    # aceeasi proba, dar cu judecatorul incomplet: nota exista, dar NU se compara
    ciuntit = list(exemplu)
    ciuntit[2] = ("0) judecata: 2/3 evaluari valide, 4/6 replici acoperite"
                  "  <-- MASURATOARE INCOMPLETA, nota NU se compara")
    r2 = rand_din_jurnal(ciuntit, "transport", "galagie", {"cod": "x"}, "acum")
    assert r2["valid"] is False and r2["nota"] == 39, r2

    # backend vechi, fara randul de acoperire -> nu presupunem ca e bine
    vechi = [l for l in exemplu if not l.startswith("0) judecata")]
    r3 = rand_din_jurnal(vechi, "lung", "curat", {"cod": "x"}, "acum")
    assert r3["valid"] is False and "backend vechi" in r3["motiv"], r3

    assert imprastiere([73, 66, 70]) == {
        "cate": 3, "mediana": 70, "minim": 66, "maxim": 73,
        "intindere": 7, "abatere_medie": 2.33}

    print("registru: toate probele trec")
