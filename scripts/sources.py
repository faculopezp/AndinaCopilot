#!/usr/bin/env python3
"""Registro de fuentes oficiales por pais (Region Andina) y patrones de URL descubiertos.

Esto es el "mapa" del pipeline: de donde sale cada dato y como se arma la URL del
informe mensual. Lo usa el scraper/parsers. Mantener actualizado cuando una fuente
cambie de formato o de host.

Estado validado a junio 2026:
  - Peru (AAP)     : PDF texto, parseable. URLs predecibles. OK
  - Chile (CAVEM)  : PDF texto. Slug de pagina predecible -> link a /informes/{hash}.pdf
  - Ecuador (AEADE): PDF texto via download_id. El boletin del mes M reporta el mes M-1.
  - Colombia       : ANDI/Fenalco (via prensa) o ANDEMOS (Power BI, no scrappeable directo).
                     datos.gov.co NO trae marca (solo volumen por clase/departamento).
"""

# ---- PERU (AAP / SUNARP) -------------------------------------------------
# Informes mensuales. 2025 ene-sep con nombre de mes; oct-dic 2025 y 2026 con hash.
PERU = {
    "base_mes": "https://aap.org.pe/storage/estadisticas/{anio}/{Mes}.pdf",  # ene-sep 2025
    "hashed": {  # meses con URL hasheada (descubiertos manualmente desde la pagina de informes)
        (2025, 10): "https://aap.org.pe/storage/estadisticas/informes-mensuales/1763151961092.pdf",
        (2025, 11): "https://aap.org.pe/storage/estadisticas/informes-mensuales/1765893943629.pdf",
        (2025, 12): "https://aap.org.pe/storage/estadisticas/informes-mensuales/1768240598052.pdf",
        (2026, 1):  "https://aap.org.pe/storage/estadisticas/informes-mensuales/1771514047285-2.pdf",
        (2026, 2):  "https://aap.org.pe/storage/estadisticas/informes-mensuales/informe-del-sector-automotor-febrero-2.pdf",
        (2026, 3):  "https://aap.org.pe/storage/estadisticas/informes-mensuales/informe-del-sector-automotor-marzo-1.pdf",
    },
    "indice": "https://aap.org.pe/estadisticas/informes-del-sector-automotor",  # para descubrir hashes nuevos
}

# ---- CHILE (CAVEM) -------------------------------------------------------
# Cada mes tiene una pagina con slug predecible; dentro esta el link al PDF (/informes/{hash}.pdf).
CHILE = {
    "pagina_mes": "https://www.cavem.cl/informesmercado/informe-{mes}-{anio}",  # mes en minuscula es-es
    "pdf_regex": r"/informes/([a-f0-9]+)\.pdf",  # extraer hash de la pagina
    "indice": "https://www.cavem.cl/informes_mercado",
    # hashes ya descubiertos (ver data/chile_hashes.csv para el set completo ene2025-may2026)
}

# ---- ECUADOR (AEADE) -----------------------------------------------------
# Boletin de ventas para prensa via download_id. OJO: el boletin "mes M" reporta datos del mes M-1.
ECUADOR = {
    "descarga": "https://www.aeade.net/?sdm_process_download=1&download_id={download_id}",
    "indice": "https://www.aeade.net/boletines-de-prensa-venta-de-vehiculos/",  # para descubrir download_id nuevos
    "download_ids": {  # etiqueta del boletin -> id (el contenido reporta el mes anterior)
        "2025-01": 32014, "2025-02": 32297, "2025-03": 32398, "2025-04": 32659,
        "2025-05": 33022, "2025-06": 33315, "2025-07": 33651, "2025-08": 33823,
        "2025-09": 34134, "2025-10": 34759, "2025-11": 34894, "2025-12": 35396,
        "2026-01": 36016,
    },
    "alternativa_cinae": "https://www.cinae.org.ec/estadisticas/",  # Power BI con texto extraible (boletin-ventas)
}

# ---- COLOMBIA ------------------------------------------------------------
COLOMBIA = {
    "andemos_interactivo": "https://www.andemos.org/registroinformesinteractivos",  # Power BI (no texto)
    "datos_gov_api": "https://www.datos.gov.co/resource/u3vn-bdcy.json",  # SIN marca, solo volumen
    "nota": "Ranking de marcas: ANDI/Fenalco o ANDEMOS. Extraccion via navegador o nota de prensa.",
}

MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
            "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
MESES_PE = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
            "Julio", "Agosto", "Setiembre", "Octubre", "Noviembre", "Diciembre"]  # AAP usa "Setiembre"

# ---- Normalizacion de nombres de marca (canon) --------------------------
# Distintas fuentes nombran la misma marca distinto (ALADDA usa "GWM"/"Gwm",
# CAVEM/AAP usan "Great Wall"). canon() unifica para que el join entre
# base_nacional, series mensuales y grupos_importadores no se rompa.
import unicodedata as _ud

ALIAS = {
    "GWM": "Great Wall", "GREAT WALL": "Great Wall", "GREATWALL": "Great Wall",
    "MMC": "Mitsubishi", "MITSUBISHI MOTORS": "Mitsubishi",
    "VW": "Volkswagen",
    "MERCEDES-BENZ": "Mercedes Benz", "MERCEDES BENZ": "Mercedes Benz",
    "DFM": "Dfsk", "SAIC MAXUS": "Maxus", "KGM": "Kgm", "SSANGYONG": "Kgm",
    "LAND ROVER": "Land Rover", "ZX AUTO": "Zx Auto", "ZXAUTO": "Zx Auto",
    "GREAT WALL MOTORS": "Great Wall",
}


def _strip_accents(s: str) -> str:
    return "".join(c for c in _ud.normalize("NFD", s) if _ud.category(c) != "Mn")


def canon(marca: str) -> str:
    """Forma canonica de un nombre de marca (Title Case + alias unificados)."""
    if not marca:
        return marca
    k = _strip_accents(str(marca)).strip().upper()
    if k in ALIAS:
        return ALIAS[k]
    return str(marca).strip().title()


# Marcas de origen chino (para el filtro "Origen" y la penetracion china)
MARCAS_CHINAS = {
    "Changan", "Jetour", "Geely", "Chery", "Dfsk", "Shineray", "Jac", "Foton",
    "Great Wall", "Haval", "Byd", "Jmc", "Forland", "Kyc", "King Long", "Dongfeng",
    "Gac", "Mg", "Baic", "Jaecoo", "Omoda", "Sinotruk", "Maxus", "Dfm", "Kaiyi",
    "Leapmotor", "Zeekr", "Neta", "Wuling", "Riddara", "Deepal",
}
