"""
handle.py — Conversor de PDF a EPUB
====================================
Flujo de trabajo (según instruc.md):
  1. Extracción inteligente con PyMuPDF: lectura de bloques de texto con
     coordenadas espaciales para eliminar encabezados, pies de página y
     números de página.
  2. Limpieza con Regex: reparar saltos de línea rotos, unir palabras
     separadas por guiones al final del renglón y detectar títulos.
  3. Conversión a EPUB con ebooklib.

Uso:
  py handle.py                         → Convierte todos los PDF en PDF/
  py handle.py "archivo.pdf"           → Convierte un PDF específico de PDF/
  py handle.py "C:/ruta/completa.pdf"  → Convierte un PDF con ruta absoluta
"""

from __future__ import annotations

import sys
import re
import io
from pathlib import Path

# Forzar salida UTF-8 en consolas Windows (cp1252)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import fitz  # PyMuPDF
from ebooklib import epub
from PIL import Image

# ──────────────────────────────────────────────────────────────────────
#  Rutas del proyecto
# ──────────────────────────────────────────────────────────────────────

DIRECTORIO_BASE = Path(__file__).parent
DIRECTORIO_PDF = DIRECTORIO_BASE / "PDF"
DIRECTORIO_SALIDA = DIRECTORIO_BASE / "epub-mobi" / "modificados"
DIRECTORIO_CARATULAS = DIRECTORIO_BASE / "caratulas"
DIRECTORIO_MD = DIRECTORIO_BASE / "md"


# ══════════════════════════════════════════════════════════════════════
#  PASO 1 — Extracción inteligente del texto
# ══════════════════════════════════════════════════════════════════════

