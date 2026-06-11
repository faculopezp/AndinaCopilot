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
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DASH = ROOT / "dashboard"


def main():
    data = json.loads((DATA / "base_nacional.json").read_text(encoding="utf-8"))
    trend = json.loads((DATA / "trend.json").read_text(encoding="utf-8"))
    cntl = json.loads((DATA / "china_tl.json").read_text(encoding="utf-8"))

    html = (DASH / "template.html").read_text(encoding="utf-8")
    html = (html
            .replace("__DATA__", json.dumps(data, ensure_ascii=False))
            .replace("__TREND__", json.dumps(trend, ensure_ascii=False))
            .replace("__CNTL__", json.dumps(cntl, ensure_ascii=False)))

    out = DASH / "Dashboard_Andino_Ventas_Auto.html"
    out.write_text(html, encoding="utf-8")
    # index.html para Vercel (outputDirectory: dashboard)
    (DASH / "index.html").write_text(html, encoding="utf-8")
    print(f"OK -> {out}  ({len(html)/1024:.1f} KB, {len(data)} filas)")


if __name__ == "__main__":
    main()
