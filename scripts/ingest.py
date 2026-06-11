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
from bs4 import BeautifulSoup

ROOT   = Path(__file__).resolve().parents[1]
DATA   = ROOT / "data"
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from sources import (
    PERU, CHILE, ECUADOR,
    MESES_ES, MESES_PE,
    MARCAS_CHINAS,
)

HEADERS = {"User-Agent": "Mozilla/5.0 (AndinaCopilot/1.0; +https://github.com/faculopezp/AndinaCopilot)"}
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
# PERU — parser de PDF de AAP
# ---------------------------------------------------------------------------

PERU_SEG = ["Automóviles, SW", "Pick up, furgonetas", "Camionetas", "SUV, todoterreno"]
PERU_HDR = re.compile(r"^Rank\. Marca (\d{4}) (\d{4}) Var\.% Part\.%")
PERU_ROW = re.compile(r"^(\d+)\s+(.+?)\s+([\d,]+)\s+([\d,]+)\s+(-?[\d.]+)%\s+([\d.]+)%$")


def parse_peru_pdf(lines: list[str]) -> tuple[list[tuple], int | None]:
    """Parsea las tablas de segmentos del PDF mensual de AAP.

    Devuelve (filas, anio_acum) donde filas = [(segmento, marca, acum_curr, acum_prev), ...].
    anio_acum es el año del acumulado (columna derecha de la tabla).
    """
    # Localizar bloque de segmentos
    gi = None
    for i in range(len(lines) - 3):
        if all(lines[i + k] == PERU_SEG[k] for k in range(4)):
            gi = i
            break
    if gi is None:
        return [], None

    out = []
    anio_acum = None
    si = 0
    k  = gi + 4

    while si < 4 and k < len(lines):
        hm = PERU_HDR.match(lines[k])
        if hm:
            anio_acum = int(hm.group(2))
            k += 1
            while k < len(lines):
                m = PERU_ROW.match(lines[k])
                if m:
                    _, marca, prev_s, curr_s, *_ = m.groups()
                    out.append((
                        PERU_SEG[si],
                        marca.strip(),
                        int(curr_s.replace(",", "")),
                        int(prev_s.replace(",", "")),
                    ))
                    k += 1
                elif lines[k].startswith("Total"):
                    k += 1
                    break
                elif lines[k].startswith("Otros"):
                    k += 1
                else:
                    if out:
                        break
                    k += 1
            si += 1
        else:
            k += 1

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

    # Meses con URL conocida
    candidates = []
    for anio in [2025, 2026]:
        for mes in range(1, 13):
            if datetime(anio, mes, 1) > datetime.now():
                break
            candidates.append((anio, mes))

    if not backfill:
        candidates = [(a, m) for a, m in candidates if (a, m) not in known]

    new_rows: dict[tuple, dict] = {}  # (anio,mes,marca) -> {acum}

    for anio, mes in candidates:
        url = peru_url(anio, mes)
        if not url:
            print(f"  [SKIP] Perú {anio}-{mes:02d}: URL no conocida (agregar a sources.py)")
            continue
        print(f"  → Perú {anio}-{mes:02d}  {url}")
        pdf = fetch_pdf(url)
        if not pdf:
            continue
        lines = pdf_to_lines(pdf)
        filas, yr = parse_peru_pdf(lines)
        if not filas:
            print(f"  [WARN] Perú {anio}-{mes:02d}: parse sin resultados")
            continue
        if yr != anio:
            print(f"  [WARN] Perú {anio}-{mes:02d}: año detectado {yr} ≠ {anio}")
            continue
        # Sumar segmentos -> nacional
        acum_por_marca: dict[str, int] = defaultdict(int)
        for _, marca, acum, _ in filas:
            acum_por_marca[marca] += acum
        for marca, acum in acum_por_marca.items():
            new_rows[(anio, mes, marca)] = acum
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

CHILE_HDR = re.compile(r"Marca\s+(\d{4})\s+(\d{4})\s+Var\.\s*%", re.IGNORECASE)
CHILE_ROW = re.compile(r"^(\d+)\s+(.+?)\s+([\d.]+)\s+([\d.]+)\s+(-?[\d.]+)\s*%?$")


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


def parse_chile_pdf(lines: list[str]) -> list[tuple]:
    """Extrae ranking acumulado del PDF de CAVEM.

    Devuelve [(marca, acum_curr, acum_prev), ...].
    El PDF incluye tablas mensuales y acumuladas; usamos la acumulada
    (la primera tabla Rank./Marca que aparece con columnas de año).
    """
    out = []
    in_table = False
    for line in lines:
        if CHILE_HDR.search(line):
            in_table = True
            continue
        if in_table:
            m = CHILE_ROW.match(line)
            if m:
                _, marca, curr_s, prev_s, *_ = m.groups()
                try:
                    curr = int(curr_s.replace(".", ""))
                    prev = int(prev_s.replace(".", ""))
                    out.append((marca.strip(), curr, prev))
                except ValueError:
                    pass
            elif line.startswith("Total") or line.startswith("TOTAL"):
                break  # fin de la tabla
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
            print(f"  → Chile {anio}-{mes:02d}: descubriendo hash...")
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
        print(f"  → Chile {anio}-{mes:02d}  {url}")
        pdf = fetch_pdf(url)
        if not pdf:
            continue
        lines = pdf_to_lines(pdf)
        filas = parse_chile_pdf(lines)
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
# ECUADOR — parser de PDF de AEADE/CINAE
# ---------------------------------------------------------------------------

