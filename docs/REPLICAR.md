# Replicar AndinaCopilot para otros países — Playbook

> Guía completa para clonar el sistema a otro conjunto de países, trabajando con Claude (Cowork / Claude Code).
> Está pensada para pasársela **entera a Claude** como contexto: "leé este doc y ayudame a replicar AndinaCopilot para {mis países}".

Repo de referencia: `github.com/faculopezp/AndinaCopilot` · Dashboard live: `andina-copilot.vercel.app`

---

## 1. Qué es y para qué

Base de datos + dashboard de **ventas automotrices por marca** de una región, para un **SDR** que vende software a concesionarios (Tecnom). El objetivo es **guiar la prospección**:

1. Ver el **top de marcas** por país → buscar sus agencias → contactarlas.
2. Detectar **marcas emergentes** (sobre todo chinas) que crecen fuerte → contactarlas antes de que exploten.
3. Del dato → a la acción: **marca → importador/grupo → red de concesionarios (puntos, contactos)**.

El usuario es un SDR, no dev. Preferencias: respuestas directas, en bullets, español; **no inventar datos** (si no está verificado → vacío/null); marcar agujeros lógicos.

---

## 2. La decisión de arquitectura (clave — copiala tal cual)

El sistema tiene **dos capas de datos que NO se mezclan**:

| Capa | Qué da | Fuente | Cubre |
|------|--------|--------|-------|
| **Amplitud** (snapshot) | Top-10 marcas por país, mismo corte, fresco | **ALADDA** (un PDF regional mensual, 14 países LatAm) | Todos los países de un saque |
| **Profundidad** (serie mensual) | Serie mes a mes + top-25 + segmentos + cola larga | Parser **propio por país** (asociación de cada país) | Solo los países que valen el esfuerzo |

**Regla de oro:** ALADDA es la base (cubre casi todo gratis). Los parsers propios se hacen **solo para los 3-4 mercados prioritarios** del SDR (los que dan Tendencia, MoM, alertas y cola larga). No pelees por serie mensual en mercados chicos: el snapshot de ALADDA ya los muestra.

`ALADDA = amplitud · asociación local = profundidad. Nunca tires los parsers propios; ALADDA los complementa.`

---

## 3. Mapa del repo (qué hace cada archivo)

```
scripts/
  sources.py          # registro de fuentes por país + canon() (normaliza nombres de marca)
  parse_aladda.py     # parser del PDF regional ALADDA (snapshot 8-14 países + serie mensual de países chicos)
  ingest.py           # ORQUESTADOR: descarga, parsea, reconstruye JSONs, dispara build. Un ingest_{pais}() por país.
  discover.py         # descubre URLs/hashes de informes nuevos (índices en vivo)
  build_dashboard.py  # inyecta data/*.json+csv en dashboard/template.html -> HTML autocontenido
  build_excel.py      # genera el .xlsx equivalente
data/
  aladda_top10.csv          # snapshot: top-10 x país (base_nacional se arma de acá)
  aladda_country_totals.csv # totales de 14 países (para el hero band)
  aladda_mensual.csv        # serie mensual de países sin parser propio (desde histórico ALADDA)
  base_nacional.csv/.json   # snapshot final que consume el dashboard (8 países, mismo corte)
  {pais}_mensual.csv        # serie mensual propia por país (schema: pais,anio,mes,marca,unid_acum,unid_mes)
  chile_full_mensual.csv    # cola larga de Chile (todas las marcas, no solo top-25)
  trend.json / china_tl.json# derivados para el dashboard (tendencia Perú, penetración china)
  grupos_importadores.csv   # marca -> grupo importador (schema: pais,marca,grupo,tipo,confianza,as_of,fuente,nota,grupo_url)
  concesionarios_*.csv      # red comercial relevada (resumen por marca + puntos con dirección/tel)
dashboard/
  template.html                     # plantilla (layout + JS); placeholders __DATA__ __MENSUAL__ __GRUPOS__ __RED__ __CNTL__
  index.html / Dashboard_...html    # generados (autocontenidos). index.html es el que sirve Vercel.
.github/workflows/update.yml        # automatización quincenal (cron días 1 y 15) + email + push
vercel.json                         # deploy (outputDirectory: dashboard)
CLAUDE.md                           # contexto para Claude (estado, fuentes, gotchas)
```