def _calcular_tamano_cuerpo(documento: fitz.Document,
                            max_paginas_muestra: int = 40) -> float:
    """
    Calcula el tamaño de fuente del cuerpo del texto analizando las
    páginas del documento.  Se toma el tamaño de fuente más frecuente
    como referencia del cuerpo.

    Returns:
        Tamaño de fuente (en pt) más utilizado en el documento.
    """
    from collections import Counter
    conteo: Counter = Counter()

    paso = max(1, len(documento) // max_paginas_muestra)
    for num_pag in range(0, len(documento), paso):
        pagina = documento[num_pag]
        dic = pagina.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        for bloque in dic.get("blocks", []):
            for linea in bloque.get("lines", []):
                for span in linea.get("spans", []):
                    texto = span["text"].strip()
                    if len(texto) > 3:  # ignorar fragmentos muy cortos
                        tam = round(span["size"], 1)
                        conteo[tam] += len(texto)

    if not conteo:
        return 10.0  # valor por defecto razonable
    return conteo.most_common(1)[0][0]


def _detectar_encabezados_repetidos(documento: fitz.Document,
                                    margen_encabezado_y: float = 55,
                                    umbral_repeticion: int = 4
                                    ) -> set[str]:
    """
    Identifica textos que aparecen repetidamente en la zona de
    encabezado de las páginas (running headers).  Estos textos se
    eliminarán durante la extracción para no ensuciar el contenido.

    Returns:
        Conjunto de textos en minúsculas considerados running headers.
    """
    from collections import Counter
    conteo: Counter = Counter()

    for num_pag in range(len(documento)):
        pagina = documento[num_pag]
        bloques = pagina.get_text("blocks")
        for b in bloques:
            y_sup, y_inf = b[1], b[3]
            if y_inf < margen_encabezado_y and b[6] == 0:
                texto = b[4].strip()
                if texto:
                    conteo[texto.lower()] += 1

    return {txt for txt, n in conteo.items() if n >= umbral_repeticion}


import re
import fitz

def extraer_texto_pagina(pagina: fitz.Page, margen_superior: float,
                         margen_inferior: float,
                         tamano_cuerpo: float = 10.0,
                         factor_titulo: float = 1.4,
                         encabezados_repetidos: set[str] | None = None,
                         num_pagina: int = 0,
                         min_img_px: int = 50,
                         ) -> tuple[list[tuple[str, str, float, bool]], float]:
    """
    Extrae elementos crudos de la página junto con el margen base.
    Devuelve: (lista_de_elementos, base_x0)
    Cada elemento es: (tipo, contenido, x0, es_titulo)
    """
    if encabezados_repetidos is None:
        encabezados_repetidos = set()

    altura = pagina.rect.height
    limite_superior = altura * margen_superior
    limite_inferior = altura * (1 - margen_inferior)
    umbral_titulo = tamano_cuerpo * factor_titulo

    dic = pagina.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_PRESERVE_IMAGES)

    elementos_raw = []

    for bloque in dic.get("blocks", []):
        bbox = bloque["bbox"]
        y_sup, y_inf = bbox[1], bbox[3]

        if y_inf <= limite_superior or y_sup >= limite_inferior:
            continue

        y_centro = (y_sup + y_inf) / 2

        if bloque.get("type", 0) == 1:
            ancho = bloque.get("width", 0)
            alto = bloque.get("height", 0)
            if ancho >= min_img_px and alto >= min_img_px:
                img_list = pagina.get_images(full=True)
                mejor_xref = None
                mejor_dist = float("inf")
                for img_info in img_list:
                    xref = img_info[0]
                    try: rects = pagina.get_image_rects(xref)
                    except Exception: continue
                    for r in rects:
                        dist = abs(r.y0 - y_sup) + abs(r.x0 - bbox[0])
                        if dist < mejor_dist:
                            mejor_dist = dist
                            mejor_xref = xref
                if mejor_xref is not None:
                    img_id = f"p{num_pagina:04d}_{mejor_xref}"
                    elementos_raw.append((y_centro, "img", f"{{{{IMG:{img_id}}}}}", 0.0, False))
            continue

        for linea in bloque.get("lines", []):
            textos_linea = []
            tamano_max = 0.0
            for span in linea.get("spans", []):
                texto = span["text"]
                if texto.strip():
                    textos_linea.append(texto)
                    if span["size"] > tamano_max:
                        tamano_max = span["size"]

            texto_completo = "".join(textos_linea).strip()
            if not texto_completo:
                continue
            if texto_completo.isdigit() and len(texto_completo) <= 4:
                continue

            tiene_palabras = len(re.findall(r"[a-zA-Z\u00c0-\u024f]{2,}", texto_completo)) >= 1
            es_mayus_decorativo = (texto_completo == texto_completo.upper() and len(texto_completo.split()) <= 2)
            tiene_chars_raros = bool(re.search(r"[^\x20-\x7e\u00a0-\u024f\u2010-\u2027\u2032-\u2037\u2018-\u201f]", texto_completo))
            es_titulo_por_fuente = (tamano_max >= umbral_titulo and len(texto_completo) < 120 and tiene_palabras and not es_mayus_decorativo and not tiene_chars_raros)

            x0_linea = linea["bbox"][0]
            y_linea = linea["bbox"][1]
            
            if es_titulo_por_fuente:
                elementos_raw.append((y_linea, "titulo", f"## {texto_completo}", x0_linea, True))
            elif texto_completo.lower() in encabezados_repetidos:
                continue
            else:
                elementos_raw.append((y_linea, "txt", texto_completo, x0_linea, False))

    # Ordenar verticalmente los elementos de esta página
    elementos_raw.sort(key=lambda e: e[0])
    
    # Calcular el margen base izquierdo de ESTA página
    lineas_cuerpo = [e for e in elementos_raw if e[1] == "txt" and not e[4] and len(e[2]) > 15]
    base_x0 = min([e[3] for e in lineas_cuerpo]) if lineas_cuerpo else 0.0

    # Limpiar la tupla para devolver solo lo necesario: (tipo, contenido, x0, es_titulo)
    elementos_finales = [(e[1], e[2], e[3], e[4]) for e in elementos_raw]

    return elementos_finales, base_x0


