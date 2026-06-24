# Mejoras v2 — feedback externo filtrado

**Lente:** AndinaCopilot es una **herramienta interna de prospección** para SDRs de Tecnom, **no** un SaaS para vender al ecosistema automotriz. Por eso descartamos lo que es posicionamiento/marketing/multi-tenant y tomamos solo lo que mejora la decisión comercial del equipo.

## Diagnóstico del feedback
- El crítico asume un producto público con muchos tipos de usuario. Internamente el usuario es uno solo (SDR Tecnom) y ya sabe qué es → casi todo el bloque "posicionamiento" no aplica.
- Lo que sí sirve para uso interno: hero metric, fecha de actualización visible, score transparente, drill-down por marca, alertas, y el bug del header vacío.
- La mejor idea del feedback (distribuidores/grupos por marca) es justo el puente a la acción del SDR, pero es otra fuente de datos (enrichment) → etapa aparte.

## Tomar (sí — mejoran el uso interno)
1. **Hero band** arriba de todo (cubre tu pedido de 4/4 + fecha): Mercado Andino — total unidades acum, +X% YoY, X% chinas, **4/4 países** con **fecha de última actualización por país** (CL may-26 · PE abr-26 · EC … · CO …).
2. **Subtítulo de 1 línea** para onboarding interno: "Ventas por marca en Chile, Perú, Ecuador y Colombia para priorizar prospección." (sin copy de marketing externo).
3. **Radar H2 → agregar Var% mensual** al lado de Var% YoY (dato ya disponible en `__MENSUAL__.mesVar`). Cambiar "presencia X/3" → **"/4"**.
4. **Score transparente**: mostrar pesos (ej. 40% volumen + 40% crecimiento + 20% presencia regional), aunque sea aproximado. Tooltip o nota.
5. **Drill-down por marca**: click en una marca → panel con ventas por país, evolución mensual, share, YoY y MoM. Alto valor para el laburo real.
6. **Alertas del mes** (chips/lista) derivadas de `__MENSUAL__`: "Jetour +78% MoM", "BYD entró al top 10", "X perdió share en PE".
7. **Fix**: encabezado vacío `##` (~línea 37) — quitar o completar.
8. **Actualizar copys obsoletos**: "Chile/Ecuador se suman en la próxima tanda" y "Colombia en proceso" ya no aplican (backfill hecho) → reemplazar por el estado real.

## Descartar / postergar (no aplican a uso interno)
- Tagline de marketing + posicionamiento "para SDRs/agencias/software/proveedores".
- Filtro "tipo de proveedor (CRM / agencia / IA)": es feature multi-tenant de SaaS; internamente siempre somos Tecnom.
- Pivote a "motor de inteligencia comercial para venderle al ecosistema": fuera de alcance ahora.

## Alto ROI, etapa aparte (la mejor mejora del feedback)
- **Módulo marca → distribuidor → contacto**: al abrir una marca, mostrar quién la importa/maneja + director comercial + LinkedIn + sitio. Es el puente directo a la acción del SDR. **No sale de los PDFs** → necesita enrichment (Apollo / LinkedIn, ya conectados). Tratarlo como módulo separado, después de las mejoras de UI.

## Tus 2 adds (confirmados)
- Radar H2: **+ Var% mensual** además de YoY. ✅ dato disponible.
- **4/4 países + fecha de última actualización** del dato, visible arriba. ✅ (va en el hero band)

## Checklist para Claude Code (orden sugerido)
1. Hero band con last-update por país + 4/4 + KPIs región (incluye tus 2 adds).
2. Radar H2: columna **Var% mensual** + presencia **/4**.
3. Fix `##` vacío + actualizar copys de "en proceso".
4. Score transparente (pesos visibles).
5. Drill-down por marca (modal/panel).
6. Alertas del mes (derivadas de `__MENSUAL__`).
7. (Etapa 2) Módulo marca → distribuidor → contacto con enrichment.