---

## 4. Las 4 capas de datos (con schemas exactos)

**1. Snapshot regional** — `data/base_nacional.json` (lo que ve el dashboard como "último corte")
```
{pais, periodo, marca, uc (unidades curr), up (previo), var (%YoY), fuente}
```
Se arma en `ingest.py:rebuild_base_nacional()` **desde `aladda_top10.csv`** aplicando `canon()`. 8 países al mismo corte.

**2. Serie mensual por país** — `data/{pais}_mensual.csv`
```
pais, anio, mes, marca, unid_acum, unid_mes
```
Una por país con parser propio. Alimenta Tendencia, Var% MoM, filtro de período y alertas. `build_dashboard.build_mensual()` las lee todas + calcula YoY/MoM.

**3. Importadoras** — `data/grupos_importadores.csv` (marca → grupo importador)
```
pais, marca, grupo, tipo(importador/distribuidor/directo), confianza(alta/media/baja), as_of, fuente, nota, grupo_url
```
Relevado con **investigación web** (no sale de los PDFs). Aplicar `canon()` al mergear.

**4. Red comercial** — `data/concesionarios_resumen.csv` + `concesionarios_puntos.csv`
```
resumen: pais, marca, puntos, grupos, estado
puntos:  pais, marca, grupo_concesionario, punto, tipo, direccion, telefono, confianza, fecha
```
Sale de planillas del Drive del equipo (relevamiento manual/semi-auto de webs de marca).

---

## 5. Fuentes: cómo encontrarlas por país (la metodología)

### A) ALADDA (gratis, cubre casi todo)
- Índice: `https://www.amda.mx/category/aladda-2/`
- PDF mensual: `https://www.amda.mx/wp-content/uploads/aladda_regional_{mmm}{yy}.pdf` (ej. `may26`)
- Trae 14 países LatAm: **Brasil, México, Argentina, Chile, Colombia, Perú, Ecuador, Uruguay, Costa Rica, Guatemala, Panamá, Paraguay, Venezuela, Rep. Dominicana**.
- `parse_aladda.py` ya lo parsea. **Si tus países están acá, el snapshot es gratis** — solo agregalos a `EN_ALCANCE` en `parse_aladda.py`.
- ⚠️ El formato del PDF **varía mes a mes** (a veces texto, a veces tablas). Por eso hay dos parsers (`parse_text` y `parse_marca_tables`) y una **validación** antes de escribir (ver §10).

### B) Fuente propia por país (para los prioritarios) — la receta de búsqueda
Para cada país prioritario, pedile a Claude que busque en la web la **asociación automotriz** que publique **ventas mensuales por marca en formato parseable** (PDF/Excel). Las que ya encontramos:

| País | Fuente | Formato | URL pattern / cómo |
|------|--------|---------|--------------------|
| Chile | **CAVEM** | PDF texto | página con slug `informe-{mes}-{anio}` → link `/informes/{hash}.pdf` |
| Perú | **AAP/SUNARP** | PDF texto | `aap.org.pe/estadisticas/...` (nombre de mes 2025; hash 2026) |
| Ecuador | **AEADE** | PDF texto (Power BI) | `download_id`; el boletín del mes M reporta M-1 |
| Colombia | **ANDI** (RUNT) | PDF "TOP 20 marcas" | `andi.com.co/Uploads/{NN}. INFORME SECTOR AUTOMOTOR {MMM}_PRENSA-INDUSTRIA {YYYY}.pdf`; recientes en Google Drive vía blog de **Fenalco** |