def extraer_imagenes_pdf(ruta_pdf: Path,
                         min_img_px: int = 50) -> dict[str, tuple[bytes, str]]:
    """
    Extrae todas las imágenes del PDF que superen el tamaño mínimo.

    Returns:
        Dict  img_id → (bytes_imagen, extension)
        donde img_id tiene formato ``pNNNN_xref``.
    """
    documento = fitz.open(str(ruta_pdf))
    imagenes: dict[str, tuple[bytes, str]] = {}
    cache_xref: dict[int, tuple[bytes, str]] = {}

    for num_pag in range(len(documento)):
        pagina = documento[num_pag]
        for img_info in pagina.get_images(full=True):
            xref = img_info[0]
            w, h = img_info[2], img_info[3]
            if w < min_img_px or h < min_img_px:
                continue
            img_id = f"p{num_pag:04d}_{xref}"
            if xref in cache_xref:
                imagenes[img_id] = cache_xref[xref]
                continue
            try:
                base = documento.extract_image(xref)
            except Exception:
                continue
            ext = base.get("ext", "png")
            datos = (base["image"], ext)
            cache_xref[xref] = datos
            imagenes[img_id] = datos

    documento.close()
    return imagenes


def extraer_texto_pdf(ruta_pdf: Path,
                      margen_superior: float = 0.08,
                      margen_inferior: float = 0.08) -> tuple[list[str], dict[str, tuple[bytes, str]]]:
    """
    Abre un PDF, une todas las páginas de forma fluida resolviendo cortes 
    de párrafo en saltos de página, y extrae las imágenes.
    """
    documento = fitz.open(str(ruta_pdf))

    tamano_cuerpo = _calcular_tamano_cuerpo(documento)
    encabezados_rep = _detectar_encabezados_repetidos(documento)

    # Aquí acumularemos los elementos de TODO el libro en orden continuo
    # Formato de la tupla: (tipo, contenido, es_sangria)
    flujo_global_elementos = []
    
    for numero in range(len(documento)):
        pagina = documento[numero]
        elementos_pag, base_x0 = extraer_texto_pagina(
            pagina, margen_superior, margen_inferior,
            tamano_cuerpo=tamano_cuerpo,
            encabezados_repetidos=encabezados_rep,
            num_pagina=numero,
        )
        
        # Procesamos los elementos de la página usando su propio base_x0
        for tipo, contenido, x0, es_titulo in elementos_pag:
            if tipo == "txt":
                umbral_sangria = 8.0
                es_sangria = (x0 - base_x0) > umbral_sangria
                flujo_global_elementos.append((tipo, contenido, es_sangria))
            else:
                # Títulos e imágenes no requieren cálculo de sangría
                flujo_global_elementos.append((tipo, contenido, False))

    documento.close()

    # ── Reconstrucción global de párrafos (Omitiendo fronteras de página) ──
    bloques_finales = []
    parrafo_actual = []

    for tipo, contenido, es_sangria in flujo_global_elementos:
        if tipo == "img":
            if parrafo_actual:
                bloques_finales.append(" ".join(parrafo_actual))
                parrafo_actual = []
            bloques_finales.append(contenido)
            
        elif tipo == "titulo":
            if parrafo_actual:
                bloques_finales.append(" ".join(parrafo_actual))
                parrafo_actual = []
            bloques_finales.append(contenido)
            
        else:  # tipo == "txt"
            # Si la línea tiene sangría, cerramos el párrafo anterior e iniciamos uno nuevo
            if es_sangria:
                if parrafo_actual:
                    bloques_finales.append(" ".join(parrafo_actual))
                    parrafo_actual = []
            
            # Unimos las líneas pertenecientes al mismo párrafo con un espacio en blanco
            parrafo_actual.append(contenido)

    # Guardar el último párrafo del libro
    if parrafo_actual:
        bloques_finales.append(" ".join(parrafo_actual))

    # Generamos el Markdown unificado con doble salto de línea
    markdown_completo = "\n\n".join(bloques_finales)

    # Extraer imágenes en pasada separada
    imagenes = extraer_imagenes_pdf(ruta_pdf)

    # Devolvemos el markdown completo dentro de la lista
    return [markdown_completo], imagenes


# ══════════════════════════════════════════════════════════════════════
#  PASO 2 — Limpieza y estructuración con Regex
# ══════════════════════════════════════════════════════════════════════

def reparar_guiones_de_corte(texto: str) -> str:
    """
    Une palabras que fueron cortadas con guión al final del renglón.

    Ejemplo:
        "progra-\nmaciòn"  →  "programaciòn"
    """
    return re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", texto)


