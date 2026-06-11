#!/usr/bin/env python3
"""Scraper de informes mensuales (Chile / Perú / Ecuador) + reconstrucción de data/.

Uso:
    python scripts/ingest.py [--pais chile|peru|ecuador|all] [--backfill]

Flags:
    --pais      País a procesar (default: all).
    --backfill  Procesa TODOS los meses conocidos, no solo los nuevos.

Lo que hace:
    1. Descarga PDFs de los informes mensuales usando sources.py.
    2. Parsea las tablas de ventas por marca con pdfplumber.
    3. Actualiza data/base_nacional.csv (snapshot último acumulado) y
       data/peru_nacional_mensual.csv / data/chile_mensual.csv / data/ecuador_mensual.csv.
    4. Reconstruye data/base_nacional.json, data/trend.json, data/china_tl.json.
    5. Llama a build_dashboard.py para regenerar el HTML.

Requiere: pdfplumber, requests, beautifulsoup4  (ver requirements.txt)
"""

import argparse
import csv
import io
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pdfplumber
import requests

ROOT   = Path(__file__).resolve().parents[1]
DATA   = ROOT / "data"
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from sources import (
    PERU, CHILE, ECUADOR,
    MESES_ES, MESES_PE,
    MARCAS_CHINAS,
)
from discover import discover_peru_urls, discover_ecuador_ids

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/pdf,application/octet-stream,*/*",
    "Referer": "https://www.aeade.net/",
}
SLEEP   = 2  # segundos entre requests para no sobrecargar las fuentes


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def fetch_pdf(url: str) -> bytes | None:
    """Descarga un PDF y devuelve los bytes, o None si falla."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        if "pdf" not in r.headers.get("Content-Type", "").lower() and not url.endswith(".pdf"):
            print(f"  [WARN] Content-Type inesperado en {url}: {r.headers.get('Content-Type')}")
        return r.content
    except Exception as e:
        print(f"  [ERROR] No se pudo descargar {url}: {e}")
        return None


def pdf_to_lines(pdf_bytes: bytes) -> list[str]:
    """Extrae texto de un PDF como lista de líneas limpias."""
    lines = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
            lines.extend(text.splitlines())
    return [l.strip() for l in lines if l.strip()]


# ---------------------------------------------------------------------------
# PERU — parser de PDF de AAP (formato 2026: 2 columnas por fila, pag. 15)
# ---------------------------------------------------------------------------

# Una entrada por columna: "1 Toyota 3,723 3,183 -14.5% 27.9%"
PERU_ENTRY = re.compile(
    r'(\d+)\s+'                          # rank
    r'((?:[A-Za-z\xc0-\xff]+\s*)+?)\s+' # marca (puede tener espacios)
    r'([\d,]+)\s+'                       # año anterior
    r'([\d,]+)\s+'                       # año actual
    r'(-?[\d.]+)%\s+'                    # var%
    r'([\d.]+)%'                         # part%
)

PERU_HDR   = re.compile(r'Rank\.\s*Marca\s+(\d{4})\s+(\d{4})')
# Página con el resumen nacional por marca (tiene ranking + "de cada año")
PERU_PAGE = re.compile(r'Venta de veh[íi]culos livianos a \w+ de cada a', re.IGNORECASE)


def parse_peru_pdf(pdf_bytes: bytes) -> tuple[list[tuple], int | None]:
    """Parsea las tablas de vehículos livianos del PDF mensual de AAP.

    Busca la página con 'Venta de vehículos livianos a {mes} de cada año'
    y extrae las 4 tablas (2 columnas) sumando por marca.

    Devuelve ([(marca, acum_curr, acum_prev), ...], anio_acum).
    """
    target_page = None
    anio_acum = None

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
            if PERU_PAGE.search(text) and PERU_HDR.search(text):
                target_page = text
                break

    if not target_page:
        return [], None

    lines = [l.strip() for l in target_page.splitlines() if l.strip()]

    # Detectar año del acumulado desde la primera cabecera de tabla
    for line in lines:
        hm = PERU_HDR.search(line)
        if hm:
            anio_acum = int(hm.group(2))
            break

    if not anio_acum:
        return [], None

    # Acumular unidades por marca (todas las entradas de las 4 tablas)
    acum: dict[str, list[int]] = {}  # marca -> [curr, prev]
    for line in lines:
        if line.startswith(("Total", "Otros", "Fuente", "Rank")):
            continue
        for m in PERU_ENTRY.finditer(line):
            marca = m.group(2).strip().title()
            prev  = int(m.group(3).replace(",", ""))
            curr  = int(m.group(4).replace(",", ""))
            if marca not in acum:
                acum[marca] = [0, 0]
            acum[marca][0] += curr
            acum[marca][1] += prev

    out = [(marca, vals[0], vals[1]) for marca, vals in acum.items() if vals[0] > 0]
    return out, anio_acum


def peru_url(anio: int, mes: int) -> str | None:
    key = (anio, mes)
    if key in PERU["hashed"]:
        return PERU["hashed"][key]
    if anio == 2025 and mes <= 9:
        nombre = MESES_PE[mes - 1]
        return PERU["base_mes"].format(anio=anio, Mes=nombre)
    return None  # desconocido: hay que descubrirlo desde el índice


def ingest_peru(backfill: bool = False) -> list[dict]:
    """Descarga e ingesta los informes mensuales de Perú.
    Devuelve lista de registros {'anio','mes','marca','unid_acum','unid_mes'}.
    """
    existing = _load_monthly_csv("peru_nacional_mensual.csv")
    known    = {(int(r["anio"]), int(r["mes"])) for r in existing}

    # URL map = descubrimiento en vivo (prioridad) + fallback hardcodeado de sources.py
    url_map = discover_peru_urls()
    print(f"  Perú: {len(url_map)} informes descubiertos en el índice de AAP")

    # Candidatos: todos los meses con URL conocida (descubierta o hardcodeada)
    candidates = []
    for anio in [2025, 2026]:
        for mes in range(1, 13):
            if datetime(anio, mes, 1) > datetime.now():
                break
            if (anio, mes) in url_map or peru_url(anio, mes):
                candidates.append((anio, mes))

    if not backfill:
        candidates = [(a, m) for a, m in candidates if (a, m) not in known]

    new_rows: dict[tuple, dict] = {}  # (anio,mes,marca) -> {acum}

    for anio, mes in candidates:
        url = url_map.get((anio, mes)) or peru_url(anio, mes)
        if not url:
            print(f"  [SKIP] Perú {anio}-{mes:02d}: URL no conocida")
            continue
        print(f"  ->Peru {anio}-{mes:02d}  {url}")
        pdf = fetch_pdf(url)
        if not pdf:
            continue
        filas, yr = parse_peru_pdf(pdf)
        if not filas:
            print(f"  [WARN] Peru {anio}-{mes:02d}: parse sin resultados")
            continue
        if yr != anio:
            print(f"  [WARN] Peru {anio}-{mes:02d}: anio detectado {yr} != {anio}")
            continue
        for marca, curr, _ in filas:
            new_rows[(anio, mes, marca)] = curr
        time.sleep(SLEEP)

    if not new_rows:
        print("  Perú: sin datos nuevos.")
        return existing

    # Reconstruir serie completa combinando existing + new
    combined: dict[tuple, int] = {}
    for r in existing:
        combined[(int(r["anio"]), int(r["mes"]), r["marca"])] = int(r["unid_acum"])
    combined.update(new_rows)

    return _build_monthly_series(combined, "Peru")


# ---------------------------------------------------------------------------
# CHILE — parser de PDF de CAVEM
# ---------------------------------------------------------------------------

# Formato CAVEM: cada fila = "rank marca mes_curr mes_prev var% part% part%  rank marca acum_curr acum_prev var% part% part%"
CHILE_PAGE = re.compile(r'RANKING MARCAS', re.IGNORECASE)
CHILE_HDR  = re.compile(r'Ranking Acumulado', re.IGNORECASE)
CHILE_ACUM = re.compile(
    r'(\d+)\s+'
    r'([A-Z][A-Z0-9 \-]+?)\s+'
    r'(\d[\d.]*)\s+'
    r'(\d[\d.]*)\s+'
    r'-?[\d,]+%'
)


def _chile_pdf_url(anio: int, mes: int, chile_hashes: dict) -> str | None:
    key = (anio, mes)
    if key in chile_hashes:
        h = chile_hashes[key]
        return f"https://www.cavem.cl/informes/{h}.pdf"
    return None


def _load_chile_hashes() -> dict[tuple, str]:
    path = DATA / "chile_hashes.csv"
    result = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                result[(int(row["anio"]), int(row["mes"]))] = row["hash"]
    return result


def _discover_chile_hash(anio: int, mes: int) -> str | None:
    """Abre la página de CAVEM para ese mes y extrae el hash del PDF."""
    nombre_mes = MESES_ES[mes - 1]
    url = CHILE["pagina_mes"].format(mes=nombre_mes, anio=anio)
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        m = re.search(CHILE["pdf_regex"], r.text)
        if m:
            return m.group(1)
    except Exception as e:
        print(f"  [ERROR] CAVEM descubrimiento {anio}-{mes:02d}: {e}")
    return None


def parse_chile_pdf(pdf_bytes: bytes) -> list[tuple]:
    """Extrae ranking acumulado de inscripciones del PDF de CAVEM.

    Devuelve [(marca, acum_curr, acum_prev), ...].
    Toma la primera pagina RANKING MARCAS con 'Ranking Acumulado'
    (inscripciones nuevas, no transferencias).
    """
    target_page = None
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
            if CHILE_PAGE.search(text) and CHILE_HDR.search(text):
                target_page = text
                break
    if not target_page:
        return []

    lines = [l.strip() for l in target_page.splitlines() if l.strip()]
    out = []
    in_table = False
    for line in lines:
        if CHILE_HDR.search(line):
            in_table = True
            continue
        if not in_table:
            continue
        if line.upper().startswith(("TOTAL", "OTRAS", "PARTICIPACI", "MARCA")):
            if line.upper().startswith("TOTAL"):
                break
            continue
        matches = list(CHILE_ACUM.finditer(line))
        if len(matches) >= 2:
            m = matches[1]  # bloque acumulado (lado derecho)
        elif len(matches) == 1:
            m = matches[0]
        else:
            continue
        marca = m.group(2).strip().title()
        curr  = int(m.group(3).replace(".", ""))
        prev  = int(m.group(4).replace(".", ""))
        out.append((marca, curr, prev))
    return out


def ingest_chile(backfill: bool = False) -> list[dict]:
    existing   = _load_monthly_csv("chile_mensual.csv")
    known      = {(int(r["anio"]), int(r["mes"])) for r in existing}
    hashes     = _load_chile_hashes()

    # Descubrir hashes faltantes en el CSV
    candidates = []
    for anio in [2025, 2026]:
        for mes in range(1, 13):
            if datetime(anio, mes, 1) > datetime.now():
                break
            candidates.append((anio, mes))

    # Intentar descubrir hashes que no tenemos
    for anio, mes in candidates:
        if (anio, mes) not in hashes:
            print(f"  ->Chile {anio}-{mes:02d}: descubriendo hash...")
            h = _discover_chile_hash(anio, mes)
            if h:
                hashes[(anio, mes)] = h
                print(f"     hash encontrado: {h}")
                _append_chile_hash(anio, mes, h)
            time.sleep(SLEEP)

    if not backfill:
        candidates = [(a, m) for a, m in candidates if (a, m) not in known]

    combined: dict[tuple, int] = {}
    for r in existing:
        combined[(int(r["anio"]), int(r["mes"]), r["marca"])] = int(r["unid_acum"])

    for anio, mes in candidates:
        url = _chile_pdf_url(anio, mes, hashes)
        if not url:
            print(f"  [SKIP] Chile {anio}-{mes:02d}: hash no disponible")
            continue
        print(f"  ->Chile {anio}-{mes:02d}  {url}")
        pdf = fetch_pdf(url)
        if not pdf:
            continue
        filas = parse_chile_pdf(pdf)
        if not filas:
            print(f"  [WARN] Chile {anio}-{mes:02d}: parse sin resultados")
            continue
        for marca, acum, _ in filas:
            combined[(anio, mes, marca)] = acum
        time.sleep(SLEEP)

    if not combined:
        print("  Chile: sin datos.")
        return existing

    return _build_monthly_series(combined, "Chile")


def _append_chile_hash(anio: int, mes: int, hash_val: str):
    path = DATA / "chile_hashes.csv"
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([anio, mes, hash_val])


# ---------------------------------------------------------------------------
# ECUADOR — parser de PDF de AEADE (dos formatos según el año)
# ---------------------------------------------------------------------------

# Abreviaciones de meses en español para detectar el período en el PDF
_EC_MES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}


def _ec_group_words(words, y_tol=8):
    from collections import defaultdict as _dd
    rows = _dd(list)
    for w in words:
        rows[round(w["top"] / y_tol) * y_tol].append(w)
    return {y: sorted(ws, key=lambda w: w["x0"]) for y, ws in sorted(rows.items())}


def _ec_merge_rightmost(word_list, col_x_min, x_gap=25):
    """Toma los fragmentos en x > col_x_min, agrupa por proximidad, devuelve entero del grupo más a la derecha."""
    frags = [(w["x0"], w["text"]) for w in word_list if w["x0"] >= col_x_min]
    if not frags:
        return None
    groups, cur = [], [frags[0]]
    for i in range(1, len(frags)):
        if frags[i][0] - cur[-1][0] <= x_gap:
            cur.append(frags[i])
        else:
            groups.append(cur); cur = [frags[i]]
    groups.append(cur)
    token = "".join(t for _, t in groups[-1])
    try:
        return int(token.replace(".", "").replace(",", ""))
    except ValueError:
        return None


_EC_MESES_LONG = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9,
    "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def _parse_ecuador_old_format(page) -> tuple[list[tuple], int | None, int | None]:
    """PDF con tabla comparativa y números fragmentados (boletines 2025-01 a ~2025-05).

    Sub-formatos:
      - Annual (ene-dic): 2 años x 2 cols (mes + acum)
      - Mensual puro: 2 años x 1 col (sólo ese mes)
      - Mensual + acum: 2 años x 2 cols (mes + acum-YTD)
    """
    text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
    words = page.extract_words(x_tolerance=1, y_tolerance=3)
    row_map = _ec_group_words(words, y_tol=8)

    # Detectar año actual desde fila de cabecera (fila con 2+ patrones 20XX)
    curr_year = None
    right_col_x = 700
    for ws in row_map.values():
        year_words = [w for w in ws if re.match(r"^20\d\d$", w["text"])]
        if len(year_words) >= 2:
            curr_year = int(year_words[-1]["text"])
            right_col_x = year_words[-1]["x0"] - 30
            break

    if not curr_year:
        return [], None, None

    # Detectar mes desde la línea de cabecera de columna:
    # "Ene - Dic", "Ene - Mar", "Enero Enero" etc.
    curr_mes = None
    # Patrón "Ene... - {mes}" indica acumulado hasta ese mes
    m = re.search(r"Ene(?:ro)?\s*-\s*(\w+)", text, re.IGNORECASE)
    if m:
        abbr = m.group(1).lower()[:3]
        curr_mes = _EC_MES.get(abbr)
    if not curr_mes:
        # Fallback: buscar nombre largo de mes en la zona de cabecera (primeras 10 líneas del texto)
        for line in text.splitlines()[:15]:
            for nombre, num in _EC_MESES_LONG.items():
                if nombre in line.lower():
                    curr_mes = num
                    break
            if curr_mes:
                break

    if not curr_mes:
        curr_mes = 12  # default conservador

    out = []
    SKIP = {"MARCAS", "TOTAL", "OTRAS", "FUENTE", "NOTA", "SRI", "ELABORACI"}
    for ws in row_map.values():
        brand_words = [w for w in ws if w["x0"] < 400 and re.match(r"^[A-Z][A-Z0-9 \-]*$", w["text"])]
        if not brand_words:
            continue
        brand = " ".join(w["text"] for w in brand_words).strip()
        if any(brand.startswith(s) for s in SKIP):
            continue
        val = _ec_merge_rightmost(ws, right_col_x)
        if val and val > 0:
            out.append((brand.title(), val))

    return out, curr_year, curr_mes


def _parse_ecuador_new_format(text: str) -> tuple[list[tuple], int | None, int | None]:
    """PDF exportado desde Power BI con texto limpio (boletines 2025-06+).

    Maneja dos sub-variantes:
      - 4 columnas: "{mon} {yr1}  {mon} {yr2}  Ene-{mon} {yr1}  Ene-{mon} {yr2}"
      - 2 columnas: "{mon} {yr1}  {mon} {yr2}"  (comparativo mensual puro)
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    anio, mes = None, None
    for line in lines[:20]:
        # Prioridad: "Ene-{mon} {yy}" = acumulado YTD
        m = re.search(r"Ene-(\w{3})\s+(\d{2})\s*$", line, re.IGNORECASE)
        if m:
            mes_abbr = m.group(1).lower()[:3]
            mes = _EC_MES.get(mes_abbr)
            anio = 2000 + int(m.group(2))
            break
        # Fallback: "{mon} {yy}" al final (2-columnas mensual)
        m2 = re.search(r"\b(\w{3,})\s+(\d{2})\s*$", line, re.IGNORECASE)
        if m2:
            mes_abbr = m2.group(1).lower()[:3]
            cand = _EC_MES.get(mes_abbr)
            if cand:
                mes = cand
                anio = 2000 + int(m2.group(2))
                break

    if not anio:
        return [], None, None

    out = []
    header_passed = False
    for line in lines:
        if re.match(r"^Marca\s+\w{3}", line, re.IGNORECASE):
            header_passed = True
            continue
        if not header_passed:
            continue
        # Fila de datos: MARCA seguido de 2 o 4 números
        m = re.match(r"^([A-Z][A-Z0-9 \-]+?)\s+((?:[\d]+\s+){1,3}[\d]+)\s*$", line)
        if m:
            brand = m.group(1).strip().title()
            if brand.lower().startswith("otras"):
                break
            nums = [int(x) for x in m.group(2).split()]
            cumul = nums[-1]  # última columna = más reciente
            if cumul > 0:
                out.append((brand, cumul))
        elif header_passed and re.match(r"^\d", line):
            break

    return out, anio, mes


def parse_ecuador_pdf(pdf_bytes: bytes) -> tuple[list[tuple], int | None, int | None]:
    """Parsea el PDF mensual de AEADE. Devuelve ([(marca, unidades)], anio, mes)."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
            if "TOP 20" in text and "MARCAS DE VEH" in text:
                if "Power BI Desktop" in text or re.search(r"Ene-\w{3}\s+\d{2}", text):
                    return _parse_ecuador_new_format(text)
                else:
                    return _parse_ecuador_old_format(page)
    return [], None, None


def _load_ecuador_ids() -> dict[str, int]:
    return dict(ECUADOR["download_ids"])


def ingest_ecuador(backfill: bool = False) -> list[dict]:
    existing  = _load_monthly_csv("ecuador_mensual.csv")
    known_ym  = {(int(r["anio"]), int(r["mes"])) for r in existing}

    # IDs en vivo desde el índice de AEADE (más nuevo primero) + fallback hardcodeado
    discovered = discover_ecuador_ids()
    hardcoded  = list(_load_ecuador_ids().values())
    # Unión preservando orden (descubiertos primero, luego hardcodeados no vistos)
    seen = set()
    ids: list[int] = []
    for did in discovered + hardcoded:
        if did not in seen:
            seen.add(did)
            ids.append(did)
    print(f"  Ecuador: {len(discovered)} IDs en índice AEADE ({len(ids)} totales con fallback)")

    # Sin backfill: solo revisar los más nuevos (evita descargar 60+ PDFs cada quincena)
    if not backfill:
        ids = ids[:4]

    combined: dict[tuple, int] = {}
    for r in existing:
        combined[(int(r["anio"]), int(r["mes"]), r["marca"])] = int(r["unid_acum"])

    for did in ids:
        url = ECUADOR["descarga"].format(download_id=did)
        pdf = fetch_pdf(url)
        if not pdf:
            continue
        filas, anio, mes = parse_ecuador_pdf(pdf)
        if not filas or not anio or not mes:
            print(f"  [WARN] Ecuador id={did}: parse sin resultados")
            continue
        if not backfill and (anio, mes) in known_ym:
            continue  # ya lo tenemos; en orden nuevo->viejo el resto también
        print(f"  ->Ecuador id={did}  periodo {anio}-{mes:02d}  marcas: {len(filas)}")
        for marca, unid in filas:
            combined[(anio, mes, marca)] = unid
        time.sleep(SLEEP)

    if not combined:
        print("  Ecuador: sin datos.")
        return existing

    return _build_monthly_series(combined, "Ecuador")


# ---------------------------------------------------------------------------
# Helpers de CSV / series
# ---------------------------------------------------------------------------

def _load_monthly_csv(filename: str) -> list[dict]:
    path = DATA / filename
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _build_monthly_series(combined: dict[tuple, int], pais: str) -> list[dict]:
    """Convierte {(anio,mes,marca): acum} en lista de dicts con unid_mes calculado."""
    meses_por_marca: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
    for (a, m, marca), acum in combined.items():
        meses_por_marca[marca].append((a, m, acum))

    rows = []
    for marca, pts in meses_por_marca.items():
        pts.sort()
        for i, (a, m, acum) in enumerate(pts):
            if i > 0 and pts[i - 1][0] == a and pts[i - 1][1] == m - 1:
                mensual = acum - pts[i - 1][2]
                mensual = mensual if mensual >= 0 else ""
            elif m == 1:
                mensual = acum
            else:
                mensual = ""
            rows.append({
                "pais": pais, "anio": a, "mes": m,
                "marca": marca, "unid_acum": acum, "unid_mes": mensual,
            })

    rows.sort(key=lambda r: (r["anio"], r["mes"], r["marca"]))
    return rows


def save_monthly_csv(rows: list[dict], filename: str):
    path = DATA / filename
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["pais", "anio", "mes", "marca", "unid_acum", "unid_mes"])
        w.writeheader()
        w.writerows(rows)
    print(f"  Guardado: {path} ({len(rows)} filas)")


# ---------------------------------------------------------------------------
# Reconstrucción de base_nacional + JSONs para el dashboard
# ---------------------------------------------------------------------------

def rebuild_base_nacional(
    peru_rows: list[dict],
    chile_rows: list[dict],
    ecuador_rows: list[dict],
):
    """Reconstruye base_nacional.csv/.json con el último acumulado de cada país."""
    snapshots = []

    def last_snapshot(rows: list[dict], pais: str, fuente: str):
        if not rows:
            return
        # Último mes disponible
        last_anio = max(int(r["anio"]) for r in rows)
        last_mes  = max(int(r["mes"]) for r in rows if int(r["anio"]) == last_anio)
        periodo   = f"Acum a {MESES_ES[last_mes - 1].capitalize()} {last_anio}"
        subset    = [r for r in rows if int(r["anio"]) == last_anio and int(r["mes"]) == last_mes]
        for r in subset:
            # Buscar acum mismo mes año anterior
            prev = next(
                (x for x in rows if int(x["anio"]) == last_anio - 1
                 and int(x["mes"]) == last_mes and x["marca"] == r["marca"]),
                None
            )
            curr  = int(r["unid_acum"])
            prev_u = int(prev["unid_acum"]) if prev else None
            var    = round((curr - prev_u) / prev_u * 100, 1) if prev_u else None
            snapshots.append({
                "pais": pais, "periodo": periodo, "marca": r["marca"],
                "uc": curr,
                "up": prev_u if prev_u else None,
                "var": var,
                "fuente": fuente,
            })

    last_snapshot(peru_rows,    "Peru",    "AAP/SUNARP")
    last_snapshot(chile_rows,   "Chile",   "CAVEM")
    last_snapshot(ecuador_rows, "Ecuador", "AEADE")

    # Colombia: mantener filas existentes (se actualizan a mano)
    existing = _load_monthly_csv("base_nacional.csv") if False else []  # no toca Colombia
    with open(DATA / "base_nacional.csv", newline="", encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))
    colombia_rows = [r for r in all_rows if r["pais"] == "Colombia"]

    # Colombia rows come from CSV (long keys) — normalize to short keys for JSON
    colombia_json = []
    for r in colombia_rows:
        uc = int(r.get("unidades_curr") or r.get("uc") or 0)
        up_raw = r.get("unidades_prev") or r.get("up")
        up = int(up_raw) if up_raw not in (None, "", "n/d") else None
        var_raw = r.get("var_yoy_pct") or r.get("var")
        var = float(var_raw) if var_raw not in (None, "", "n/d") else None
        colombia_json.append({
            "pais": r["pais"], "periodo": r["periodo"], "marca": r["marca"],
            "uc": uc, "up": up, "var": var, "fuente": r["fuente"],
        })

    final_json = snapshots + colombia_json

    # CSV keeps long field names for human readability
    csv_rows = []
    for r in final_json:
        csv_rows.append({
            "pais": r["pais"], "periodo": r["periodo"], "marca": r["marca"],
            "unidades_curr": r["uc"],
            "unidades_prev": r["up"] if r["up"] is not None else "",
            "var_yoy_pct":   r["var"] if r["var"] is not None else "",
            "fuente": r["fuente"],
        })
    fields = ["pais", "periodo", "marca", "unidades_curr", "unidades_prev", "var_yoy_pct", "fuente"]
    with open(DATA / "base_nacional.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(csv_rows)

    # JSON para el dashboard (short keys)
    with open(DATA / "base_nacional.json", "w", encoding="utf-8") as f:
        json.dump(final_json, f, ensure_ascii=False, indent=2)

    final = final_json  # for the print below

    print(f"  base_nacional: {len(final)} filas ({len(snapshots)} actualizadas + {len(colombia_rows)} Colombia)")


def rebuild_trend_json(peru_rows: list[dict], chile_rows: list[dict]):
    """Reconstruye trend.json con la serie mensual (Perú + Chile)."""
    trend = []
    for r in peru_rows + chile_rows:
        if r["unid_mes"] == "" or r["unid_mes"] is None:
            continue
        trend.append({
            "pais":  r["pais"],
            "anio":  int(r["anio"]),
            "mes":   int(r["mes"]),
            "marca": r["marca"],
            "unid":  int(r["unid_mes"]),
        })
    trend.sort(key=lambda x: (x["pais"], x["anio"], x["mes"], x["marca"]))
    with open(DATA / "trend.json", "w", encoding="utf-8") as f:
        json.dump(trend, f, ensure_ascii=False, indent=2)
    print(f"  trend.json: {len(trend)} puntos")


def rebuild_china_tl(peru_rows: list[dict], chile_rows: list[dict]):
    """Reconstruye china_tl.json: penetración de marcas chinas mes a mes."""
    data: dict[tuple, dict] = {}  # (pais, anio, mes) -> {total, chinas}

    for r in peru_rows + chile_rows:
        if r["unid_mes"] == "" or r["unid_mes"] is None:
            continue
        key = (r["pais"], int(r["anio"]), int(r["mes"]))
        if key not in data:
            data[key] = {"total": 0, "chinas": 0}
        unid = int(r["unid_mes"])
        data[key]["total"] += unid
        if r["marca"] in MARCAS_CHINAS:
            data[key]["chinas"] += unid

    result = []
    for (pais, anio, mes), v in sorted(data.items()):
        pct = round(v["chinas"] / v["total"] * 100, 1) if v["total"] else 0
        result.append({"pais": pais, "anio": anio, "mes": mes,
                        "total": v["total"], "chinas": v["chinas"], "pct_chinas": pct})
    with open(DATA / "china_tl.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"  china_tl.json: {len(result)} puntos")


# ---------------------------------------------------------------------------
# Reporte de la corrida (para notificación por email desde el workflow)
# ---------------------------------------------------------------------------

def _periodos(rows: list[dict]) -> set:
    return {(int(r["anio"]), int(r["mes"])) for r in rows}


def _country_report(rows: list[dict], prev: set) -> dict:
    cur = _periodos(rows)
    nuevos = sorted(cur - prev)
    ultimo = max(cur) if cur else None
    return {
        "ejecutado": True,
        "nuevos": [f"{a}-{m:02d}" for a, m in nuevos],
        "ultimo_periodo": f"{ultimo[0]}-{ultimo[1]:02d}" if ultimo else None,
        "total_filas": len(rows),
    }


def _write_report(report: dict):
    """Escribe run_report.json (máquina) y run_report.txt (cuerpo del email)."""
    (ROOT / "run_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"AndinaCopilot - corrida {report['fecha']}", ""]
    hubo_nuevos = False
    for pais, r in report["paises"].items():
        if r.get("error"):
            lines.append(f"[ERROR] {pais}: fallo la ingesta - {r['error']}")
        elif not r.get("ejecutado"):
            lines.append(f"[----]  {pais}: no ejecutado en esta corrida")
        elif r["nuevos"]:
            hubo_nuevos = True
            lines.append(
                f"[NUEVO] {pais}: {len(r['nuevos'])} mes(es) nuevo(s) -> "
                f"{', '.join(r['nuevos'])}  (último: {r['ultimo_periodo']}, {r['total_filas']} filas)")
        else:
            lines.append(
                f"[=]     {pais}: sin datos nuevos  (último: {r['ultimo_periodo']}, {r['total_filas']} filas)")

    lines.append("")
    lines.append("Resumen: " + ("se actualizó al menos un país." if hubo_nuevos
                                else "ningún país trajo datos nuevos."))
    lines.append("")
    lines.append("Dashboard: https://andina-copilot.vercel.app")
    (ROOT / "run_report.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n=== Reporte ===")
    print("\n".join(lines))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pais", default="all", choices=["chile", "peru", "ecuador", "all"])
    ap.add_argument("--backfill", action="store_true", help="Reprocesar todos los meses conocidos")
    args = ap.parse_args()

    run_peru    = args.pais in ("peru",    "all")
    run_chile   = args.pais in ("chile",   "all")
    run_ecuador = args.pais in ("ecuador", "all")

    # Cargar series existentes (para los países que no se actualizan en esta corrida)
    peru_rows    = _load_monthly_csv("peru_nacional_mensual.csv")
    chile_rows   = _load_monthly_csv("chile_mensual.csv")
    ecuador_rows = _load_monthly_csv("ecuador_mensual.csv")

    report = {"fecha": datetime.now().strftime("%Y-%m-%d %H:%M"), "paises": {}}
    prev = {
        "Peru":    _periodos(peru_rows),
        "Chile":   _periodos(chile_rows),
        "Ecuador": _periodos(ecuador_rows),
    }

    if run_peru:
        print("\n=== PERU ===")
        try:
            peru_rows = ingest_peru(backfill=args.backfill)
            save_monthly_csv(peru_rows, "peru_nacional_mensual.csv")
            report["paises"]["Peru"] = _country_report(peru_rows, prev["Peru"])
        except Exception as e:
            print(f"  [ERROR] Perú: {e}")
            report["paises"]["Peru"] = {"ejecutado": True, "error": str(e)}

    if run_chile:
        print("\n=== CHILE ===")
        try:
            chile_rows = ingest_chile(backfill=args.backfill)
            save_monthly_csv(chile_rows, "chile_mensual.csv")
            report["paises"]["Chile"] = _country_report(chile_rows, prev["Chile"])
        except Exception as e:
            print(f"  [ERROR] Chile: {e}")
            report["paises"]["Chile"] = {"ejecutado": True, "error": str(e)}

    if run_ecuador:
        print("\n=== ECUADOR ===")
        try:
            ecuador_rows = ingest_ecuador(backfill=args.backfill)
            save_monthly_csv(ecuador_rows, "ecuador_mensual.csv")
            report["paises"]["Ecuador"] = _country_report(ecuador_rows, prev["Ecuador"])
        except Exception as e:
            print(f"  [ERROR] Ecuador: {e}")
            report["paises"]["Ecuador"] = {"ejecutado": True, "error": str(e)}

    print("\n=== Reconstruyendo JSONs ===")
    rebuild_base_nacional(peru_rows, chile_rows, ecuador_rows)
    rebuild_trend_json(peru_rows, chile_rows)
    rebuild_china_tl(peru_rows, chile_rows)

    print("\n=== Regenerando dashboard ===")
    import subprocess
    subprocess.run([sys.executable, str(SCRIPTS / "build_dashboard.py")], check=True)

    _write_report(report)
    print("\nListo.")


if __name__ == "__main__":
    main()
