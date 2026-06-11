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

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DASH = ROOT / "dashboard"


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
                    "pais": r["pais"], "anio": anio, "mes": mes, "marca": r["marca"],
                    "acc": int(r["unid_acum"]),
                    "mes_u": int(mu) if mu not in ("", None) else None,
                })

    acc_idx = {(r["pais"], r["marca"], r["anio"], r["mes"]): r["acc"] for r in raw}
    mes_idx = {(r["pais"], r["marca"], r["anio"], r["mes"]): r["mes_u"] for r in raw}

    rows, periodos = [], set()
    for r in raw:
        ym = f'{r["anio"]}-{r["mes"]:02d}'
        periodos.add(ym)
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
    return {"periodos": sorted(periodos), "rows": rows}


def main():
    data = json.loads((DATA / "base_nacional.json").read_text(encoding="utf-8"))
    trend = json.loads((DATA / "trend.json").read_text(encoding="utf-8"))
    cntl = json.loads((DATA / "china_tl.json").read_text(encoding="utf-8"))
    mensual = build_mensual()

    html = (DASH / "template.html").read_text(encoding="utf-8")
    html = (html
            .replace("__DATA__", json.dumps(data, ensure_ascii=False))
            .replace("__TREND__", json.dumps(trend, ensure_ascii=False))
            .replace("__CNTL__", json.dumps(cntl, ensure_ascii=False))
            .replace("__MENSUAL__", json.dumps(mensual, ensure_ascii=False)))

    out = DASH / "Dashboard_Andino_Ventas_Auto.html"
    out.write_text(html, encoding="utf-8")
    # index.html para Vercel (outputDirectory: dashboard)
    (DASH / "index.html").write_text(html, encoding="utf-8")
    print(f"OK -> {out}  ({len(html)/1024:.1f} KB, {len(data)} filas)")


if __name__ == "__main__":
    main()