def reparar_saltos_de_linea(texto: str) -> str:
    """
    Convierte saltos de línea simples (dentro de un mismo párrafo)
    en espacios, preservando los saltos dobles como separación real
    entre párrafos.

    Lógica:
      - Un salto doble (\n\n) indica cambio de párrafo → se preserva.
      - Un salto simple (\n) dentro de un párrafo → se reemplaza por
        un espacio para reconstruir la oración.
    """
    # Proteger los saltos dobles
    texto = re.sub(r"\n{2,}", "\n\n", texto)
    # Reemplazar saltos simples por espacios
    texto = re.sub(r"(?<!\n)\n(?!\n)", " ", texto)
    return texto


def detectar_titulos(texto: str) -> str:
    """
    Detecta líneas que son títulos o encabezados de capítulo y les
    asigna formato Markdown (##).  No vuelve a marcar líneas que ya
    tengan el prefijo ``## `` (insertado durante la extracción por
    análisis de fuente).

    Heurísticas adicionales (complementan la detección por fuente):
      - Líneas cortas (< 80 caracteres) completamente en mayúsculas.
      - Líneas que comienzan con palabras clave de capítulo.
      - Líneas con formato "N  Título" (número + título corto).
    """
    lineas = texto.split("\n")
    resultado = []

    patron_capitulo = re.compile(
        r"^(Cap[íi]tulo|Chapter|Parte|Secci[oó]n|Introducci[oó]n|"
        r"Conclusi[oó]n|Ep[ií]logo|Pr[oó]logo|Prefacio|Ap[eé]ndice|"
        r"Acknowledgements?|Bibliography|Index|Contents)"
        r"(\s+\d+)?(\s*:\s+.+)?\s*$",
    )

    # Patrón para títulos numerados: "1 The Reach of Explanations"
    patron_numerado = re.compile(
        r"^\d{1,3}\s{1,4}[A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ\s',:\-]+$"
    )

    for linea in lineas:
        linea_limpia = linea.strip()

        if not linea_limpia:
            resultado.append("")
            continue

        # Ya marcada como título → conservar
        if linea_limpia.startswith("## "):
            resultado.append(linea_limpia)
            continue

        es_titulo = False

        # Líneas cortas en mayúsculas con al menos 2 palabras alfabéticas
        # (excluir entradas de índice con números como "ENIAC 139")
        if (len(linea_limpia) < 80
                and linea_limpia == linea_limpia.upper()
                and not re.search(r"\d", linea_limpia)
                and len(re.findall(r"[A-ZÁÉÍÓÚÑ]{3,}", linea_limpia)) >= 2):
            es_titulo = True

        # Líneas que son exactamente un título de capítulo
        # (solo cuando comienzan con mayúscula, no mid-sentence)
        if patron_capitulo.match(linea_limpia):
            es_titulo = True

        # Títulos numerados cortos (ej. "3 The Spark")
        if (len(linea_limpia) < 80
                and patron_numerado.match(linea_limpia)):
            es_titulo = True

        if es_titulo:
            resultado.append(f"## {linea_limpia}")
        else:
            resultado.append(linea_limpia)

    return "\n".join(resultado)


def limpiar_espacios_multiples(texto: str) -> str:
    """Reduce múltiples espacios consecutivos a uno solo."""
    return re.sub(r" {2,}", " ", texto)


def _proteger_titulos_antes_de_saltos(texto: str) -> str:
    """
    Asegura que las líneas marcadas como ``## título`` o placeholders
    de imagen ``{{IMG:...}}`` tengan un salto doble antes y después,
    para que ``reparar_saltos_de_linea`` no las fusione con el párrafo
    adyacente.
    """
    lineas = texto.split("\n")
    resultado = []
    for linea in lineas:
        limpia = linea.strip()
        if limpia.startswith("## ") or limpia.startswith("{{IMG:"):
            if resultado and resultado[-1].strip():
                resultado.append("")
            resultado.append(linea)
            resultado.append("")
        else:
            resultado.append(linea)
    return "\n".join(resultado)


