#!/usr/bin/env python3
"""Importa las planillas de red comercial (Drive) -> data/concesionarios_{resumen,puntos}.csv

DURABLE / re-ejecutable por cualquiera con acceso a las planillas:
  1. En Drive: File -> Download -> Microsoft Excel (.xlsx) de cada planilla
     ("Concesionarios Peru", "Concesionarios Chile", ...).
  2. Dejar los .xlsx en  data/_drive_raw/  (esa carpeta está gitignoreada).
  3. Correr:  python scripts/import_concesionarios.py
Las planillas quedan referenciadas abajo (SHEETS) para saber cuál bajar.

Cada workbook tiene 2 tipos de hoja que se detectan por encabezado:
  - "Universo"  -> resumen por marca (# puntos, # grupos, estado)
  - "Agencias"  -> puntos de venta (grupo, punto, dirección, teléfono)
"""
import csv
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sources import canon

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "_drive_raw"
DATA = ROOT / "data"

SHEETS = {
    "Peru":  "https://docs.google.com/spreadsheets/d/1ZcGJMM3-1Zkr1-l49lK-CKgsu1t22Sx13SgYwHSfbDw/edit",
    "Chile": "https://docs.google.com/spreadsheets/d/1JlgS9l07XRyT8Ej0DbTna-H-1TKhH1-8Uwa6XLu26OM/edit",
}


def _pais_from_name(fname: str) -> str:
    low = fname.lower()
    for p in ("peru", "perú"):
        if p in low:
            return "Peru"
    for p, key in {"Chile": "chile", "Ecuador": "ecuador", "Colombia": "colombia",
                   "Costa Rica": "costa", "Guatemala": "guatemala", "Panama": "panam",
                   "Rep. Dominicana": "dominican"}.items():
        if key in low:
            return p
    return fname  # último recurso


def _col(header, *names):
    h = [str(c or "").strip().lower() for c in header]
    for n in names:
        for j, c in enumerate(h):
            if n in c:
                return j
    return None


def parse_rows(pais, rows, resumen, puntos):
    """rows = lista de filas (listas de celdas). Detecta el tipo por encabezado."""
    # encontrar la fila de encabezado (primera con celdas de texto reconocibles)
    hdr_i = hdr = None
    for i, row in enumerate(rows):
        cells = [str(c or "").strip() for c in row]
        if _col(cells, "# puntos", "puntos") is not None and _col(cells, "marca") is not None:
            hdr_i, hdr, kind = i, cells, "resumen"
            break
        if _col(cells, "grupo concesionario") is not None:
            hdr_i, hdr, kind = i, cells, "puntos"
            break
    if hdr is None:
        return
    body = rows[hdr_i + 1:]
    if kind == "resumen":
        iM, iE = _col(hdr, "marca"), _col(hdr, "estado")
        iP, iG = _col(hdr, "# puntos", "puntos"), _col(hdr, "# grupos", "grupos")
        for row in body:
            cells = [str(c or "").strip() for c in row]
            if iM >= len(cells) or not cells[iM]:
                continue
            try:
                p, g = int(cells[iP]), int(cells[iG])
            except (ValueError, IndexError):
                continue
            resumen.append({"pais": pais, "marca": canon(cells[iM]), "marca_original": cells[iM],
                            "puntos": p, "grupos": g,
                            "estado": cells[iE] if iE is not None and iE < len(cells) else ""})
    else:
        iMa, iGr = _col(hdr, "marca"), _col(hdr, "grupo concesionario")
        iNo, iTi = _col(hdr, "nombre del punto", "punto"), _col(hdr, "tipo")
        iDi = _col(hdr, "direccion", "dirección")
        iTe = _col(hdr, "telefono", "teléfono")
        iCo, iFe = _col(hdr, "confianza"), _col(hdr, "fecha")
        for row in body:
            cells = [str(c or "").strip() for c in row]
            def g(i):
                return cells[i] if i is not None and i < len(cells) else ""
            marca, grupo = g(iMa), g(iGr)
            if not marca or not grupo:
                continue
            puntos.append({"pais": pais, "marca": canon(marca), "marca_original": marca,
                           "grupo_concesionario": grupo, "punto": g(iNo), "tipo": g(iTi),
                           "direccion": g(iDi), "telefono": g(iTe),
                           "confianza": g(iCo), "fecha": g(iFe)})


def main():
    if not RAW.exists() or not any(RAW.glob("*.xlsx")):
        print(f"[!] No hay .xlsx en {RAW}")
        print("    Descargá las planillas de Drive como Excel y dejalas ahí:")
        for pais, url in SHEETS.items():
            print(f"      · {pais}: {url}")
        return
    import openpyxl
    resumen, puntos = [], []
    for path in sorted(RAW.glob("*.xlsx")):
        pais = _pais_from_name(path.name)
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for ws in wb.worksheets:
            parse_rows(pais, list(ws.iter_rows(values_only=True)), resumen, puntos)
        print(f"  {path.name} -> {pais}")

    seen = set()
    resumen = [r for r in resumen if not ((r["pais"], r["marca"]) in seen or seen.add((r["pais"], r["marca"])))]
    seen2 = set()
    puntos = [p for p in puntos if not ((k := (p["pais"], p["marca"], p["grupo_concesionario"], p["punto"], p["tipo"])) in seen2 or seen2.add(k))]

    with open(DATA / "concesionarios_resumen.csv", "w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=["pais", "marca", "marca_original", "puntos", "grupos", "estado"]).writeheader()
        csv.DictWriter(f, fieldnames=["pais", "marca", "marca_original", "puntos", "grupos", "estado"]).writerows(resumen)
    with open(DATA / "concesionarios_puntos.csv", "w", newline="", encoding="utf-8") as f:
        fn = ["pais", "marca", "marca_original", "grupo_concesionario", "punto", "tipo", "direccion", "telefono", "confianza", "fecha"]
        w = csv.DictWriter(f, fieldnames=fn); w.writeheader(); w.writerows(puntos)
    print(f"OK -> resumen: {len(resumen)} filas · puntos: {len(puntos)} filas")


if __name__ == "__main__":
    main()
