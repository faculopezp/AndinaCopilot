# Fix dashboard v3 — datos ALADDA + click marca

## Causa raiz (las dos cosas cuelgan del mismo paso)
El `aladda_top10.csv` (8 paises, may-26) NO esta mergeado a `base_nacional.json`.
- `base_nacional.json` sigue con 4 paises y Ecuador en "Acum a Enero 2026".
- El dash lee base_nacional.json -> muestra EC ene-26 y "4/4".

## Fix 1 — Integrar ALADDA al pipeline (durable, no parche manual)
En `ingest.py` / build de base:
1. Correr `parse_aladda.py` (ya esta en scripts/) o leer `data/aladda_top10.csv`.
2. Mergear a `base_nacional`: para los 8 paises objetivo (Chile, Peru, Ecuador, Colombia,
   Costa Rica, Guatemala, Panama, Rep. Dominicana) usar ALADDA como ULTIMO corte (may-26).
   - Reemplaza el snapshot viejo de Ecuador (ene-26) y suma CR/GT/PA/RD.
   - Mantener la serie mensual propia (chile/peru/ecuador_mensual.csv) para Tendencia/H2.
3. Regenerar base_nacional.json + dashboard.
Resultado: hero pasa a 8 paises, fechas frescas, Ecuador may-26.

## Fix 2 — Normalizar nombres de marca (arregla el click roto)
Hoy conviven "Great Wall" y "Gwm" (y posibles diferencias de mayusc/title-case) entre fuentes.
- Definir un mapa de alias canonico, ej:
  ALIAS = {"GWM": "Great Wall", "GREAT WALL": "Great Wall", "MMC": "Mitsubishi", ...}
- Aplicarlo SIEMPRE al construir base_nacional Y grupos_importadores (misma funcion canon()).
- Asi `openMarca` matchea entre paises y el modal nunca sale vacio.

## Fix 3 — openMarca robusto (template.html, ~lineas 367-387)
- Match canonico/insensible a mayusc:
    SNAP.filter(d => canon(d.marca) === canon(marca))
- Cambiar el hardcode "de 4 paises" (linea 384) por dinamico:
    `Presente en ${snap.length} de ${paises().length} paises`
- Si snap.length === 0: mostrar "Sin datos de ventas para esta marca" en vez de tabla vacia
  (defensivo, evita el modal "roto").

## Nota
El merge se puede hacer a mano una vez para ver el resultado YA, pero si no entra al
pipeline, la proxima corrida de ingest lo pisa. Por eso Fix 1 va en ingest/build, no manual.