def limpiar_texto(texto_crudo: str) -> str:
    """
    Pipeline completo de limpieza:
      1. Reparar guiones de corte de renglón.
      2. Detectar títulos adicionales con heurísticas de regex.
      3. Proteger títulos (## ) con saltos dobles.
      4. Reparar saltos de línea rotos (unir párrafos).
      5. Limpiar espacios múltiples.

    Nota: los títulos detectados por tamaño de fuente ya vienen
    marcados desde la extracción (Paso 1).  Aquí se aplican
    heurísticas adicionales *antes* de unir párrafos, para que
    los títulos no se fusionen con el texto circundante.
    """
    texto = reparar_guiones_de_corte(texto_crudo)
    texto = detectar_titulos(texto)
    texto = _proteger_titulos_antes_de_saltos(texto)
    texto = reparar_saltos_de_linea(texto)
    texto = limpiar_espacios_multiples(texto)
    return texto.strip()


# ══════════════════════════════════════════════════════════════════════
#  PASO 3 — Generación del EPUB
# ══════════════════════════════════════════════════════════════════════

def markdown_a_html(texto_md: str,
                    imagenes: dict[str, tuple[bytes, str]] | None = None,
                    ) -> str:
    """
    Convierte el texto en formato Markdown simplificado a HTML.
    Soporta: títulos ##, párrafos, imágenes {{IMG:id}}.
    """
    if imagenes is None:
        imagenes = {}
    patron_img = re.compile(r"\{\{IMG:(.+?)\}\}")

    def _img_tag(img_id: str) -> str:
        ext = imagenes.get(img_id, (b"", "png"))[1]
        return (f'<div class="img-container">'
                f'<img src="images/{img_id}.{ext}" alt="" />'
                f'</div>')

    lineas = texto_md.split("\n")
    html_partes = []
    parrafo_actual = []

    def cerrar_parrafo():
        if parrafo_actual:
            contenido = " ".join(parrafo_actual).strip()
            if contenido:
                contenido = patron_img.sub(
                    lambda m: f'</p>{_img_tag(m.group(1))}<p>',
                    contenido,
                )
                html_partes.append(f"<p>{contenido}</p>")
            parrafo_actual.clear()

    for linea in lineas:
        linea = linea.strip()
        if not linea:
            cerrar_parrafo()
            continue
        if linea.startswith("## "):
            cerrar_parrafo()
            html_partes.append(f"<h2>{linea[3:].strip()}</h2>")
        elif linea.startswith("### "):
            cerrar_parrafo()
            html_partes.append(f"<h3>{linea[4:].strip()}</h3>")
        elif patron_img.fullmatch(linea):
            cerrar_parrafo()
            html_partes.append(_img_tag(patron_img.fullmatch(linea).group(1)))
        else:
            parrafo_actual.append(linea)

    cerrar_parrafo()
    return "\n".join(html_partes)


def _consolidar_titulos_consecutivos(texto: str) -> str:
    """
    Cuando un capítulo tiene número y nombre en líneas separadas
    (ej. ``## 1`` seguido de ``## The Reach of Explanations``), los
    une en un solo título: ``## 1 — The Reach of Explanations``.

    También une títulos de capítulo que se partieron en varias líneas
    (ej. ``## A Physicist's History of Bad`` seguido de ``## Philosophy``).
    """
    lineas = texto.split("\n")
    resultado = []
    i = 0
    while i < len(lineas):
        linea = lineas[i].strip()
        if linea.startswith("## "):
            titulo_actual = linea[3:].strip()
            # Mirar si la siguiente línea no vacía también es un título
            j = i + 1
            while j < len(lineas) and not lineas[j].strip():
                j += 1
            if (j < len(lineas)
                    and lineas[j].strip().startswith("## ")):
                siguiente = lineas[j].strip()[3:].strip()
                if titulo_actual.isdigit():
                    # Unir: "## 1" + "## The Spark" → "## 1 — The Spark"
                    resultado.append(f"## {titulo_actual} — {siguiente}")
                    i = j + 1
                    continue
                else:
                    # Unir títulos partidos en varias líneas, solo si
                    # el primero termina con una palabra que indica
                    # continuación (preposición, artículo, adjetivo...)
                    ultima_palabra = titulo_actual.split()[-1].lower()
                    palabras_continuacion = {
                        "of", "the", "a", "an", "and", "or", "in", "on",
                        "for", "to", "with", "by", "at", "from", "as",
                        "de", "del", "la", "el", "los", "las", "un",
                        "una", "y", "e", "o", "en", "con", "por", "bad",
                        "good", "new", "old",
                    }
                    if ultima_palabra in palabras_continuacion:
                        resultado.append(f"## {titulo_actual} {siguiente}")
                        i = j + 1
                        continue
        resultado.append(lineas[i])
        i += 1
    return "\n".join(resultado)


