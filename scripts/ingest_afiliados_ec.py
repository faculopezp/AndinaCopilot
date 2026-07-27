#!/usr/bin/env python3
"""Afiliados de AEADE (Ecuador) -> data/afiliados_aeade_ec.csv

Fuente OFICIAL: https://www.aeade.net/afiliados/  (la misma asociación de los PDFs
de ventas). Son importadores/distribuidores y concesionarios OFICIALES de marca,
con la(s) marca(s) que representan + dirección + ciudad + teléfono + web.

Es la capa de mayor calidad para el flujo marca -> importador -> red -> contacto.
Complementa (no reemplaza) a dealers_ecuador.csv (patiotuerca = lotes de usados
multimarca). Aquí: quién representa cada marca en Ecuador y cómo contactarlo.

La página son tablas de Visual Composer (una por categoría). Columnas:
  1 empresa · 2 líneas de negocio (marca) · 3 dirección · 4 ciudad · 5 teléfono · 6 web

Uso:
    python scripts/ingest_afiliados_ec.py
"""
import csv
import pathlib
import re
import sys
import time

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sources import canon, MARCAS_CHINAS, ALIAS

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
URL = "https://www.aeade.net/afiliados/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

# Orden de las tablas = orden de las pestañas. La última ("Todos") es la unión -> se ignora.
CATEGORIAS = ["Importador", "Concesionario", "Repuestos", "Neumaticos",
              "Motos", "Taller", "Financiera", "Otras"]

PROVINCIA = {
    "QUITO": "Pichincha", "GUAYAQUIL": "Guayas", "CUENCA": "Azuay",
    "AMBATO": "Tungurahua", "MANTA": "Manabí", "PORTOVIEJO": "Manabí",
    "MACHALA": "El Oro", "LOJA": "Loja", "RIOBAMBA": "Chimborazo",
    "IBARRA": "Imbabura", "SANTO DOMINGO": "Santo Domingo de los Tsáchilas",
    "DURAN": "Guayas", "LATACUNGA": "Cotopaxi", "ESMERALDAS": "Esmeraldas",
    "QUEVEDO": "Los Ríos", "SALINAS": "Santa Elena",
}


def _strip_accents(s: str) -> str:
    import unicodedata as ud
    return "".join(c for c in ud.normalize("NFD", s) if ud.category(c) != "Mn")


def fetch(retries: int = 4) -> str | None:
    for a in range(retries):
        try:
            r = requests.get(URL, headers=HEADERS, timeout=45)
            if r.status_code == 200 and "column-1" in r.content.decode("utf-8", "ignore"):
                return r.content.decode("utf-8", "ignore")
        except requests.RequestException:
            pass
        time.sleep(2 + a)
    return None


