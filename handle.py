from __future__ import annotations

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

import sys
import re
from pathlib import Path

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


# ══════════════════════════════════════════════════════════════════════
#  PASO 1 — Extracción inteligente del texto
# ══════════════════════════════════════════════════════════════════════

def extraer_texto_pagina(pagina: fitz.Page, margen_superior: float,
                         margen_inferior: float) -> str:
    """
    Extrae texto de una página descartando bloques que estén dentro de
    las zonas de encabezado (margen superior) o pie de página (margen
    inferior).  Esto elimina automáticamente números de página,
    encabezados repetidos y otros artefactos de impresión.

    Args:
        pagina:           Objeto Page de PyMuPDF.
        margen_superior:  Fracción de la altura de la página que se
                          considera zona de encabezado (ej. 0.06 = 6%).
        margen_inferior:  Fracción de la altura que se considera zona
                          de pie de página (ej. 0.06 = 6%).

    Returns:
        Texto limpio de la página como cadena.
    """
    altura = pagina.rect.height
    limite_superior = altura * margen_superior
    limite_inferior = altura * (1 - margen_inferior)

    bloques = pagina.get_text("blocks")  # (x0, y0, x1, y1, texto, ...)
    lineas_validas = []

    for bloque in bloques:
        # bloque[3] = y1 (borde inferior del bloque)
        # bloque[1] = y0 (borde superior del bloque)
        y_superior = bloque[1]
        y_inferior = bloque[3]

        # Descartar si el bloque está en la zona de encabezado o pie
        if y_inferior <= limite_superior:
            continue
        if y_superior >= limite_inferior:
            continue

        # bloque[4] contiene el texto; bloque[6] indica si es imagen (1)
        if bloque[6] == 0:  # Solo bloques de texto
            lineas_validas.append(bloque[4].strip())

    return "\n".join(lineas_validas)