def dividir_en_capitulos(texto_limpio: str) -> list[dict]:
    """
    Divide el texto limpio en capítulos usando los títulos (##) como
    separadores.

    Returns:
        Lista de diccionarios con las claves:
          - "titulo": texto del título del capítulo.
          - "contenido": texto Markdown del capítulo (incluye el título).
    """
    # Consolidar títulos consecutivos (número + nombre)
    texto_limpio = _consolidar_titulos_consecutivos(texto_limpio)

    patron = re.compile(r"^## (.+)$", re.MULTILINE)
    posiciones = [(m.start(), m.group(1)) for m in patron.finditer(texto_limpio)]

    if not posiciones:
        # No se detectaron capítulos → un solo capítulo con todo el texto
        return [{"titulo": "Contenido", "contenido": texto_limpio}]

    capitulos = []
    for i, (inicio, titulo) in enumerate(posiciones):
        fin = posiciones[i + 1][0] if i + 1 < len(posiciones) else len(texto_limpio)
        fragmento = texto_limpio[inicio:fin].strip()
        capitulos.append({"titulo": titulo, "contenido": fragmento})

    # Si hay texto antes del primer título, agregarlo como "Preliminares"
    if posiciones[0][0] > 0:
        preliminar = texto_limpio[: posiciones[0][0]].strip()
        if preliminar:
            capitulos.insert(
                0, {"titulo": "Preliminares", "contenido": preliminar}
            )

    return capitulos


def buscar_caratula(nombre_pdf: str) -> Path | None:
    """
    Busca una imagen de carátula en el directorio de carátulas.
    Compara de forma flexible (sin extensión, en minúsculas, reemplazando
    guiones/espacios).

    Returns:
        Ruta a la imagen encontrada, o None si no existe.
    """
    if not DIRECTORIO_CARATULAS.exists():
        return None

    nombre_base = Path(nombre_pdf).stem.lower().replace("-", " ").replace("_", " ")
    extensiones_validas = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

    for archivo in DIRECTORIO_CARATULAS.iterdir():
        if archivo.suffix.lower() not in extensiones_validas:
            continue
        nombre_archivo = archivo.stem.lower().replace("-", " ").replace("_", " ")
        # Coincidencia parcial: si uno contiene al otro
        if nombre_base in nombre_archivo or nombre_archivo in nombre_base:
            return archivo

    return None


