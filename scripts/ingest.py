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
    canon,
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

def fetch_pdf(url: str, retries: int = 3) -> bytes | None:
    """Descarga un PDF y devuelve los bytes, o None si falla.

    Reintenta ante errores transitorios (conexión cortada/timeout). No reintenta
    en 404 (URL inexistente).
    """
    for intento in range(1, retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=40)
            r.raise_for_status()
            if "pdf" not in r.headers.get("Content-Type", "").lower() and not url.endswith(".pdf"):
                print(f"  [WARN] Content-Type inesperado en {url}: {r.headers.get('Content-Type')}")
            return r.content
        except requests.HTTPError as e:
            code = getattr(e.response, "status_code", None)
            if code == 404:
                print(f"  [ERROR] 404 (no existe): {url}")
                return None
            if intento == retries:
                print(f"  [ERROR] HTTP {code} tras {retries} intentos: {url}")
                return None
        except Exception as e:
            if intento == retries:
                print(f"  [ERROR] No se pudo descargar {url}: {e}")
                return None
        time.sleep(2 * intento)
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


# Lista COMPLETA de marcas (livianos nuevos), unidades del mes. Página titulada
# "RANKING DE MARCAS CHINAS {mes}" pero lista las ~70 marcas en 2 columnas.
# Trae solo unidades del mes (sin acumulado ni interanual) -> sirve para la cola larga.
CHILE_FULL_HDR = re.compile(r"MARCA\s+UNIDADES\s+PARTICIPACI", re.IGNORECASE)
CHILE_FULL_ROW = re.compile(r"(\d+)\s+([A-Z][A-Z0-9 .\-]+?)\s+([\d.]+)\s+[\d,]+%")


def parse_chile_full_pdf(pdf_bytes: bytes) -> dict[str, int]:
    """Devuelve {marca: unidades_del_mes} con TODAS las marcas de livianos nuevos.

    Elige la página de la lista completa (no usados ni transferencias).
    """
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
            if not CHILE_FULL_HDR.search(text):
                continue
            low = text.lower()
            if "usados" in low or "transferencias" in low or "mercado total" in low:
                continue
            out: dict[str, int] = {}
            for line in text.splitlines():
                for m in CHILE_FULL_ROW.finditer(line):
                    marca = m.group(2).strip().title()
                    if marca.upper() in ("TOTAL", "OTRAS", "MARCA"):
                        continue
                    out[marca] = int(m.group(3).replace(".", ""))
            if out:
                return out
    return {}


def ingest_chile_full(backfill: bool = False) -> list[dict]:
    """Serie mensual de la lista COMPLETA de marcas de Chile (unidades del mes).

    Aislada de chile_mensual (top-25 acumulado oficial). Reutiliza los hashes ya
    descubiertos. Solo unidades del mes; el acumulado se calcula por cumsum aguas abajo.
    """
    existing = _load_monthly_csv("chile_full_mensual.csv")
    known    = {(int(r["anio"]), int(r["mes"])) for r in existing}
    hashes   = _load_chile_hashes()

    candidates = []
    for anio in [2025, 2026]:
        for mes in range(1, 13):
            if datetime(anio, mes, 1) > datetime.now():
                break
            candidates.append((anio, mes))
    if not backfill:
        candidates = [(a, m) for a, m in candidates if (a, m) not in known]

    # filas previas como {(anio,mes,marca): unid_mes}
    out_rows = {(int(r["anio"]), int(r["mes"]), r["marca"]): r["unid_mes"]
                for r in existing}

    for anio, mes in candidates:
        url = _chile_pdf_url(anio, mes, hashes)
        if not url:
            continue
        pdf = fetch_pdf(url)
        if not pdf:
            continue
        marcas = parse_chile_full_pdf(pdf)
        if not marcas:
            print(f"  [WARN] Chile full {anio}-{mes:02d}: sin lista completa (formato viejo)")
            continue
        for marca, unid in marcas.items():
            out_rows[(anio, mes, marca)] = unid
        print(f"  ->Chile full {anio}-{mes:02d}: {len(marcas)} marcas")
        time.sleep(SLEEP)

    rows = [{"pais": "Chile", "anio": a, "mes": m, "marca": mk,
             "unid_acum": "", "unid_mes": u}
            for (a, m, mk), u in out_rows.items()]
    rows.sort(key=lambda r: (r["anio"], r["mes"], r["marca"]))
    return rows


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
# COLOMBIA — boletín mensual ANDI (Cámara Industria Automotriz, fuente RUNT)
# ---------------------------------------------------------------------------
# PDF con "TOP 20 marcas": unidades del mes + mismo mes año anterior + var%.
import urllib.parse as _urlparse