def extraer_texto_pdf(ruta_pdf: Path,
                      margen_superior: float = 0.06,
                      margen_inferior: float = 0.06) -> list[str]:
    """
    Abre un PDF y extrae el texto de cada página como una lista de
    cadenas, descartando encabezados y pies de página.

    Returns:
        Lista donde cada elemento es el texto limpio de una página.
    """
    documento = fitz.open(str(ruta_pdf))
    paginas_texto = []

    for numero in range(len(documento)):
        pagina = documento[numero]
        texto = extraer_texto_pagina(pagina, margen_superior, margen_inferior)
        if texto.strip():
            paginas_texto.append(texto)

    documento.close()
    return paginas_texto


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
    Intenta detectar líneas que son títulos o encabezados de capítulo
    y les asigna formato Markdown (##).

    Heurísticas:
      - Líneas cortas (< 80 caracteres) completamente en mayúsculas.
      - Líneas que comienzan con "Capítulo", "CAPÍTULO", "Chapter",
        "Parte", "Sección", etc.
    """
    lineas = texto.split("\n")
    resultado = []

    patron_capitulo = re.compile(
        r"^(cap[íi]tulo|chapter|parte|secci[oó]n|introducci[oó]n|"
        r"conclusi[oó]n|ep[ií]logo|pr[oó]logo|prefacio|ap[eé]ndice)"
        r"\b",
        re.IGNORECASE,
    )

    for linea in lineas:
        linea_limpia = linea.strip()

        if not linea_limpia:
            resultado.append("")
            continue

        es_titulo = False

        # Líneas cortas en mayúsculas → posible título
        if (len(linea_limpia) < 80
                and linea_limpia == linea_limpia.upper()
                and re.search(r"[A-ZÁÉÍÓÚÑ]", linea_limpia)):
            es_titulo = True

        # Líneas que empiezan con palabras clave de capítulo
        if patron_capitulo.match(linea_limpia):
            es_titulo = True

        if es_titulo:
            resultado.append(f"## {linea_limpia}")
        else:
            resultado.append(linea_limpia)

    return "\n".join(resultado)


def limpiar_espacios_multiples(texto: str) -> str:
    """Reduce múltiples espacios consecutivos a uno solo."""
    return re.sub(r" {2,}", " ", texto)


def limpiar_texto(texto_crudo: str) -> str:
    """
    Pipeline completo de limpieza:
      1. Reparar guiones de corte de renglón.
      2. Reparar saltos de línea rotos (unir párrafos).
      3. Detectar y marcar títulos con formato Markdown.
      4. Limpiar espacios múltiples.
    """
    texto = reparar_guiones_de_corte(texto_crudo)
    texto = reparar_saltos_de_linea(texto)
    texto = detectar_titulos(texto)
    texto = limpiar_espacios_multiples(texto)
    return texto.strip()


# ══════════════════════════════════════════════════════════════════════
#  PASO 3 — Generación del EPUB
# ══════════════════════════════════════════════════════════════════════

def markdown_a_html(texto_md: str) -> str:
    """
    Convierte el texto en formato Markdown simplificado a HTML
    para insertarlo en el EPUB.

    Soporta:
      - Títulos ##
      - Párrafos separados por líneas en blanco
    """
    lineas = texto_md.split("\n")
    html_partes = []
    parrafo_actual = []

    def cerrar_parrafo():
        if parrafo_actual:
            contenido = " ".join(parrafo_actual).strip()
            if contenido:
                html_partes.append(f"<p>{contenido}</p>")
            parrafo_actual.clear()

    for linea in lineas:
        linea = linea.strip()

        if not linea:
            cerrar_parrafo()
            continue

        if linea.startswith("## "):
            cerrar_parrafo()
            titulo = linea[3:].strip()
            html_partes.append(f"<h2>{titulo}</h2>")
        else:
            parrafo_actual.append(linea)

    cerrar_parrafo()
    return "\n".join(html_partes)


def dividir_en_capitulos(texto_limpio: str) -> list[dict]:
    """
    Divide el texto limpio en capítulos usando los títulos (##) como
    separadores.

    Returns:
        Lista de diccionarios con las claves:
          - "titulo": texto del título del capítulo.
          - "contenido": texto Markdown del capítulo (incluye el título).
    """
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
               ruta_salida: Path, ruta_caratula: Path | None = None) -> None:
    """
    Crea un archivo EPUB a partir de los capítulos procesados.

    Args:
        titulo_libro:   Título del libro (usado como metadato).
        capitulos:      Lista de dicts con "titulo" y "contenido".
        ruta_salida:    Ruta completa del archivo .epub de salida.
        ruta_caratula:  Ruta opcional a una imagen de portada.
    """
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

        html_contenido = markdown_a_html(capitulo["contenido"])
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

def convertir_pdf_a_epub(ruta_pdf: Path) -> None:
    """
    Ejecuta el pipeline completo para un solo archivo PDF:
      1. Extraer texto (filtrar encabezados/pies).
      2. Limpiar y estructurar con regex.
      3. Dividir en capítulos.
      4. Generar EPUB.
    """
    nombre = ruta_pdf.stem
    print(f"\n{'─' * 60}")
    print(f"  📖 Procesando: {ruta_pdf.name}")
    print(f"{'─' * 60}")

    # Paso 1: Extracción
    print("  ⏳ Extrayendo texto del PDF...")
    paginas = extraer_texto_pdf(ruta_pdf)

    if not paginas:
        print("  ⚠️  No se pudo extraer texto. El PDF puede ser solo imágenes.")
        return

    print(f"  ✅ {len(paginas)} páginas extraídas.")

    # Paso 2: Limpieza
    print("  ⏳ Limpiando y estructurando texto...")
    texto_completo = "\n\n".join(paginas)
    texto_limpio = limpiar_texto(texto_completo)

    # Paso 3: Dividir en capítulos
    capitulos = dividir_en_capitulos(texto_limpio)
    print(f"  ✅ {len(capitulos)} capítulo(s) detectado(s).")

    # Buscar carátula opcional
    caratula = buscar_caratula(ruta_pdf.name)

    # Paso 4: Generar EPUB
    ruta_salida = DIRECTORIO_SALIDA / f"{nombre}.epub"
    print(f"  ⏳ Generando EPUB...")
    crear_epub(nombre, capitulos, ruta_salida, caratula)
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
    RUTA_PDF = "El acto de crear - Rick Rubin.pdf"  # Ejemplo: "El acto de crear - Rick Rubin.pdf"

    if RUTA_PDF:
        argumento = RUTA_PDF
    elif len(sys.argv) > 1:
        argumento = sys.argv[1]
    else:
        argumento = ""

    if argumento:
        ruta = Path(argumento)

        # Si es solo un nombre de archivo, buscarlo en la carpeta PDF/
        if not ruta.is_absolute() and not ruta.exists():
            ruta = DIRECTORIO_PDF / argumento

        if not ruta.exists():
            print(f"❌ Archivo no encontrado: {ruta}")
            sys.exit(1)

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