def crear_epub(titulo_libro: str, capitulos: list[dict],
               ruta_salida: Path, ruta_caratula: Path | None = None,
               imagenes: dict[str, tuple[bytes, str]] | None = None,
               ) -> None:
    """
    Crea un archivo EPUB a partir de los capítulos procesados.

    Args:
        titulo_libro:   Título del libro (usado como metadato).
        capitulos:      Lista de dicts con "titulo" y "contenido".
        ruta_salida:    Ruta completa del archivo .epub de salida.
        ruta_caratula:  Ruta opcional a una imagen de portada.
        imagenes:       Dict img_id → (bytes, ext) de imágenes del PDF.
    """
    if imagenes is None:
        imagenes = {}

    libro = epub.EpubBook()

    # ── Metadatos ──
    libro.set_identifier(f"pdf2epub-{titulo_libro.lower().replace(' ', '-')}")
    libro.set_title(titulo_libro)
    libro.set_language("es")
    libro.add_author("Desconocido")

    # ── Estilos CSS para el contenido ──
    estilo_css = epub.EpubItem(
        uid="estilo_principal",
        file_name="style/default.css",
        media_type="text/css",
        content="""
body {
    font-family: Georgia, "Times New Roman", serif;
    line-height: 1.6;
    margin: 1em;
    color: #1a1a1a;
}
h2 {
    font-size: 1.4em;
    margin-top: 2em;
    margin-bottom: 0.5em;
    color: #2c3e50;
    border-bottom: 1px solid #ccc;
    padding-bottom: 0.3em;
}
p {
    text-align: justify;
    margin-bottom: 0.8em;
    text-indent: 1.5em;
}
p:first-of-type {
    text-indent: 0;
}
.img-container {
    text-align: center;
    margin: 1.5em 0;
    page-break-inside: avoid;
}
.img-container img {
    max-width: 100%;
    height: auto;
}
""".encode("utf-8"),
    )
    libro.add_item(estilo_css)

    # ── Carátula (si existe) ──
    if ruta_caratula and ruta_caratula.exists():
        with open(ruta_caratula, "rb") as img_file:
            contenido_imagen = img_file.read()

        extension = ruta_caratula.suffix.lower()
        tipo_media = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }.get(extension, "image/jpeg")

        nombre_portada = f"cover{extension}"
        imagen_portada = epub.EpubItem(
            uid="portada-imagen",
            file_name=f"images/{nombre_portada}",
            media_type=tipo_media,
            content=contenido_imagen,
        )
        libro.add_item(imagen_portada)
        libro.set_cover(nombre_portada, contenido_imagen)
        print(f"  📷 Carátula agregada: {ruta_caratula.name}")

    # ── Agregar imágenes extraídas del PDF ──
    mapa_ext_media = {
        "jpeg": "image/jpeg", "jpg": "image/jpeg",
        "png": "image/png", "gif": "image/gif",
        "bmp": "image/bmp", "tiff": "image/tiff",
        "jxr": "image/jxr", "jpx": "image/jpx",
    }
    for img_id, (img_bytes, ext) in imagenes.items():
        media = mapa_ext_media.get(ext, f"image/{ext}")
        item = epub.EpubItem(
            uid=f"img-{img_id}",
            file_name=f"images/{img_id}.{ext}",
            media_type=media,
            content=img_bytes,
        )
        libro.add_item(item)

    # ── Crear capítulos EPUB ──
    secciones_epub = []
    tabla_contenido = []

    for indice, capitulo in enumerate(capitulos):
        nombre_archivo = f"capitulo_{indice:03d}.xhtml"
        seccion = epub.EpubHtml(
            title=capitulo["titulo"],
            file_name=nombre_archivo,
            lang="es",
        )

        html_contenido = markdown_a_html(capitulo["contenido"], imagenes)
        seccion.content = f"""
<html>
<head><link rel="stylesheet" href="style/default.css" /></head>
<body>
{html_contenido}
</body>
</html>
""".encode("utf-8")

        seccion.add_item(estilo_css)
        libro.add_item(seccion)
        secciones_epub.append(seccion)
        tabla_contenido.append(seccion)

    # ── Tabla de contenido y orden de lectura ──
    libro.toc = tabla_contenido
    libro.add_item(epub.EpubNcx())
    libro.add_item(epub.EpubNav())
    libro.spine = ["nav"] + secciones_epub

    # ── Guardar ──
    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(ruta_salida), libro)


# ══════════════════════════════════════════════════════════════════════
#  Pipeline principal
# ══════════════════════════════════════════════════════════════════════

