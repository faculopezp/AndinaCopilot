# AndinaCopilot


---

## Qué hay acá

```
AndinaCopilot/
├─ data/                         # BD cruda (fuente de verdad)
│  ├─ base_nacional.csv/.json    # snapshot nacional por país/marca (último acumulado)
│  ├─ peru_nacional_mensual.csv  # serie mensual Perú (ene-2025 → mar-2026)
│  ├─ trend.json                 # serie mensual lista para el dashboard
│  ├─ china_tl.json              # penetración de marcas chinas en el tiempo
│  └─ chile_hashes.csv           # hashes de los PDFs mensuales de CAVEM (Chile)
├─ scripts/
│  ├─ sources.py                 # registro de fuentes y patrones de URL por país
│  ├─ build_dashboard.py         # genera el dashboard desde la plantilla + datos
│  └─ parse_peru_reference.py    # parser de referencia del PDF de AAP (Perú)
├─ dashboard/
│  ├─ template.html              # plantilla con placeholders __DATA__/__TREND__/__CNTL__
│  └─ Dashboard_Andino_Ventas_Auto.html  # dashboard generado (abrir en el navegador)
├─ Base_Andina_Ventas_Auto.xlsx  # misma base en Excel (para compartir/editar a mano)
├─ .github/workflows/update.yml  # automatización quincenal (esqueleto)
└─ vercel.json                   # deploy del dashboard a Vercel
```

## Cómo correrlo

Requisitos: Python 3.10+.

```bash
pip install -r requirements.txt
python scripts/build_dashboard.py   # regenera dashboard/Dashboard_Andino_Ventas_Auto.html
```

Después abrí `dashboard/Dashboard_Andino_Ventas_Auto.html` en cualquier navegador. Es **autocontenido** (no necesita servidor ni internet): lo podés mandar por mail/Drive/WhatsApp y se abre solo.

## Estado de las fuentes (jun-2026)

| País | Fuente | Formato | Estado |
|------|--------|---------|--------|
| Perú | AAP / SUNARP | PDF texto | ✅ serie mensual completa cargada |
| Chile | CAVEM | PDF texto | ✅ validado; backfill mensual pendiente (vía automatización) |
| Ecuador | AEADE | PDF texto (download_id) | ✅ validado; backfill pendiente |
| Colombia | ANDI/Fenalco · ANDEMOS | Power BI / prensa | ⚠️ top-20 cargado; sin marca en datos.gov.co |

Detalle de URLs y patrones en `scripts/sources.py`.

## Esquema de la BD (`data/base_nacional.csv`)

`pais, periodo, marca, unidades_curr, unidades_prev, var_yoy_pct, fuente`

Una fila por país-marca (último acumulado disponible). Períodos por país (Perú a marzo-2026, Chile/Ecuador a mayo-2026) — el ranking y el % interanual son comparables; el volumen absoluto no es 1:1 por el desfase de publicación.

## Roadmap (versión final)

- [ ] Backfill mensual completo Chile + Ecuador (scraper sobre `sources.py`).
- [ ] Colombia con ranking de marcas (ANDEMOS/ANDI vía navegador).
- [ ] Automatización quincenal (`.github/workflows/update.yml`) que revisa informes nuevos, parsea, actualiza `data/` y regenera el dashboard.
- [ ] Deploy del dashboard a Vercel con acceso compartido para el equipo.

## Notas

- La penetración de marcas chinas es el indicador clave para el caso de uso "contactar antes de que exploten".
- La lista de marcas chinas vive en `scripts/sources.py` (`MARCAS_CHINAS`) — agregar marcas nuevas ahí.
