#!/usr/bin/env python3
"""Directorio de agencias/dealers de Ecuador desde ecuador.patiotuerca.com/dealers.

Fuente pública (marketplace de seminuevos). Son lotes MULTIMARCA de usados, NO
concesionarios oficiales de una marca — por eso van a su propia tabla y NO se
mezclan con concesionarios_*.csv (que son la red oficial de marca de Chile/Perú).

Sirve como base de contactos del mercado ecuatoriano: nombre, ciudad, dirección,
stock, rating y URL del perfil. La marca por dealer (de su inventario) y el
teléfono viven en la ficha; se pueden enriquecer después (una pasada por perfil).

Los datos vienen server-rendered como JSON-LD (`"@type":"AutoDealer"`) en el HTML,
lo que es estable y no requiere navegador. Paginación: ?page=N (12 por página).

Uso:
    python scripts/ingest_dealers_ec.py            # baja todo -> data/dealers_ecuador.csv
    python scripts/ingest_dealers_ec.py --max 3    # solo 3 páginas (prueba)
"""
import argparse
import csv
import json
import pathlib
import re
import sys
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BASE = "https://ecuador.patiotuerca.com/dealers?page={n}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"}

# Ciudad -> provincia (Ecuador). Best-effort para las ciudades frecuentes; el
# resto queda vacío (no inventar). Ampliá el mapa a medida que aparezcan ciudades.
PROVINCIA = {
    "QUITO": "Pichincha", "GUAYAQUIL": "Guayas", "CUENCA": "Azuay",
    "AMBATO": "Tungurahua", "MANTA": "Manabí", "PORTOVIEJO": "Manabí",
    "MACHALA": "El Oro", "LOJA": "Loja", "RIOBAMBA": "Chimborazo",
    "IBARRA": "Imbabura", "SANTO DOMINGO": "Santo Domingo de los Tsáchilas",
    "DURAN": "Guayas", "DURÁN": "Guayas", "LATACUNGA": "Cotopaxi",
    "ESMERALDAS": "Esmeraldas", "QUEVEDO": "Los Ríos", "BABAHOYO": "Los Ríos",
    "MILAGRO": "Guayas", "SALINAS": "Santa Elena", "LA LIBERTAD": "Santa Elena",
    "OTAVALO": "Imbabura", "TULCAN": "Carchi", "TULCÁN": "Carchi",
    "SANGOLQUI": "Pichincha", "SANGOLQUÍ": "Pichincha",
}


def fetch(url: str, retries: int = 4) -> str | None:
    for a in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=45)
            if r.status_code == 200 and "AutoDealer" in r.content.decode("utf-8", "ignore"):
                return r.content.decode("utf-8", "ignore")  # forzar UTF-8 (evita mojibake en ñ)
            if r.status_code == 404:
                return None
        except requests.RequestException:
            pass
        time.sleep(2 + a)
    return None


def extract_dealers(html: str) -> list[dict]:
    """Saca los objetos JSON-LD AutoDealer por balanceo de llaves (robusto ante
    variaciones del render)."""
    out = []
    for m in re.finditer(r'\{"@type":"AutoDealer"', html):
        i, depth = m.start(), 0
        for j in range(i, min(i + 4000, len(html))):
            if html[j] == "{":
                depth += 1
            elif html[j] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        out.append(json.loads(html[i:j + 1]))
                    except json.JSONDecodeError:
                        pass
                    break
    return out


def _split_address(street: str) -> tuple[str, str, str]:
    """'AV. X 1-2, QUITO, Ecuador' -> (direccion, ciudad, provincia)."""
    parts = [p.strip() for p in street.split(",") if p.strip()]
    ciudad = provincia = ""
    if parts and parts[-1].lower() in ("ecuador", "ec"):
        parts = parts[:-1]
    if parts:
        ciudad = parts[-1]
        provincia = PROVINCIA.get(ciudad.upper(), "")
        direccion = ", ".join(parts[:-1]) if len(parts) > 1 else ""
    else:
        direccion = street
    # normalizar ciudad a Title Case si viene toda en mayúsculas
    ciudad_norm = ciudad.title() if ciudad.isupper() else ciudad
    return re.sub(r"\s{2,}", " ", direccion).strip(), ciudad_norm.strip(), provincia