def convertir_pdf_a_epub(ruta_pdf: Path, usar_md_local: bool = False) -> None:
    """
    Ejecuta el pipeline completo para un solo archivo PDF:
      1. Extraer texto (filtrar encabezados/pies).
      2. Limpiar y estructurar con regex.
      3. Guardar en carpeta md/ (y permitir cargar desde ahí).
      4. Dividir en capítulos.
      5. Generar EPUB.
    """
    nombre = ruta_pdf.stem
    print(f"\n{'─' * 60}")
    print(f"  📖 Procesando: {nombre}")
    print(f"{'─' * 60}")

    ruta_md = DIRECTORIO_MD / f"{nombre}.md"

    if usar_md_local and ruta_md.exists():
        print(f"  ⏳ Leyendo texto editado desde: {ruta_md.name}")
        texto_limpio = ruta_md.read_text(encoding="utf-8")
        
        print("  ⏳ Extrayendo solo imágenes del PDF...")
        imagenes = extraer_imagenes_pdf(ruta_pdf)
        print(f"  ✅ {len(imagenes)} imagen(es) encontrada(s).")
    else:
        # Paso 1: Extracción
        print("  ⏳ Extrayendo texto e imágenes del PDF...")
        paginas, imagenes = extraer_texto_pdf(ruta_pdf)

        if not paginas:
            print("  ⚠️  No se pudo extraer texto. El PDF puede ser solo imágenes.")
            return

        print(f"  ✅ {len(paginas)} páginas extraídas, {len(imagenes)} imagen(es) encontrada(s).")

        # Paso 2: Limpieza
        print("  ⏳ Limpiando y estructurando texto...")
        texto_completo = "\n\n".join(paginas)
        texto_limpio = limpiar_texto(texto_completo)

        # Paso 3: Guardar Markdown
        DIRECTORIO_MD.mkdir(parents=True, exist_ok=True)
        ruta_md.write_text(texto_limpio, encoding="utf-8")
        print(f"  ✅ Archivo Markdown guardado en: md/{ruta_md.name}")

    # Paso 4: Dividir en capítulos
    capitulos = dividir_en_capitulos(texto_limpio)
    print(f"  ✅ {len(capitulos)} capítulo(s) detectado(s).")

    # Buscar carátula opcional
    caratula = buscar_caratula(ruta_pdf.name)

    # Paso 5: Generar EPUB
    ruta_salida = DIRECTORIO_SALIDA / f"{nombre}.epub"
    print(f"  ⏳ Generando EPUB...")
    crear_epub(nombre, capitulos, ruta_salida, caratula, imagenes)
    print(f"  ✅ EPUB guardado: {ruta_salida}")


def main():
    """
    Punto de entrada.

    Prioridad:
      1. Si RUTA_PDF tiene un valor → convierte ese archivo.
      2. Si se pasa un argumento por línea de comandos → lo usa.
      3. Sin nada → convierte todos los PDF en PDF/
    """
    # ── Poner aquí la ruta del PDF a convertir (nombre o ruta completa) ──
    RUTA_PDF = "The Beginning of Infinity ( PDFDrive ).pdf"  # Ejemplo: "El acto de crear - Rick Rubin.pdf"

    if len(sys.argv) > 1:
        argumento = sys.argv[1]
    elif RUTA_PDF:
        argumento = RUTA_PDF
    else:
        argumento = ""

    if argumento:
        ruta = Path(argumento)

        # Si es solo un nombre de archivo, buscarlo en la carpeta PDF/ o md/
        if not ruta.is_absolute() and not ruta.exists():
            if ruta.suffix.lower() == ".md":
                ruta = DIRECTORIO_MD / argumento
            else:
                ruta = DIRECTORIO_PDF / argumento

        if not ruta.exists():
            print(f"❌ Archivo no encontrado: {ruta}")
            sys.exit(1)

        if ruta.suffix.lower() == ".md":
            ruta_pdf = DIRECTORIO_PDF / f"{ruta.stem}.pdf"
            if not ruta_pdf.exists():
                print(f"❌ No se encontró el PDF correspondiente: {ruta_pdf}")
                sys.exit(1)
            convertir_pdf_a_epub(ruta_pdf, usar_md_local=True)
        else:
            convertir_pdf_a_epub(ruta)
    else:
        # Convertir todos los PDFs en la carpeta
        archivos_pdf = sorted(DIRECTORIO_PDF.glob("*.pdf"))

        if not archivos_pdf:
            print(f"❌ No se encontraron archivos PDF en: {DIRECTORIO_PDF}")
            sys.exit(1)

        print(f"📚 Encontrados {len(archivos_pdf)} archivos PDF para convertir.\n")

        for ruta_pdf in archivos_pdf:
            try:
                convertir_pdf_a_epub(ruta_pdf)
            except Exception as error:
                print(f"  ❌ Error procesando {ruta_pdf.name}: {error}")

    print(f"\n{'═' * 60}")
    print("  🏁 Proceso finalizado.")
    print(f"{'═' * 60}")


if __name__ == "__main__":
    main()
