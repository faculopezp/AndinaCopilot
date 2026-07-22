"""One-off: importa las planillas 'Concesionarios Peru/Chile' del Drive (formato
markdown-table) -> data/concesionarios_resumen.csv + data/concesionarios_puntos.csv
"""
import json, re, csv, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sources import canon

BASE = pathlib.Path(r"C:\Users\faq_l\.claude\projects\C--Users-faq-l-OneDrive-Documentos-Claude-Things-reports\95c4e560-5b88-4090-8560-363aeb4a4609\tool-results")
FILES = {
    "Peru": BASE / "mcp-11c7aa87-bbf4-4550-943f-ab5e0750b214-read_file_content-1784728922154.txt",
    "Chile": BASE / "mcp-11c7aa87-bbf4-4550-943f-ab5e0750b214-read_file_content-1784728937636.txt",
}
DATA = pathlib.Path(__file__).resolve().parents[1] / "data"


def clean(cell: str) -> str:
    return cell.replace("\\#", "#").replace("\\+", "+").replace("\\'", "'").strip()


def tables_of(content: str):
    """Agrupa líneas '|...|' consecutivas en tablas; devuelve listas de filas (celdas)."""
    tables, cur = [], []
    for ln in content.splitlines():
        if ln.strip().startswith("|"):
            cells = [clean(c) for c in ln.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-+:?", c) or c == "" for c in cells):
                continue  # fila de alineación o vacía
            cur.append(cells)
        else:
            if cur:
                tables.append(cur)
                cur = []
    if cur:
        tables.append(cur)
    return tables


resumen, puntos = [], []
for pais, path in FILES.items():
    content = json.loads(path.read_text(encoding="utf-8"))["fileContent"]
    for tbl in tables_of(content):
        # header = primera fila con celdas con texto
        hdr = None
        for i, row in enumerate(tbl):
            if any(c for c in row):
                hdr = [h.lower() for h in row]
                body = tbl[i + 1:]
                break
        if not hdr:
            continue
        def col(*names):
            for n in names:
                for j, h in enumerate(hdr):
                    if n in h:
                        return j
            return None
        if col("# puntos") is not None and col("marca") is not None and col("# grupos") is not None:
            iM, iE, iP, iG = col("marca"), col("estado"), col("# puntos"), col("# grupos")
            req = [iM, iP, iG] + ([iE] if iE is not None else [])
            for row in body:
                if len(row) <= max(req):
                    continue
                try:
                    p, g = int(row[iP]), int(row[iG])
                except ValueError:
                    continue
                marca = row[iM]
                if not marca:
                    continue
                resumen.append({"pais": pais, "marca": canon(marca), "marca_original": marca,
                                "puntos": p, "grupos": g,
                                "estado": row[iE] if iE is not None and iE < len(row) else ""})
        elif col("grupo concesionario") is not None:
            iMa, iGr = col("marca"), col("grupo concesionario")
            iNo, iTi = col("nombre del punto"), col("tipo")
            iDi, iTe = col("direccion", "dirección"), col("telefono", "teléfono")
            iCo, iFe = col("confianza"), col("fecha")
            for row in body:
                if len(row) <= max(x for x in [iMa, iGr, iNo] if x is not None):
                    continue
                marca, grupo = row[iMa], row[iGr]
                if not marca or not grupo:
                    continue
                def g(i):
                    return row[i] if i is not None and i < len(row) else ""
                puntos.append({
                    "pais": pais, "marca": canon(marca), "marca_original": marca,
                    "grupo_concesionario": grupo, "punto": g(iNo), "tipo": g(iTi),
                    "direccion": g(iDi), "telefono": g(iTe),
                    "confianza": g(iCo), "fecha": g(iFe),
                })

# dedupe (el export del Sheet puede repetir tablas)
_seen = set()
resumen = [r for r in resumen if not ((r["pais"], r["marca"]) in _seen or _seen.add((r["pais"], r["marca"])))]
_seen2 = set()
puntos = [p for p in puntos if not ((k := (p["pais"], p["marca"], p["grupo_concesionario"], p["punto"], p["tipo"])) in _seen2 or _seen2.add(k))]

with open(DATA / "concesionarios_resumen.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["pais", "marca", "marca_original", "puntos", "grupos", "estado"])
    w.writeheader(); w.writerows(resumen)
with open(DATA / "concesionarios_puntos.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["pais", "marca", "marca_original", "grupo_concesionario",
                                      "punto", "tipo", "direccion", "telefono", "confianza", "fecha"])
    w.writeheader(); w.writerows(puntos)

print(f"resumen: {len(resumen)} filas | puntos: {len(puntos)} filas")
from collections import Counter
print("resumen por pais:", Counter(r["pais"] for r in resumen))
print("puntos por pais:", Counter(r["pais"] for r in puntos))
print("ej resumen:", resumen[:3])
print("ej punto:", puntos[:1])