ECUADOR_HDR = re.compile(r"Marca\s+Cantidad", re.IGNORECASE)
ECUADOR_ROW = re.compile(r"^(\d+)\s+(.+?)\s+([\d.,]+)\s+([\d.]+)%$")


def _load_ecuador_ids() -> dict[str, int]:
    return dict(ECUADOR["download_ids"])


def _discover_ecuador_ids(known_ids: dict[str, int]) -> dict[str, int]:
    """Scrapea el índice de AEADE para encontrar download_ids nuevos."""
    new = {}
    try:
        r = requests.get(ECUADOR["indice"], headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            m = re.search(r"download_id=(\d+)", href)
            if m:
                did = int(m.group(1))
                if did not in known_ids.values():
                    # Intentar etiquetar por texto del link
                    label = a.get_text(strip=True)
                    new[label] = did
    except Exception as e:
        print(f"  [ERROR] AEADE índice: {e}")
    return new


def parse_ecuador_pdf(lines: list[str]) -> tuple[list[tuple], str | None]:
    """Extrae Top marcas del boletín de AEADE.

    Devuelve ([(marca, unidades), ...], periodo_str).
    OJO: el boletín del mes M reporta el mes M-1.
    """
    periodo = None
    for line in lines[:30]:
        m = re.search(r"BOLETÍN DE VENTAS\s+(\w+)\s+(\d{4})", line, re.IGNORECASE)
        if m:
            periodo = f"{m.group(1).capitalize()} {m.group(2)}"
            break

    out = []
    in_table = False
    for line in lines:
        if ECUADOR_HDR.search(line):
            in_table = True
            continue
        if in_table:
            m = ECUADOR_ROW.match(line)
            if m:
                _, marca, cant_s, *_ = m.groups()
                try:
                    out.append((marca.strip(), int(cant_s.replace(".", "").replace(",", ""))))
                except ValueError:
                    pass
            elif line.startswith("Total") or line.startswith("TOTAL"):
                break
    return out, periodo


def ingest_ecuador(backfill: bool = False) -> list[dict]:
    existing  = _load_monthly_csv("ecuador_mensual.csv")
    known_ym  = {(int(r["anio"]), int(r["mes"])) for r in existing}
    ids       = _load_ecuador_ids()

    # Descubrir IDs nuevos
    new_ids = _discover_ecuador_ids(ids)
    if new_ids:
        print(f"  Ecuador: {len(new_ids)} posibles IDs nuevos encontrados: {list(new_ids.items())}")
        # No los agregamos automáticamente a sources.py porque requieren validación manual
        # pero sí los procesamos en esta sesión
        ids.update({k: v for k, v in new_ids.items()})

    combined: dict[tuple, int] = {}
    for r in existing:
        combined[(int(r["anio"]), int(r["mes"]), r["marca"])] = int(r["unid_acum"])

    for label, did in ids.items():
        # Parsear etiqueta tipo "2026-01" -> boletin mes M, datos de M-1
        try:
            label_dt = datetime.strptime(label[:7], "%Y-%m")
        except ValueError:
            continue
        # Datos son del mes M-1
        if label_dt.month == 1:
            datos_anio, datos_mes = label_dt.year - 1, 12
        else:
            datos_anio, datos_mes = label_dt.year, label_dt.month - 1

        if not backfill and (datos_anio, datos_mes) in known_ym:
            continue

        url = ECUADOR["descarga"].format(download_id=did)
        print(f"  → Ecuador {datos_anio}-{datos_mes:02d}  (boletín {label}) {url}")
        pdf = fetch_pdf(url)
        if not pdf:
            continue
        lines = pdf_to_lines(pdf)
        filas, _ = parse_ecuador_pdf(lines)
        if not filas:
            print(f"  [WARN] Ecuador {datos_anio}-{datos_mes:02d}: parse sin resultados")
            continue
        for marca, unid in filas:
            combined[(datos_anio, datos_mes, marca)] = unid
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

    if run_peru:
        print("\n=== PERU ===")
        peru_rows = ingest_peru(backfill=args.backfill)
        save_monthly_csv(peru_rows, "peru_nacional_mensual.csv")

    if run_chile:
        print("\n=== CHILE ===")
        chile_rows = ingest_chile(backfill=args.backfill)
        save_monthly_csv(chile_rows, "chile_mensual.csv")

    if run_ecuador:
        print("\n=== ECUADOR ===")
        ecuador_rows = ingest_ecuador(backfill=args.backfill)
        save_monthly_csv(ecuador_rows, "ecuador_mensual.csv")

    print("\n=== Reconstruyendo JSONs ===")
    rebuild_base_nacional(peru_rows, chile_rows, ecuador_rows)
    rebuild_trend_json(peru_rows, chile_rows)
    rebuild_china_tl(peru_rows, chile_rows)

    print("\n=== Regenerando dashboard ===")
    import subprocess
    subprocess.run([sys.executable, str(SCRIPTS / "build_dashboard.py")], check=True)

    print("\nListo.")


if __name__ == "__main__":
    main()