**Patrón de asociaciones por país** (para que Claude las busque): Argentina→**ACARA/ADEFA**, México→**AMDA/INEGI**, Brasil→**Fenabrave**, Uruguay→**ASCOMA**, Panamá→**ADAP** (ojo: visor Papermark, difícil), Costa Rica→**AIVEMA** (por prensa), Bolivia/Paraguay→prensa. **No todas tienen data parseable** — es normal; ahí te quedás con el snapshot de ALADDA.

**Criterio de decisión:** ¿la asociación publica un PDF/Excel mensual con tabla de marcas + unidades? → parser propio. ¿Solo prensa o Power BI? → snapshot ALADDA y listo.

---

## 6. El pipeline automatizado

`.github/workflows/update.yml` corre **cron días 1 y 15** (o manual):
1. `python scripts/ingest.py --pais all [--backfill]` → descubre informes nuevos, descarga, parsea, actualiza CSVs.
2. `ingest.py` refresca ALADDA (con **validación**), reconstruye `base_nacional`, `trend.json`, `china_tl.json`.
3. `build_dashboard.py` + `build_excel.py` → regeneran HTML y xlsx.
4. Genera `run_report.txt` (por país: nuevo/sin cambios/error) y lo **manda por email** (`dawidd6/action-send-mail`, Gmail SMTP).
5. Commitea y pushea → **Vercel auto-deploya**.

Secrets necesarios en GitHub Actions: `MAIL_USERNAME`, `MAIL_PASSWORD` (app password de Gmail). Deploy: Vercel con `outputDirectory: dashboard`.

---

## 7. El dashboard

`template.html` = **un solo HTML autocontenido** (datos embebidos, sin backend). `build_dashboard.py` reemplaza placeholders `__DATA__ __MENSUAL__ __GRUPOS__ __RED__ __CNTL__` con los JSON.

**Layout horizontal**: sidebar izquierdo (paleta Tecnom `#301B5A`/`#4A1FCC`) con secciones:
- **🎯 Radar H2** (home): alertas del mes + score compuesto (40% volumen + 38% YoY + 22% presencia regional + bonus chinas)
- **🌎 Región**: KPIs, comparativa entre países, penetración china, concentración
- **🌍 Cockpit por país**: KPIs, filtros propios, top marcas, tendencia mensual, alertas, importadoras + red
- **🏢 Importadoras**: grupos priorizados por marcas en rampa + red (ptos) + detalle
- **🗃️ Datos**: BD cruda ordenable + descargas

Extras: routing por hash (`#pais/Chile`), buscador global de marca (drill-down), responsive, `canon()` en JS espejo del de Python.

---

## 8. Enrichment (importadoras + red comercial)

- **Importadoras** (`grupos_importadores.csv`): Claude releva por web la asociación/registro y las webs de importadores. Fuente autoritativa: **directorios de socios** de cada asociación (el socio = el importador de la marca). Marcar `confianza` y `grupo_url`.
- **Red comercial** (`concesionarios_*.csv`): relevamiento de las webs de marca (dealer locators) — muchas bloquean bots o son mapas JS. Práctica real: el equipo lo carga en **planillas de Drive**; se bajan como `.xlsx` a `data/_drive_raw/` (gitignoreado) y `scripts/import_concesionarios.py` las importa al repo (durable, re-ejecutable). En el dashboard aparece como columna **"Red (ptos)"** por grupo + puntos por marca.
- **Etapa siguiente (no hecha aún)**: contactos (director comercial, LinkedIn) vía **Apollo** (ya conectado) — marca → grupo → decisor.

---

## 9. RECETA paso a paso para replicar (lo importante)

Decile esto a Claude, en este orden:

**Paso 0 — Setup**
1. Fork del repo `faculopezp/AndinaCopilot` (o copialo). `pip install -r requirements.txt` (pdfplumber, requests, beautifulsoup4, openpyxl).
2. Definí tus países objetivo y editá `EN_ALCANCE` en `parse_aladda.py` y `ALADDA_SOLO_MENSUAL` en `ingest.py`.