def _load_marca_vocab() -> dict[str, str]:
    """{forma_superficie_UPPER_sin_acentos: marca_canon}. Sale de la base + alias."""
    surfaces: set[str] = set(MARCAS_CHINAS)
    for fn, col in [("base_nacional.csv", "marca"), ("grupos_importadores.csv", "marca")]:
        p = DATA / fn
        if p.exists():
            with open(p, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row.get(col):
                        surfaces.add(row[col])
    # marcas frecuentes en Ecuador que quizá aún no estén en la base
    surfaces |= {"Kia", "Chevrolet", "Toyota", "Hyundai", "Nissan", "Renault",
                 "Mazda", "Volkswagen", "Ford", "Suzuki", "Honda", "Mitsubishi",
                 "Great Wall", "Chery", "Dongfeng", "Jac", "Jmc", "Jetour",
                 "Changan", "Byd", "Foton", "Hino", "Volvo", "Mercedes Benz",
                 "Bmw", "Audi", "Peugeot", "Citroen", "Fiat", "Jeep", "Ram",
                 "Subaru", "Baic", "Maxus", "Shineray", "Daihatsu"}
    vocab: dict[str, str] = {}
    for s in surfaces:
        vocab[_strip_accents(s).upper()] = canon(s)
    for alias_key, target in ALIAS.items():          # "GREAT WALL" -> "Great Wall"
        vocab[_strip_accents(alias_key).upper()] = target
    return vocab


def _detect_marcas(texto: str, vocab: dict[str, str]) -> str:
    up = _strip_accents(texto).upper()
    found = []
    for surface, canonical in vocab.items():
        if len(surface) < 2:
            continue
        if re.search(r"\b" + re.escape(surface) + r"\b", up):
            if canonical not in found:
                found.append(canonical)
    # priorizar frases largas: si está "Great Wall", no hace falta nada extra; el set ya dedup
    return "; ".join(sorted(found))


def _cell_text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def parse_table(table_html: str, categoria: str, vocab: dict) -> list[dict]:
    rows = re.findall(r"<tr.*?</tr>", table_html, re.DOTALL)
    out = []
    for rw in rows:
        tds = re.findall(r'<td class="column-\d+">(.*?)</td>', rw, re.DOTALL)
        if len(tds) < 2:
            continue
        cols = [_cell_text(td) for td in tds]
        empresa = cols[0]
        # col1 puede ser un logo (texto vacío): recuperar nombre del alt o del filename del src
        if not empresa:
            alt = re.search(r'alt="([^"]+)"', tds[0])
            if alt and alt.group(1).strip():
                empresa = alt.group(1).strip()
            else:
                src = re.search(r'src="[^"]+/([^/"]+)\.(?:png|jpg|jpeg|webp|svg)"', tds[0], re.I)
                if src:
                    empresa = re.sub(r"[-_]+", " ", src.group(1)).strip().title()
        if not empresa or empresa.upper().startswith("EMPRESA"):
            continue  # fila de encabezado o sin nombre recuperable
        lineas = cols[1] if len(cols) > 1 else ""
        direccion = cols[2] if len(cols) > 2 else ""
        ciudad = cols[3] if len(cols) > 3 else ""
        telefono = cols[4] if len(cols) > 4 else ""
        web = cols[5] if len(cols) > 5 else ""
        if not web:  # a veces el sitio está como link
            href = re.search(r'href="(https?://[^"]+)"', rw)
            web = href.group(1) if href else ""
        ciudad_norm = ciudad.title() if ciudad.isupper() else ciudad
        out.append({
            "pais": "Ecuador", "categoria": categoria, "empresa": empresa.strip(),
            "marcas": _detect_marcas(empresa + " " + lineas, vocab),
            "lineas_negocio": lineas, "direccion": direccion,
            "ciudad": ciudad_norm.strip(),
            "provincia": PROVINCIA.get(_strip_accents(ciudad).upper(), ""),
            "telefono": re.sub(r"\s+", " ", telefono).strip(), "web": web.strip(),
        })
    return out


_FX = (
    '<table>'
    '<tr><td class="column-1">EMPRESAS AFILIADAS</td><td class="column-2">LÍNEA(S) DE NEGOCIO</td>'
    '<td class="column-3">DIRECCIÓN</td><td class="column-4">CIUDAD</td>'
    '<td class="column-5">TELÉFONO</td><td class="column-6">WEB</td></tr>'
    '<tr><td class="column-1">AEKIA S.A.</td>'
    '<td class="column-2">Importa vehículos, repuestos y talleres marca KIA MOTORS</td>'
    '<td class="column-3">Av. 10 de Agosto N31-162</td><td class="column-4">QUITO</td>'
    '<td class="column-5">2548813</td><td class="column-6"></td></tr>'
    '<tr><td class="column-1"><img src="https://x/uploads/mercandina.png" alt="" /></td>'
    '<td class="column-2">Camiones y buses marca Chevrolet</td>'
    '<td class="column-3">Galo Plaza Lasso</td><td class="column-4">QUITO</td>'
    '<td class="column-5">2977700</td><td class="column-6">www.mercandina.ec</td></tr>'
    '</table>'
)


def selftest() -> int:
    """Valida el parseo de la tabla contra un fixture (sin red). Caza el format drift:
    header ignorado, marca detectada, teléfono, y nombre recuperado de un logo."""
    vocab = {"KIA": "Kia", "KIA MOTORS": "Kia", "CHEVROLET": "Chevrolet"}
    rows = parse_table(_FX, "Importador", vocab)
    ok = (len(rows) == 2
          and rows[0]["empresa"] == "AEKIA S.A." and rows[0]["marcas"] == "Kia"
          and rows[0]["ciudad"] == "Quito" and rows[0]["telefono"] == "2548813"
          and rows[1]["empresa"] == "Mercandina"       # nombre recuperado del logo
          and rows[1]["marcas"] == "Chevrolet" and rows[1]["web"] == "www.mercandina.ec")
    if not ok:
        print("SELFTEST AEADE FALLÓ:", rows)
        return 1
    print("selftest AEADE OK:", [(r["empresa"], r["marcas"]) for r in rows])
    return 0


def main():
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    html = fetch()
    if not html:
        print("[!] No se pudo bajar la página de afiliados (revisá red/headers).")
        return
    tables = re.findall(r"<table.*?</table>", html, re.DOTALL)
    print(f"  tablas encontradas: {len(tables)}")
    vocab = _load_marca_vocab()
    print(f"  vocabulario de marcas: {len(vocab)} formas")

    rows = []
    for idx, categoria in enumerate(CATEGORIAS):
        if idx >= len(tables):
            break
        parsed = parse_table(tables[idx], categoria, vocab)
        rows.extend(parsed)
        print(f"  {categoria}: {len(parsed)} afiliados")

    cols = ["pais", "categoria", "empresa", "marcas", "lineas_negocio",
            "direccion", "ciudad", "provincia", "telefono", "web"]
    out = DATA / "afiliados_aeade_ec.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    con_marca = sum(1 for r in rows if r["marcas"])
    con_tel = sum(1 for r in rows if r["telefono"])
    print(f"OK -> {out}: {len(rows)} afiliados · {con_marca} con marca detectada · {con_tel} con teléfono")


if __name__ == "__main__":
    main()