CO_MMM = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN",
          "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"]
CO_MESES = {m: i + 1 for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
     "agosto", "septiembre", "octubre", "noviembre", "diciembre"])}
# fila: "1 RENAULT 2.582 1.653 14,1% 56,2%"
CO_ROW = re.compile(
    r"^\d+\s+([A-Za-z\xc0-\xff][A-Za-z\xc0-\xff .\-]+?)\s+([\d.]+)\s+([\d.]+)\s+"
    r"[\d.,]+\s*%\s+-?[\d.,]+\s*%")
CO_PERIODO = re.compile(r"\b(" + "|".join(CO_MESES) + r")\s+(\d{4})", re.IGNORECASE)


def _co_url(anio: int, mes: int) -> str:
    f = f"{mes:02d}. INFORME SECTOR AUTOMOTOR {CO_MMM[mes - 1]}_PRENSA-INDUSTRIA {anio}.pdf"
    return "https://www.andi.com.co/Uploads/" + _urlparse.quote(f)


def parse_colombia_andi(pdf_bytes: bytes) -> tuple[list[tuple], int | None, int | None]:
    """Top-20 marcas del boletín ANDI. Devuelve ([(marca, unid_mes)], anio, mes)."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=2) or ""
            up = text.upper()
            if "TOP 20" not in up or "MARCAS" not in up:
                continue
            anio = mes = None
            for line in text.splitlines()[:8]:
                m = CO_PERIODO.search(line)
                if m:
                    mes = CO_MESES[m.group(1).lower()]
                    anio = int(m.group(2))
                    break
            out = []
            for line in text.splitlines():
                if line.upper().startswith(("OTRAS", "TOTAL")):
                    continue
                m = CO_ROW.match(line.strip())
                if m:
                    marca = canon(m.group(1).strip())
                    out.append((marca, int(m.group(2).replace(".", ""))))
            if out:
                return out, anio, mes
    return [], None, None


FENALCO_INDEX = "https://www.fenalco.com.co/blog/gremial-4"


def _fenalco_posts() -> list[str]:
    """URLs de posts 'Informe del Sector Automotor' del blog de Fenalco (más nuevos primero)."""
    from urllib.parse import urljoin
    try:
        r = requests.get(FENALCO_INDEX, headers=HEADERS, timeout=25)
        r.raise_for_status()
    except Exception as e:
        print(f"  [CO discover] índice Fenalco falló: {e}")
        return []
    seen, urls = set(), []
    for m in re.finditer(r'href="([^"]*informe-del-sector-automotor-a-[^"]+)"', r.text, re.I):
        u = urljoin(FENALCO_INDEX, m.group(1))
        if u not in seen and "vehiculos-electricos" not in u and "motocicletas" not in u:
            seen.add(u)
            urls.append(u)
    return urls


def _drive_pdf_from_post(post_url: str) -> bytes | None:
    """Extrae el ID de Google Drive del post de Fenalco y baja el PDF."""
    try:
        r = requests.get(post_url, headers=HEADERS, timeout=25)
        m = re.search(r"drive\.google\.com/file/d/([\w-]+)", r.text)
        if not m:
            return None
    except Exception as e:
        print(f"  [CO discover] post {post_url}: {e}")
        return None
    return fetch_pdf(f"https://drive.google.com/uc?export=download&id={m.group(1)}")


def ingest_colombia(backfill: bool = False) -> list[dict]:
    """Serie mensual de Colombia desde el boletín ANDI (unidades del mes -> acum cumsum).

    Dos fuentes del mismo boletín:
      1) URL directa andi.com.co/Uploads (patrón estable, 2025 + ene-2026).
      2) Posts recientes de Fenalco que alojan el PDF en Google Drive (2026+).
    """
    existing = _load_monthly_csv("colombia_mensual.csv")
    known = {(int(r["anio"]), int(r["mes"])) for r in existing}
    mensual = {(int(r["anio"]), int(r["mes"]), r["marca"]): int(r["unid_mes"])
               for r in existing if r.get("unid_mes") not in ("", None)}

    candidates = []
    for anio in (2025, 2026):
        for mes in range(1, 13):
            if datetime(anio, mes, 1) > datetime.now():
                break
            candidates.append((anio, mes))
    if not backfill:
        candidates = [(a, m) for a, m in candidates if (a, m) not in known]

    # 1) Patrón de URL directa (andi.com.co/Uploads) — meses con archivo en ese path
    for anio, mes in candidates:
        pdf = fetch_pdf(_co_url(anio, mes))
        if not pdf:
            continue
        filas, y, mo = parse_colombia_andi(pdf)
        if not filas:
            print(f"  [WARN] Colombia {anio}-{mes:02d}: sin tabla de marcas")
            continue
        if y and mo and (y, mo) != (anio, mes):
            print(f"  [WARN] Colombia {anio}-{mes:02d}: período detectado {y}-{mo:02d} != esperado")
        for marca, unid in filas:
            mensual[(anio, mes, marca)] = unid
        print(f"  ->Colombia {anio}-{mes:02d}: {len(filas)} marcas (ANDI/Uploads)")
        time.sleep(SLEEP)

    # 2) Sweep de posts recientes de Fenalco (PDF en Google Drive) para los que faltan.
    #    El período real se lee del PDF (los slugs a veces están mal etiquetados).
    covered = {(a, m) for (a, m, _) in mensual}
    for post in _fenalco_posts()[:8]:
        pdf = _drive_pdf_from_post(post)
        if not pdf:
            continue
        filas, y, mo = parse_colombia_andi(pdf)
        if not (filas and y and mo):
            continue
        if (y, mo) in covered:
            if not backfill:
                break  # más nuevos primero: si éste ya está, los siguientes también
            continue
        for marca, unid in filas:
            mensual[(y, mo, marca)] = unid
        covered.add((y, mo))
        print(f"  ->Colombia {y}-{mo:02d}: {len(filas)} marcas (Fenalco/Drive)")
        time.sleep(SLEEP)

    if not mensual:
        print("  Colombia: sin datos.")
        return existing

    # unid_mes directo de ANDI; unid_acum = suma corrida dentro del año
    by_marca = defaultdict(list)
    for (a, m, marca), um in mensual.items():
        by_marca[marca].append((a, m, um))
    rows = []
    for marca, pts in by_marca.items():
        pts.sort()
        run = {}
        for a, m, um in pts:
            run[a] = run.get(a, 0) + um
            rows.append({"pais": "Colombia", "anio": a, "mes": m, "marca": marca,
                         "unid_acum": run[a], "unid_mes": um})
    rows.sort(key=lambda r: (r["anio"], r["mes"], r["marca"]))
    return rows


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

def ingest_aladda():
    """Refresca data/aladda_top10.csv desde el PDF regional de ALADDA (si hay red).

    Si la descarga falla (sandbox sin red, índice caído), se mantiene el CSV
    existente. El merge a base_nacional usa ese CSV igual.
    """
    try:
        import parse_aladda as pa
        year, month = pa.discover_latest()
        text = pa.fetch_pdf_text(year, month)
        parsed = pa.parse_text(text)
        label = f"{pa.MMM[month - 1].capitalize()} {year}"
        pa.write_csvs(parsed, periodo_label=label)
        print(f"  ALADDA actualizado: {label} ({len(parsed['totals'])} países)")
    except Exception as e:
        print(f"  ALADDA: uso el CSV existente (no pude refrescar: {e})")


# Países sin serie mensual propia: su Tendencia/MoM sale de ALADDA histórico.
# (Colombia ya no: tiene fuente propia, el boletín mensual de ANDI.)
ALADDA_SOLO_MENSUAL = {"Costa Rica", "Guatemala", "Panama", "Rep. Dominicana"}


def ingest_aladda_mensual(backfill: bool = False, anios=(2026,)):
    """Serie mensual de los países SIN parser propio, desde el histórico de ALADDA.

    Cada informe ALADDA trae el acumulado Ene-{mes} por marca; juntando meses y
    restando se obtiene la unidad mensual (igual lógica que CAVEM/AAP). Sólo cubre
    Colombia/Costa Rica/Guatemala/Panamá/Rep. Dominicana (CL/PE/EC usan su parser,
    más profundo). Incremental: salta los meses ya presentes salvo --backfill.
    """
    import parse_aladda as pa
    existing = _load_monthly_csv("aladda_mensual.csv")
    known = {(int(r["anio"]), int(r["mes"])) for r in existing}

    # Meses candidatos: ene..(último disponible) de cada año pedido
    try:
        ly, lm = pa.discover_latest()
    except Exception:
        ly, lm = 2026, 5
    candidates = []
    for y in anios:
        last = lm if y == ly else 12
        for m in range(1, last + 1):
            if datetime(y, m, 1) > datetime.now():
                break
            candidates.append((y, m))
    if not backfill:
        candidates = [(y, m) for y, m in candidates if (y, m) not in known]

    # combinado por país: {(anio,mes,marca): acum}
    combined = {p: {} for p in ALADDA_SOLO_MENSUAL}
    for r in existing:  # arranco con lo ya bajado
        if r["pais"] in combined:
            combined[r["pais"]][(int(r["anio"]), int(r["mes"]), r["marca"])] = int(r["unid_acum"])

    import io as _io
    import pdfplumber as _pp
    for (y, m) in candidates:
        try:
            pdf_bytes = pa.fetch_pdf_bytes(y, m)
        except Exception as e:
            print(f"  [WARN] ALADDA mensual {y}-{m:02d}: {e}")
            continue
        # Hibrido: tablas (extract_tables) + texto (parse_text). Cada mes uno u otro
        # funciona mejor; unimos para maximizar cobertura de los 5 países objetivo.
        mes_data = {p: {} for p in ALADDA_SOLO_MENSUAL}  # pais -> {marca: acum}
        try:
            for pais, marcas in pa.parse_marca_tables(pdf_bytes).items():
                if pais in mes_data:
                    for marca, acum in marcas.items():
                        mes_data[pais][canon(marca)] = acum
        except Exception as e:
            print(f"  [WARN] tablas {y}-{m:02d}: {e}")
        try:
            with _pp.open(_io.BytesIO(pdf_bytes)) as _pdf:
                _txt = "\n".join((p.extract_text() or "") for p in _pdf.pages)
            for r in pa.parse_text(_txt)["top10"]:
                if r["pais"] in mes_data:
                    mes_data[r["pais"]].setdefault(canon(r["marca"]), r["acum_2026"])
        except Exception as e:
            print(f"  [WARN] texto {y}-{m:02d}: {e}")
        n = 0
        for pais, marcas in mes_data.items():
            for marca, acum in marcas.items():
                combined[pais][(y, m, marca)] = acum
                n += 1
        cubiertos = sum(1 for p, mk in mes_data.items() if mk)
        print(f"  ->ALADDA mensual {y}-{m:02d}: {n} filas, {cubiertos}/5 países objetivo")
        time.sleep(SLEEP)

    rows = []
    for pais, comb in combined.items():
        if comb:
            rows += _build_monthly_series(comb, pais)
    rows.sort(key=lambda r: (r["pais"], r["anio"], r["mes"], r["marca"]))
    save_monthly_csv(rows, "aladda_mensual.csv")
    return rows


def rebuild_base_nacional(*_ignored):
    """Reconstruye base_nacional.csv/.json desde ALADDA (8 países, último corte).

    ALADDA = amplitud (top-10 por país, 8 países objetivo, fresco). Las series
    mensuales propias (CAVEM/AAP/AEADE) se mantienen para Tendencia/H2/filtro de
    período; este snapshot es el "Último (por país)". Aplica canon() a las marcas.
    Acepta args posicionales por compatibilidad con la firma vieja (se ignoran).
    """
    aladda = _load_monthly_csv("aladda_top10.csv")  # reusa el lector de CSV
    final_json = []
    for r in aladda:
        up_raw = r.get("unidades_prev")
        var_raw = r.get("var_yoy_pct")
        final_json.append({
            "pais": r["pais"],
            "periodo": r["periodo"],
            "marca": canon(r["marca"]),
            "uc": int(r["unidades_curr"]),
            "up": int(up_raw) if up_raw not in (None, "", "n/d") else None,
            "var": float(var_raw) if var_raw not in (None, "", "n/d") else None,
            "fuente": r.get("fuente", "ALADDA (AMDA)"),
        })

    # CSV legible (claves largas) + JSON para el dashboard (claves cortas)
    fields = ["pais", "periodo", "marca", "unidades_curr", "unidades_prev", "var_yoy_pct", "fuente"]
    with open(DATA / "base_nacional.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in final_json:
            w.writerow({
                "pais": r["pais"], "periodo": r["periodo"], "marca": r["marca"],
                "unidades_curr": r["uc"],
                "unidades_prev": r["up"] if r["up"] is not None else "",
                "var_yoy_pct": r["var"] if r["var"] is not None else "",
                "fuente": r["fuente"],
            })
    with open(DATA / "base_nacional.json", "w", encoding="utf-8") as f:
        json.dump(final_json, f, ensure_ascii=False, indent=2)

    paises = sorted({r["pais"] for r in final_json})
    print(f"  base_nacional: {len(final_json)} filas · {len(paises)} países ({', '.join(paises)})")


def rebuild_trend_json(peru_rows: list[dict], chile_rows: list[dict]):
    """Reconstruye trend.json con la forma que espera el dashboard:
        {"series": {marca: [{"ym": "YYYY-MM", "u": unid_mes, "acc": unid_acum}, ...]}}

    La tab Tendencia del dashboard es de Perú; se arma desde peru_rows.
    `u` = unidades del mes (puede ser None si no se pudo calcular el delta);
    `acc` = acumulado del año (siempre presente; lo usa el momentum Q1 YoY).
    """
    series: dict[str, list[dict]] = {}
    for r in peru_rows:
        um = r["unid_mes"]
        u_val = int(um) if um not in ("", None) else None
        ym = f'{int(r["anio"])}-{int(r["mes"]):02d}'
        series.setdefault(r["marca"], []).append({
            "ym": ym,
            "u": u_val,
            "acc": int(r["unid_acum"]),
        })
    for marca in series:
        series[marca].sort(key=lambda p: p["ym"])

    with open(DATA / "trend.json", "w", encoding="utf-8") as f:
        json.dump({"series": series}, f, ensure_ascii=False, indent=2)
    print(f"  trend.json: {len(series)} marcas (Perú)")


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

    # Forma que espera el dashboard: {pais: [{"ym": "YYYY-MM", "pct": n}, ...]}
    result: dict[str, list[dict]] = {}
    for (pais, anio, mes), v in sorted(data.items()):
        pct = round(v["chinas"] / v["total"] * 100, 1) if v["total"] else 0
        result.setdefault(pais, []).append({"ym": f"{anio}-{mes:02d}", "pct": pct})
    with open(DATA / "china_tl.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    npts = sum(len(v) for v in result.values())
    print(f"  china_tl.json: {npts} puntos en {len(result)} países")


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
    ap.add_argument("--pais", default="all", choices=["chile", "peru", "ecuador", "colombia", "all"])
    ap.add_argument("--backfill", action="store_true", help="Reprocesar todos los meses conocidos")
    args = ap.parse_args()

    run_peru     = args.pais in ("peru",     "all")
    run_chile    = args.pais in ("chile",    "all")
    run_ecuador  = args.pais in ("ecuador",  "all")
    run_colombia = args.pais in ("colombia", "all")

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
        # Lista completa de marcas (cola larga), aislada: si falla no afecta lo de arriba
        try:
            chile_full = ingest_chile_full(backfill=args.backfill)
            save_monthly_csv(chile_full, "chile_full_mensual.csv")
        except Exception as e:
            print(f"  [WARN] Chile lista completa: {e}")

    if run_ecuador:
        print("\n=== ECUADOR ===")
        try:
            ecuador_rows = ingest_ecuador(backfill=args.backfill)
            save_monthly_csv(ecuador_rows, "ecuador_mensual.csv")
            report["paises"]["Ecuador"] = _country_report(ecuador_rows, prev["Ecuador"])
        except Exception as e:
            print(f"  [ERROR] Ecuador: {e}")
            report["paises"]["Ecuador"] = {"ejecutado": True, "error": str(e)}

    if run_colombia:
        print("\n=== COLOMBIA (ANDI) ===")
        co_prev = _periodos(_load_monthly_csv("colombia_mensual.csv"))
        try:
            colombia_rows = ingest_colombia(backfill=args.backfill)
            save_monthly_csv(colombia_rows, "colombia_mensual.csv")
            report["paises"]["Colombia"] = _country_report(colombia_rows, co_prev)
        except Exception as e:
            print(f"  [ERROR] Colombia: {e}")
            report["paises"]["Colombia"] = {"ejecutado": True, "error": str(e)}

    print("\n=== ALADDA (regional, 8 países) ===")
    ingest_aladda()
    try:
        ingest_aladda_mensual(backfill=args.backfill)   # serie mensual CO/CR/GT/PA/RD (incremental)
    except Exception as e:
        print(f"  [WARN] ALADDA mensual: {e}")

    print("\n=== Reconstruyendo JSONs ===")
    rebuild_base_nacional()  # snapshot desde ALADDA (8 países)
    rebuild_trend_json(peru_rows, chile_rows)   # series mensuales propias (Tendencia/H2)
    rebuild_china_tl(peru_rows, chile_rows)

    print("\n=== Regenerando dashboard ===")
    import subprocess
    subprocess.run([sys.executable, str(SCRIPTS / "build_dashboard.py")], check=True)

    _write_report(report)
    print("\nListo.")


if __name__ == "__main__":
    main()