**Paso 1 — Snapshot gratis (ALADDA)**
3. Corré `python scripts/parse_aladda.py` → genera `aladda_top10.csv` + `country_totals` con tus países. Si tus países están en las 14, ya tenés el snapshot. Verificá con `--selftest`.

**Paso 2 — Series mensuales propias (solo prioritarios)**
4. Por cada país prioritario: pedile a Claude que **busque la asociación** con data parseable (§5B). Si existe, que **inspeccione el PDF real con pdfplumber** antes de escribir el parser (el layout siempre sorprende).
5. Que escriba `parse_{pais}()` + `ingest_{pais}()` en `ingest.py` siguiendo el molde de Colombia (`ingest_colombia`, el más limpio: patrón de URL + fallback discovery). Schema de salida: `pais,anio,mes,marca,unid_acum,unid_mes`.
6. Agregá `{pais}_mensual.csv` a la lista `files` en `build_dashboard.build_mensual()`.
7. Backfill: `python scripts/ingest.py --pais {pais} --backfill`.

**Paso 3 — Importadoras (web)**
8. Pedile a Claude una pasada de investigación web: por cada marca top de cada país, el grupo importador. Cargar `grupos_importadores.csv` con `confianza` + `grupo_url`. (Ojo cambios de representación año a año.)

**Paso 4 — Red comercial (Drive)**
9. Armá las planillas de agencias por país (Drive) e importalas con `_import_concesionarios.py` (ajustá los `fileId`).

**Paso 5 — Dashboard + automatización**
10. `python scripts/build_dashboard.py` y abrí `dashboard/index.html`. Ajustá copys/nombres de secciones.
11. Repo en GitHub + Vercel (`outputDirectory: dashboard`) + secrets de mail. Listo, desatendido.

---

## 10. Gotchas y lecciones (para no tropezar donde tropezamos)

- **Validá el parseo de ALADDA antes de pisar el CSV.** El PDF cambia de formato mes a mes; un parseo malo duplicó/perdió países en producción. `ingest_aladda()` exige "8 países con ≥8 marcas, sin duplicados" o **mantiene el CSV anterior**. Además hay dedupe en `write_csvs` y en `rebuild_base_nacional`.
- **`canon()` es obligatorio.** Distintas fuentes nombran igual la marca distinto (GWM vs Great Wall). Se aplica a base_nacional, MENSUAL y grupos. Sin esto, el drill-down no matchea y el radar duplica.
- **Inspeccioná el PDF con pdfplumber antes de escribir el regex.** Siempre. Los números vienen partidos (`6 .654`), en 2 columnas, con glyphs raros. No asumas el layout.
- **`fetch_pdf` con retry.** Varios servers cortan la conexión (transitorio). Reintentá 3 veces; no reintentes en 404.
- **Series propias solo donde valga.** No inviertas en parsers frágiles para mercados chicos: el snapshot los cubre. (Panamá/Papermark, Costa Rica/prensa → no vale.)
- **No inventar.** Donde la fuente no reporta → vacío/null. Confianza explícita en importadoras.
- **PDFs pesados** (ALADDA ~7MB): en CI corre bien; local usá background para no cortar por timeout.

---

## 11. Checklist final
- [ ] `parse_aladda.py --selftest` pasa
- [ ] `aladda_top10.csv` con tus N países, sin duplicados
- [ ] `base_nacional.json` = N países al mismo corte
- [ ] 1+ país con serie mensual propia (Tendencia/MoM andando)
- [ ] `grupos_importadores.csv` con las top marcas
- [ ] dashboard abre, drill-down con datos, hero con fecha por país
- [ ] GitHub Actions + Vercel + email configurados

**Regla mental para Claude:** amplitud con ALADDA, profundidad con la asociación de cada país prioritario, todo cruzado por `canon()`, y del dato a la acción vía importador → red.