def parse_dealer(d: dict) -> dict:
    url = d.get("url", "")
    m = re.search(r"/dealers-profile/([^/]+)/(\d+)", url)
    slug, did = (m.group(1), m.group(2)) if m else ("", "")
    street = (d.get("address") or {}).get("streetAddress", "") or ""
    direccion, ciudad, provincia = _split_address(street)
    rating = reviews = ""
    ar = d.get("aggregateRating")
    if isinstance(ar, dict):
        rating = ar.get("ratingValue", "")
        reviews = ar.get("reviewCount", "")
    return {
        "pais": "Ecuador", "dealer_id": did, "dealer": d.get("name", "").strip(),
        "ciudad": ciudad, "provincia": provincia, "direccion": direccion,
        "rating": rating, "reviews": reviews, "url": url,
    }


def _vehiculo_counts(html: str) -> list[int]:
    """'32 vehículos disponibles' en orden de aparición (mismo orden que los dealers)."""
    return [int(x.replace(".", "")) for x in
            re.findall(r"([\d.]+)\s+veh[íi]culos?\s+disponibles", html)]


_FX = ('<script type="application/ld+json">{"@type":"ItemList","itemListElement":'
       '[{"item":{"@type":"AutoDealer","name":"AG Autos","url":'
       '"https://ecuador.patiotuerca.com/dealers-profile/ag-autos/1194","address":'
       '{"@type":"PostalAddress","streetAddress":"Av. 12 de Abril, CUENCA, Ecuador",'
       '"addressCountry":"EC"},"aggregateRating":{"@type":"AggregateRating",'
       '"ratingValue":5,"reviewCount":1}}}]}</script>'
       '<div class="card">32 vehículos disponibles</div>')


def selftest() -> int:
    """Valida la extracción contra un fixture (sin red). Caza el format drift."""
    ds = extract_dealers(_FX)
    assert len(ds) == 1, ds
    row = parse_dealer(ds[0])
    counts = _vehiculo_counts(_FX)
    row["vehiculos"] = counts[0] if counts else ""
    ok = (row["dealer_id"] == "1194" and row["dealer"] == "AG Autos"
          and row["ciudad"] == "Cuenca" and row["provincia"] == "Azuay"
          and str(row["rating"]) == "5" and row["vehiculos"] == 32)
    if not ok:
        print("SELFTEST patiotuerca FALLÓ:", row, counts)
        return 1
    print("selftest patiotuerca OK:", row["dealer"], row["ciudad"], row["provincia"],
          f"{row['vehiculos']} veh")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=40, help="tope de páginas (seguridad)")
    ap.add_argument("--selftest", action="store_true", help="valida el parser sin red y sale")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())

    seen, rows = set(), []
    for n in range(1, args.max + 1):
        html = fetch(BASE.format(n=n))
        if not html:
            print(f"  página {n}: sin respuesta, corto.")
            break
        dealers = extract_dealers(html)
        counts = _vehiculo_counts(html)
        if not dealers:
            print(f"  página {n}: 0 dealers, corto.")
            break
        nuevos = 0
        for k, d in enumerate(dealers):
            row = parse_dealer(d)
            if not row["dealer_id"] or row["dealer_id"] in seen:
                continue
            seen.add(row["dealer_id"])
            row["vehiculos"] = counts[k] if k < len(counts) else ""
            rows.append(row)
            nuevos += 1
        print(f"  página {n}: {len(dealers)} dealers ({nuevos} nuevos) · total {len(rows)}")
        if nuevos == 0:
            break
        time.sleep(1)

    rows.sort(key=lambda r: r["dealer"].lower())
    cols = ["pais", "dealer_id", "dealer", "ciudad", "provincia", "direccion",
            "vehiculos", "rating", "reviews", "url"]
    out = DATA / "dealers_ecuador.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    con_prov = sum(1 for r in rows if r["provincia"])
    print(f"OK -> {out}: {len(rows)} agencias · {con_prov} con provincia")
    ciudades = {}
    for r in rows:
        ciudades[r["ciudad"]] = ciudades.get(r["ciudad"], 0) + 1
    top = sorted(ciudades.items(), key=lambda x: -x[1])[:6]
    print("  por ciudad:", ", ".join(f"{c}={n}" for c, n in top))


if __name__ == "__main__":
    main()
