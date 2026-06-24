#!/usr/bin/env python3
"""Parser del informe regional ALADDA (AMDA) -> data/aladda_top10.csv + aladda_country_totals.csv

ALADDA publica un PDF mensual con 14 paises: totales por pais + Top 10 de marcas por pais.
- Indice (para descubrir el ultimo): https://www.amda.mx/category/aladda-2/
- PDF: https://www.amda.mx/wp-content/uploads/aladda_regional_{mmm}{yy}.pdf  (ej. aladda_regional_may26.pdf)

Uso:
    python scripts/parse_aladda.py                 # descubre y procesa el ultimo informe
    python scripts/parse_aladda.py --mes 2026-05   # mes puntual
    python scripts/parse_aladda.py --pdf ruta.pdf  # desde un PDF local
    python scripts/parse_aladda.py --selftest      # valida la logica de parseo (sin red)

parse_text() es puro y testeable. La descarga usa requests (corre en la notebook,
no en el sandbox de Cowork que tiene la red bloqueada).
"""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import pathlib
import re
import sys
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

INDICE = "https://www.amda.mx/category/aladda-2/"
PDF_TPL = "https://www.amda.mx/wp-content/uploads/aladda_regional_{mmm}{yy}.pdf"
MMM = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

# Normalizacion de nombres de pais (claves ASCII; el texto del PDF se des-acentua antes de buscar)
NORM = {
    "BRASIL": "Brasil", "MEXICO": "Mexico", "ARGENTINA": "Argentina",
    "CHILE": "Chile", "COLOMBIA": "Colombia", "PERU": "Peru", "ECUADOR": "Ecuador",
    "URUGUAY": "Uruguay", "COSTA RICA": "Costa Rica", "GUATEMALA": "Guatemala",
    "PANAMA": "Panama", "PARAGUAY": "Paraguay", "VENEZUELA": "Venezuela",
    "REPUBLICA DOMINICANA": "Rep. Dominicana", "BOLIVIA": "Bolivia",
}
EN_ALCANCE = {"Chile", "Colombia", "Peru", "Ecuador", "Costa Rica", "Guatemala",
              "Panama", "Rep. Dominicana"}

# Marcas de origen chino (eje del radar de emergentes). Mantener sincronizado con sources.py.
MARCAS_CHINAS = {
    "GWM", "GREAT WALL", "CHANGAN", "JETOUR", "GEELY", "CHERY", "BYD", "JAC",
    "DONGFENG", "SINOTRUK", "FOTON", "MG", "HAVAL", "OMODA", "JAECOO", "MAXUS",
    "DFSK", "DFM", "KYC", "SHINERAY", "BAIC", "KAIYI", "NETA", "ZEEKR", "LEAPMOTOR",
}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _u(tok: str) -> int:
    return int(tok.replace(".", "").replace(",", ""))


def _is_unit(tok: str) -> bool:
    return bool(re.fullmatch(r"\d{1,3}(?:\.\d{3})*|\d+", tok)) and "," not in tok


def _norm_country(name):
    n = _strip_accents(name).strip().rstrip("*").strip().upper()
    return NORM.get(n)


