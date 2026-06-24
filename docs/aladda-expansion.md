# ALADDA como fuente regional + expansión a 8 países

## Por qué ALADDA cambia el juego
ALADDA (vía AMDA, México) publica un **informe regional mensual** que reemplaza/mejora gran parte del scraping per-país:
- **URL predecible**: `https://www.amda.mx/wp-content/uploads/aladda_regional_{mmm}{yy}.pdf` (ej. `aladda_regional_may26.pdf`). El índice para descubrir el último: `https://www.amda.mx/category/aladda-2/`.
- **PDF de texto** (export Power BI, extraíble con pdfplumber).
- **14 países en un solo archivo**, incluye los 4 nuevos (Costa Rica, Guatemala, Panamá, Rep. Dominicana) + actualiza Ecuador a **mayo 2026**.
- Trae, por país: **totales** (mes + acumulado + variación) y **Top 10 de marcas** (mes + acumulado + participación).

### Trade-off (importante)
- ALADDA = **amplitud**: 14 países, fresco, top-10 por marca + "Otras". **No** trae el long-tail ni segmentos.
- CAVEM / AAP = **profundidad**: top-25 + segmentos + serie mensual, solo para Chile/Perú.
- **Decisión recomendada**: ALADDA como **fuente primaria regional** (cubre los 8 países objetivo). Mantener CAVEM/AAP como **enriquecimiento opcional** de Chile/Perú (long-tail + segmentos + penetración china mensual). No tirar lo hecho; ALADDA lo complementa y lo expande.

## Datos ya generados (en `data/`)
- **`aladda_top10.csv`** — top-10 de marcas por país, 8 países objetivo, acum a Mayo 2026. Cols: `pais, periodo, marca, unidades_curr, unidades_prev, var_yoy_pct, origen, fuente`. ✅ verificado (top10+Otras = total de cada país).
- **`aladda_country_totals.csv`** — totales de 14 países (mes + acumulado + var), flag `en_alcance` para los 8. Sirve para el **hero band** (tamaño de mercado regional) y la fecha de corte.

## Pedidos de Facu (a implementar en Claude Code)

### 1. Integrar ALADDA + expandir a 8 países
- Sumar ALADDA a `scripts/sources.py` (patrón de URL de arriba + lista de 14 países; marcar los 8 objetivo).
- Cargar `aladda_top10.csv` a `base_nacional` (o fusionarlo): pasa de 4 a **8 países** (Chile, Perú, Ecuador, Colombia, Costa Rica, Guatemala, Panamá, Rep. Dominicana).
- Para Chile/Perú/Ecuador/Colombia: ALADDA actualiza el snapshot (mayo 2026). Reconciliar con lo existente — preferir ALADDA para el último corte; mantener la serie mensual propia (CAVEM/AAP) donde ya existe.
- **Parser nuevo** `scripts/parse_aladda.py` (para el pipeline quincenal): descarga el PDF del mes, extrae la tabla regional de totales y, por cada país, la tabla de Top 10 (mapear cada tabla a su país por el valor "Total" mensual, que matchea la tabla regional). Engancharlo a `ingest.py` y `discover.py`.

### 2. BD cruda: que se vean TODOS los datos
- En la tab **Datos crudos**, hoy se ve un subconjunto. Mostrar **todas** las filas de `base_nacional` (8 países) sin truncar, con buscador + orden por columna. Si pesa, paginar o virtualizar, pero el default debe ser "todo visible/accesible", no un top-N.

### 3. Importadores / grupos: link directo al grupo
- En el panel de grupos/importadores (drill-down por marca), el **nombre del grupo debe ser un hyperlink** al sitio del grupo.
- Falta el dato de URL del grupo: agregar columna **`grupo_url`** en `data/grupos_importadores.csv` (distinta de `fuente`, que es la fuente del relevamiento). Cargar desde la planilla "Concesionarios Andean" o relevar.
- `build_dashboard.py` → `load_grupos()` ya expone `web`; sumar `grupo_url` y que el template renderice `<a href="grupo_url" target="_blank">{grupo}</a>`.

## Checklist sugerido
1. `parse_aladda.py` + sumar fuente a `sources.py`.
2. Fusionar `aladda_top10.csv` → `base_nacional` (8 países) + reconciliación.
3. Hero band usa `aladda_country_totals.csv` (mercado regional + fecha corte).
4. BD cruda: mostrar todo.
5. `grupo_url` en grupos_importadores + link en el template.
6. Actualizar `CLAUDE.md` (4→8 países, ALADDA como fuente primaria) y los copys del dashboard.

## Nota de datos
- ALADDA da acumulado Ene-May; el `var_yoy_pct` en `aladda_top10.csv` está calculado acum-2026 vs acum-2025. Donde el año anterior es 0 (ej. Tesla Colombia, base 6), queda valor alto real — no es error.
- Si más adelante quieren los 14 países (sumar Brasil, México, Argentina, etc.), el `country_totals` ya los tiene y `aladda_top10.csv` se extiende sin cambiar el esquema.
