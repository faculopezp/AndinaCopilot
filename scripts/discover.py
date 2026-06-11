#!/usr/bin/env python3
"""Descubrimiento automático de informes nuevos por país (índices oficiales).

Resuelve la URL/ID del último informe de cada país leyendo su página índice,
de modo que el pipeline no dependa de URLs hardcodeadas en sources.py.

Funciones:
    discover_peru_urls()    -> dict[(anio, mes), url]
    discover_ecuador_ids()  -> list[int]   (orden: más nuevo primero)
    discover_chile_hash(a,m)-> str | None  (ya existía en ingest; se re-expone)

Diseño:
    - Perú y Ecuador: scrapean el índice en vivo. Si falla, el caller usa el
      fallback hardcodeado de sources.py.
    - El año/mes definitivo de cada informe lo confirma el parser desde el PDF
      (parse_peru_pdf / parse_ecuador_pdf), así que el discovery solo necesita
      acercar el candidato correcto.
"""

import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from sources import PERU, ECUADOR, CHILE, MESES_ES  # noqa: E402

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*",
}

# Nombre de mes (es) -> número. Incluye variantes que usa AAP ("Setiembre").
_MES_NUM = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9,
    "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def _mes_de(texto: str) -> int | None:
    t = texto.strip().lower()
    for nombre, num in _MES_NUM.items():
        if nombre in t:
            return num
    return None


# ---------------------------------------------------------------------------
# PERÚ — índice AAP
# ---------------------------------------------------------------------------

def discover_peru_urls() -> dict[tuple, str]:
    """Lee el índice de AAP y devuelve {(anio, mes): url_pdf}.

    Reglas de la página:
      - /storage/estadisticas/{AÑO}/{Mes}.pdf   -> año explícito en la ruta
      - .../informes-mensuales/...              -> año actual (sin año en la URL);
        se infiere como (máximo año con ruta datada) + 1.
    """
    url = PERU["indice"]
    result: dict[tuple, str] = {}
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        r.raise_for_status()
    except Exception as e:
        print(f"  [discover] Perú índice falló: {e}")
        return result

    soup = BeautifulSoup(r.text, "html.parser")
    dated = []          # (anio, mes, href)
    current_year = []   # (mes, href)  -- sin año en la URL

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".pdf" not in href.lower():
            continue
        if not href.startswith("http"):
            href = "https://aap.org.pe" + href

        m_dated = re.search(r"/estadisticas/(\d{4})/([A-Za-zÁ-úñ]+)\.pdf", href)
        if m_dated:
            anio = int(m_dated.group(1))
            mes = _mes_de(m_dated.group(2))
            if mes:
                dated.append((anio, mes, href))
            continue

        if "informes-mensuales" in href:
            mes = _mes_de(a.get_text(strip=True))
            if mes:
                current_year.append((mes, href))

    # Cargar lo datado
    for anio, mes, href in dated:
        result[(anio, mes)] = href

    # Bloque "current" (sin año en la URL): puede cruzar el cambio de año.
    # El índice va del más nuevo al más viejo, así que los meses DECRECEN;
    # cuando un mes es >= al anterior, cruzamos hacia atrás un año.
    if current_year:
        max_anio = max((a for a, _, _ in dated), default=None)
        anio = (max_anio + 1) if max_anio else None
        if anio:
            prev_mes = 13
            for mes, href in current_year:
                if mes >= prev_mes:
                    anio -= 1
                prev_mes = mes
                result[(anio, mes)] = href

    return result


# ---------------------------------------------------------------------------
# ECUADOR — índice AEADE
# ---------------------------------------------------------------------------

def discover_ecuador_ids() -> list[int]:
    """Lee el índice de AEADE y devuelve los download_id en orden (más nuevo primero).

    Los links no traen etiqueta de mes; el período lo resuelve parse_ecuador_pdf
    desde el contenido del PDF. Devolvemos los IDs en el orden del índice
    (AEADE publica el más reciente arriba).
    """
    url = ECUADOR["indice"]
    ids: list[int] = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        r.raise_for_status()
    except Exception as e:
        print(f"  [discover] Ecuador índice falló: {e}")
        return ids

    soup = BeautifulSoup(r.text, "html.parser")
    seen = set()
    for a in soup.find_all("a", href=True):
        m = re.search(r"download_id=(\d+)", a["href"])
        if m:
            did = int(m.group(1))
            if did not in seen:
                seen.add(did)
                ids.append(did)
    return ids


# ---------------------------------------------------------------------------
# CHILE — descubrimiento de hash por mes (página con slug predecible)
# ---------------------------------------------------------------------------

def discover_chile_hash(anio: int, mes: int) -> str | None:
    """Abre la página del mes en CAVEM y extrae el hash del PDF."""
    nombre_mes = MESES_ES[mes - 1]
    url = CHILE["pagina_mes"].format(mes=nombre_mes, anio=anio)
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        m = re.search(CHILE["pdf_regex"], r.text)
        if m:
            return m.group(1)
    except Exception as e:
        print(f"  [discover] CAVEM {anio}-{mes:02d}: {e}")
    return None


if __name__ == "__main__":
    print("=== PERÚ (índice en vivo) ===")
    pe = discover_peru_urls()
    for (a, m), u in sorted(pe.items(), reverse=True)[:8]:
        print(f"  {a}-{m:02d}  {u}")
    print(f"  total: {len(pe)} meses")

    print("\n=== ECUADOR (índice en vivo) ===")
    ec = discover_ecuador_ids()
    print(f"  {len(ec)} download_ids; primeros: {ec[:8]}")