def parse_text(text: str) -> dict:
    """Devuelve {'totals': {pais: {...}}, 'top10': [filas]}.

    1) Tabla regional de totales -> por pais + indice acum26 -> pais.
    2) Cada tabla de marcas termina en 'Total'; su acum26 (ultima unidad) identifica el pais.
       Las filas de marca se anclan por la derecha: se saca el ranking (ultimo token entero)
       y luego acum26 = ultima unidad, acum25 = anteultima.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    totals = {}
    acum26_to_country = {}
    tot_re = re.compile(
        r"^(\D+?)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+"
        r"(-?[\d.,]+)\s*%.*?(-?[\d.,]+)\s*%")
    for ln in lines:
        m = tot_re.match(ln)
        if not m:
            continue
        pais = _norm_country(m.group(1))
        if not pais or pais in totals:
            continue
        may25, may26, a25, a26 = (_u(m.group(i)) for i in range(2, 6))
        vmay = float(m.group(6).replace(",", "."))
        vacu = float(m.group(7).replace(",", "."))
        totals[pais] = {"may_2025": may25, "may_2026": may26, "acum_2025": a25,
                        "acum_2026": a26, "var_may_pct": vmay, "var_acum_pct": vacu}
        acum26_to_country[a26] = pais

    top10 = []

    def units_of(s: str) -> list:
        return [_u(t) for t in s.split() if _is_unit(t)]

    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.lower().startswith("total"):
            us = units_of(ln)
            if len(us) >= 4 and us[-1] in acum26_to_country:
                pais = acum26_to_country[us[-1]]
                j = i - 1
                block = []
                while j >= 0 and len(block) <= 12:
                    row = lines[j]
                    rtoks = row.split()
                    core = rtoks[:-1] if (rtoks and rtoks[-1].isdigit()) else rtoks
                    bu = units_of(" ".join(core))
                    es_fila_marca = len(bu) >= 2 and (
                        (rtoks and rtoks[-1].isdigit()) or row.upper().startswith("OTRAS"))
                    if es_fila_marca:
                        marca_tokens = [t for t in core if not _is_unit(t)
                                        and "%" not in t and "," not in t]
                        marca = " ".join(marca_tokens).strip()
                        if marca:
                            block.append((marca.upper(), bu[-2], bu[-1]))
                        j -= 1
                    else:
                        if row.split() and row.split()[0].upper().startswith("MARCA"):
                            break
                        if block:
                            break
                        j -= 1
                for marca, a25, a26 in reversed(block):
                    if marca in ("OTRAS", "MARCA"):
                        continue
                    var = round((a26 - a25) / a25 * 100, 1) if a25 else None
                    top10.append({
                        "pais": pais, "marca": marca.title(),
                        "acum_2025": a25, "acum_2026": a26, "var_acum_pct": var,
                        "origen": "China" if marca in MARCAS_CHINAS else "No china",
                    })
        i += 1

    return {"totals": totals, "top10": top10}


def _pdf_url_for(year: int, month: int) -> str:
    return PDF_TPL.format(mmm=MMM[month - 1], yy=f"{year % 100:02d}")


def discover_latest():
    import requests
    from bs4 import BeautifulSoup
    html = requests.get(INDICE, timeout=30).text
    soup = BeautifulSoup(html, "html.parser")
    meses = {m: i + 1 for i, m in enumerate(
        ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"])}
    for a in soup.find_all("a"):
        m = re.search(r"aladda,\s+(\w+)\s+(\d{4})", a.get_text(" ", strip=True), re.I)
        if m and m.group(1).lower() in meses:
            return int(m.group(2)), meses[m.group(1).lower()]
    raise RuntimeError("No pude descubrir el ultimo informe ALADDA en el indice")


def fetch_pdf_text(year: int, month: int) -> str:
    import io
    import requests
    import pdfplumber
    r = requests.get(_pdf_url_for(year, month), timeout=60)
    r.raise_for_status()
    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def write_csvs(parsed: dict, periodo_label: str) -> None:
    DATA.mkdir(exist_ok=True)
    with open(DATA / "aladda_country_totals.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["pais", "periodo", "may_2025", "may_2026", "acum_2025",
                    "acum_2026", "var_may_pct", "var_acum_pct", "en_alcance"])
        for pais, t in parsed["totals"].items():
            w.writerow([pais, periodo_label, t["may_2025"], t["may_2026"],
                        t["acum_2025"], t["acum_2026"], t["var_may_pct"],
                        t["var_acum_pct"], "si" if pais in EN_ALCANCE else "no"])
    with open(DATA / "aladda_top10.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["pais", "periodo", "marca", "unidades_curr", "unidades_prev",
                    "var_yoy_pct", "origen", "fuente"])
        for r in parsed["top10"]:
            if r["pais"] not in EN_ALCANCE:
                continue
            w.writerow([r["pais"], f"Acum {periodo_label}", r["marca"],
                        r["acum_2026"], r["acum_2025"],
                        "" if r["var_acum_pct"] is None else r["var_acum_pct"],
                        r["origen"], f"ALADDA (AMDA) {periodo_label}"])


def verify(parsed: dict) -> list:
    warns = []
    by_pais = {}
    for r in parsed["top10"]:
        by_pais.setdefault(r["pais"], []).append(r)
    for pais, t in parsed["totals"].items():
        if pais not in EN_ALCANCE:
            continue
        if pais not in by_pais:
            warns.append(f"{pais}: sin marcas parseadas")
            continue
        top = sum(r["acum_2026"] for r in by_pais[pais])
        if top > t["acum_2026"]:
            warns.append(f"{pais}: top10={top} > total={t['acum_2026']} (revisar)")
    return warns


_FIXTURE = (
    "URL PAIS Mayo 2025 Mayo 2026 Ene-may 2025 Ene-may 2026 Var mayo Var ene-may\n"
    "CHILE 25.720 25.734 128.351 132.871 0.1% 3.5%\n"
    "ECUADOR 10.258 13.093 45.272 63.773 27.6% 40.9%\n"
    "MARCA Cant Part Cant Part Cant Part Cant Part #\n"
    "KIA 1.578 15,4 % 2.189 16,7 % 7.370 16,3 % 10.241 16,1 % 1\n"
    "CHEVROLET 1.638 16,0 % 1.614 12,3 % 6.884 15,2 % 7.732 12,1 % 2\n"
    "GWM 502 4,9 % 718 5,5 % 2.335 5,2 % 3.508 5,5 % 4\n"
    "OTRAS 3.728 36,3 % 4.961 37,9 % 16.244 35,9 % 24.068 37,7 % 11\n"
    "Total 10.258 100,0 % 13.093 100,0 % 45.272 100,0 % 63.773 100,0 %\n"
)


def selftest() -> int:
    p = parse_text(_FIXTURE)
    assert "Ecuador" in p["totals"], "no parseo totales"
    assert p["totals"]["Ecuador"]["acum_2026"] == 63773
    marcas = {r["marca"]: r for r in p["top10"] if r["pais"] == "Ecuador"}
    assert marcas["Kia"]["acum_2026"] == 10241, marcas
    assert marcas["Kia"]["acum_2025"] == 7370, marcas
    assert marcas["Gwm"]["origen"] == "China"
    assert marcas["Kia"]["var_acum_pct"] == round((10241 - 7370) / 7370 * 100, 1)
    assert "Otras" not in marcas
    print("selftest OK:", {k: v["acum_2026"] for k, v in marcas.items()})
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mes", help="YYYY-MM")
    ap.add_argument("--pdf", help="ruta a un PDF local")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        sys.exit(selftest())

    if a.pdf:
        import pdfplumber
        with pdfplumber.open(a.pdf) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages)
        ym = a.mes or dt.date.today().strftime("%Y-%m")
        year, month = (int(x) for x in ym.split("-"))
    else:
        if a.mes:
            year, month = (int(x) for x in a.mes.split("-"))
        else:
            year, month = discover_latest()
        print(f"Procesando ALADDA {year}-{month:02d} ...")
        text = fetch_pdf_text(year, month)

    parsed = parse_text(text)
    label = f"{MMM[month - 1].capitalize()} {year}"
    write_csvs(parsed, periodo_label=label)
    warns = verify(parsed)
    n_tot = len(parsed["totals"])
    n_mar = sum(1 for r in parsed["top10"] if r["pais"] in EN_ALCANCE)
    print(f"OK -> data/aladda_top10.csv + aladda_country_totals.csv ({label})")
    print(f"   paises: {n_tot} | marcas (8 objetivo): {n_mar}")
    for w in warns:
        print("   [!]", w)


if __name__ == "__main__":
    main()
