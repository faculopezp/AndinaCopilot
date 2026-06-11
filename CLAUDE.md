# AndinaCopilot — contexto para Claude Code

## Qué es
Base de datos + dashboard de **ventas automotrices de la Región Andina** (Chile, Perú, Ecuador, Colombia), para el proyecto **SDR Andino de Tecnom**.

**Objetivo de negocio:** reunir las ventas por marca de cada país en un solo lugar y usarlas para decidir a qué agencias/concesionarios contactar. Casos de uso concretos:
1. Ver el **top de marcas** por país → buscar sus agencias → contactarlas.
2. Detectar **marcas emergentes** (sobre todo chinas) que crecen fuerte → contactarlas **antes de que “exploten”**.
3. Ver tendencia y estado de cada mercado para priorizar.

El usuario (Facu) es SDR, no necesariamente dev. Preferencias: respuestas directas, en bullets, sin relleno; español neutro; marcar agujeros lógicos; no inventar datos (si no está verificado → vacío/null).

## Estado actual (jun-2026)
Lo construido hasta ahora (en una sesión de Cowork, ahora migrado a este repo):
- **Perú**: serie mensual completa ene-2025 → mar-2026, parseada del PDF de AAP. ✅
- **Chile**: fuente validada (CAVEM), 17 hashes de PDF descubiertos (ene2025–may2026), formato confirmado. Backfill mensual **pendiente**.
- **Ecuador**: fuente validada (AEADE, vía download_id, PDF texto). Backfill **pendiente**.
- **Colombia**: top-20 acumulado a may-2026 cargado (ANDI/Fenalco vía prensa). Sin serie histórica.
- **Dashboard** HTML autocontenido con filtros (País, Origen) + tabs Top / Emergentes / Comparar países / Tendencia (Perú) / Mercado (penetración chinas + concentración) / Datos crudos.
- **Excel** equivalente.

## Estructura del repo
```
data/        # BD cruda (fuente de verdad)
  base_nacional.csv / .json     # snapshot nacional por país-marca (último acumulado)
  peru_nacional_mensual.csv     # serie mensual Perú
  trend.json                    # serie mensual lista para el dashboard
  china_tl.json                 # penetración marcas chinas en el tiempo (Chile + Perú)
  chile_hashes.csv              # hashes de los PDFs mensuales de CAVEM
scripts/
  sources.py                    # registro de fuentes y patrones de URL por país (IP clave)
  build_dashboard.py            # inyecta data/*.json en la plantilla → dashboard HTML
  parse_peru_reference.py       # parser de referencia del PDF de AAP (Perú)
dashboard/
  template.html                 # plantilla con placeholders __DATA__ / __TREND__ / __CNTL__
  Dashboard_Andino_Ventas_Auto.html   # generado (autocontenido, abrir en navegador)
Base_Andina_Ventas_Auto.xlsx
.github/workflows/update.yml    # automatización quincenal (ESQUELETO)
vercel.json                     # deploy del dashboard
```

## Esquema de datos
`data/base_nacional.csv`: `pais, periodo, marca, unidades_curr, unidades_prev, var_yoy_pct, fuente`
- Una fila por país-marca, último acumulado disponible.
- **Períodos distintos por país** (Perú a marzo, Chile/Ecuador a mayo) por desfase de publicación. Ranking y % interanual son comparables; el volumen absoluto entre países **no** es 1:1.
- Colombia: `unidades_prev`/`var` vacíos donde la fuente no los reporta (n/d). No inventar.

`data/peru_nacional_mensual.csv`: `pais, anio, mes, marca, unid_acum, unid_mes`. `unid_mes` = delta del acumulado dentro del año.

## Fuentes por país (ver `scripts/sources.py` para URLs exactas)
| País | Fuente | Formato | Notas |
|------|--------|---------|-------|
| Perú | AAP/SUNARP | PDF texto | URLs predecibles (ene-sep 2025 por nombre de mes; oct2025+ con hash). Tablas por segmento (Automóviles, Pickup, Camionetas, SUV). Nacional = suma de segmentos. |
| Chile | CAVEM | PDF texto | Slug de página predecible `informe-{mes}-{anio}` → link `/informes/{hash}.pdf`. Trae ranking mensual y acumulado + **tabla dedicada de marcas chinas mes a mes** (clave). |
| Ecuador | AEADE | PDF texto | `download_id`. **El boletín del mes M reporta datos del mes M-1** (autodetectar desde el contenido: “BOLETÍN DE VENTAS {MES} {AÑO}”). Trae Top 20 marcas + EV + híbridos. CINAE es alternativa (Power BI con texto extraíble). |
| Colombia | ANDI/Fenalco · ANDEMOS | Power BI / prensa | ⚠️ ANDEMOS solo expone Power BI (no scrappeable directo). datos.gov.co (u3vn-bdcy) **NO trae marca**, solo volumen por clase/departamento. Ranking de marcas: vía nota de prensa o navegador. |

## Convenciones / decisiones tomadas
- **Marcas chinas**: lista en `scripts/sources.py` (`MARCAS_CHINAS`). Es el eje del caso de uso (radar de emergentes). Agregar marcas nuevas ahí.
- **Dashboard autocontenido**: un solo HTML, sin servidor ni internet, datos embebidos. Se regenera con `build_dashboard.py`. Filtros globales = País + Origen; Año/Mes viven en la tab Tendencia (única con eje temporal hoy).
- **No inventar datos**: donde la fuente no reporta, queda vacío/null.

## Gotchas conocidos
- Los PDFs de CAVEM/AAP/AEADE son ~50-56k caracteres; descargarlos + parsearlos en bulk conviene hacerlo en este entorno (notebook), no por chat.
- CAVEM: el hash del PDF cambia por mes y no es predecible → hay que abrir la página del mes y extraer el link con `CHILE["pdf_regex"]`.
- AEADE: desfase M-1 (ver arriba). Para descubrir download_id nuevos, scrapear `ECUADOR["indice"]`.
- El parser de Perú: las columnas del PDF cambian de año (2024/2025 vs 2025/2026); detectar el año desde el header `Rank. Marca {y1} {y2}`.

## Próximos pasos (roadmap, prioridad)
1. **`scripts/ingest.py`** — scraper que, usando `sources.py`: descubre informes nuevos, descarga el PDF, lo parsea (pdfplumber) y actualiza `data/`. Es lo que falta para cerrar el backfill y la automatización.
2. **Backfill mensual** Chile + Ecuador (ene-2025 → actual) con ese scraper.
3. **Colombia con marcas** (ANDEMOS/ANDI; probablemente requiera navegador/headless).
4. **Automatización quincenal**: completar `.github/workflows/update.yml` con el paso de ingesta.
5. **Deploy a Vercel** con acceso compartido para el equipo (`vercel.json` ya está).

## Cómo correr
```bash
pip install -r requirements.txt
python scripts/build_dashboard.py   # regenera dashboard/Dashboard_Andino_Ventas_Auto.html
```
