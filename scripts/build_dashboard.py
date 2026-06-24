#!/usr/bin/env python3
"""Genera el dashboard HTML autocontenido inyectando los datos en la plantilla.

Uso:
    python scripts/build_dashboard.py

Lee:
    data/base_nacional.json   -> snapshot nacional por pais/marca (ultimo acumulado)
    data/trend.json           -> serie mensual por marca (Peru)
    data/china_tl.json        -> penetracion de marcas chinas en el tiempo
    dashboard/template.html   -> plantilla con placeholders __DATA__ / __TREND__ / __CNTL__

Escribe:
    dashboard/Dashboard_Andino_Ventas_Auto.html
"""
import csv
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DASH = ROOT / "dashboard"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sources import canon  # noqa: E402  (normalización de nombres de marca)


def build_mensual():
    """Arma el dataset mensual (Perú/Chile/Ecuador) con YoY pre-calculado.

    Estructura: {"periodos": ["YYYY-MM", ...], "rows": [
        {"pais","ym","marca","acc","mes","accVar","mesVar"}, ...]}
    `acc` = acumulado del año; `mes` = unidades del mes (o None);
    `accVar`/`mesVar` = % interanual vs mismo mes del año anterior (o None).
    Colombia no tiene serie mensual, por eso no aparece acá.
    """
    files = [
        ("peru_nacional_mensual.csv", "Peru"),
        ("chile_mensual.csv", "Chile"),
        ("ecuador_mensual.csv", "Ecuador"),
    ]
    raw = []
    for fn, _pais in files:
        path = DATA / fn
        if not path.exists():
            continue
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                anio, mes = int(r["anio"]), int(r["mes"])
                mu = r["unid_mes"]
                raw.append({
                    "pais": r["pais"], "anio": anio, "mes": mes, "marca": canon(r["marca"]),
                    "acc": int(r["unid_acum"]),
                    "mes_u": int(mu) if mu not in ("", None) else None,
                })

    acc_idx = {(r["pais"], r["marca"], r["anio"], r["mes"]): r["acc"] for r in raw}
    mes_idx = {(r["pais"], r["marca"], r["anio"], r["mes"]): r["mes_u"] for r in raw}

    rows, periodos = [], set()
    seen = set()  # (pais,marca,anio,mes) ya cubiertos por las series acumuladas oficiales
    for r in raw:
        ym = f'{r["anio"]}-{r["mes"]:02d}'
        periodos.add(ym)
        seen.add((r["pais"], r["marca"], r["anio"], r["mes"]))
        pacc = acc_idx.get((r["pais"], r["marca"], r["anio"] - 1, r["mes"]))
        pmes = mes_idx.get((r["pais"], r["marca"], r["anio"] - 1, r["mes"]))
        acc_var = round((r["acc"] - pacc) / pacc * 100, 1) if pacc else None
        mes_var = (round((r["mes_u"] - pmes) / pmes * 100, 1)
                   if (pmes and r["mes_u"] is not None) else None)
        rows.append({
            "pais": r["pais"], "ym": ym, "marca": r["marca"],
            "acc": r["acc"], "mes": r["mes_u"],
            "accVar": acc_var, "mesVar": mes_var,
        })

    # Cola larga de Chile: marcas fuera del top-25 oficial. Solo unidades del mes;
    # el acumulado se calcula por suma corrida dentro del año (cumsum).
    rows += _chile_tail_rows(seen, periodos)
    return {"periodos": sorted(periodos), "rows": rows}


def _chile_tail_rows(seen: set, periodos: set) -> list:
    """Filas mensuales de marcas de Chile que NO están en el top-25 oficial.

    Lee chile_full_mensual.csv (unidades del mes, todas las marcas). Reconstruye el
    acumulado por cumsum dentro del año y el % interanual donde hay año anterior.
    """
    path = DATA / "chile_full_mensual.csv"
    if not path.exists():
        return []
    full = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            mu = r["unid_mes"]
            full.append({
                "anio": int(r["anio"]), "mes": int(r["mes"]), "marca": canon(r["marca"]),
                "mes_u": int(mu) if mu not in ("", None) else None,
            })

    # cumsum por (marca, año) y lookup de mes para YoY
    mes_lk = {(x["marca"], x["anio"], x["mes"]): x["mes_u"] for x in full}
    acc_run: dict = {}
    acc_lk: dict = {}
    for x in sorted(full, key=lambda z: (z["marca"], z["anio"], z["mes"])):
        key = (x["marca"], x["anio"])
        acc_run[key] = acc_run.get(key, 0) + (x["mes_u"] or 0)
        acc_lk[(x["marca"], x["anio"], x["mes"])] = acc_run[key]

    out = []
    for x in full:
        # saltar las que ya están en el top-25 oficial (mismo pais/marca/anio/mes)
        if ("Chile", x["marca"], x["anio"], x["mes"]) in seen:
            continue
        ym = f'{x["anio"]}-{x["mes"]:02d}'
        periodos.add(ym)
        acc = acc_lk[(x["marca"], x["anio"], x["mes"])]
        pacc = acc_lk.get((x["marca"], x["anio"] - 1, x["mes"]))
        pmes = mes_lk.get((x["marca"], x["anio"] - 1, x["mes"]))
        out.append({
            "pais": "Chile", "ym": ym, "marca": x["marca"],
            "acc": acc, "mes": x["mes_u"],
            "accVar": round((acc - pacc) / pacc * 100, 1) if pacc else None,
            "mesVar": (round((x["mes_u"] - pmes) / pmes * 100, 1)
                       if (pmes and x["mes_u"] is not None) else None),
        })
    return out


def load_grupos():
    """Carga el mapeo marca->grupo importador (data/grupos_importadores.csv)."""
    path = DATA / "grupos_importadores.csv"
    if not path.exists():
        return []
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append({
                "pais": r["pais"], "marca": canon(r["marca"]), "grupo": r["grupo"],
                "tipo": r["tipo"], "confianza": r["confianza"],
                "web": r.get("fuente", ""), "nota": r.get("nota", ""),
                "grupo_url": (r.get("grupo_url") or "").strip(),
            })
    return out


def main():
    data = json.loads((DATA / "base_nacional.json").read_text(encoding="utf-8"))
    trend = json.loads((DATA / "trend.json").read_text(encoding="utf-8"))
    cntl = json.loads((DATA / "china_tl.json").read_text(encoding="utf-8"))
    mensual = build_mensual()
    grupos = load_grupos()

    html = (DASH / "template.html").read_text(encoding="utf-8")
    html = (html
            .replace("__DATA__", json.dumps(data, ensure_ascii=False))
            .replace("__TREND__", json.dumps(trend, ensure_ascii=False))
            .replace("__CNTL__", json.dumps(cntl, ensure_ascii=False))
            .replace("__MENSUAL__", json.dumps(mensual, ensure_ascii=False))
            .replace("__GRUPOS__", json.dumps(grupos, ensure_ascii=False)))

    out = DASH / "Dashboard_Andino_Ventas_Auto.html"
    out.write_text(html, encoding="utf-8")
    # index.html para Vercel (outputDirectory: dashboard)
    (DASH / "index.html").write_text(html, encoding="utf-8")
    print(f"OK -> {out}  ({len(html)/1024:.1f} KB, {len(data)} filas)")


if __name__ == "__main__":
    main()
