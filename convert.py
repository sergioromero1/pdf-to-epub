import fitz  # PyMuPDF
import os
from ebooklib import epub
import re

def manage_toc_title(toc_title):
    if len(toc_title) > 40:
        toc_title = toc_title[:37] + "..."
    return toc_title

def convert_chapters_to_Epub(title, author, chapters_data, images_info, output_filename):
    # Datos iniciales de Epub
    book = epub.EpubBook()

    # Metadatos de Epub
    book.set_identifier(f"id-{title}")
    book.set_title(title)
    book.set_language('es')
    book.add_author(author)

    chapters = []
    toc = []

    for img in images_info:
        with open(img["path"], 'rb') as img_file:
            epub_img = epub.EpubImage()
            epub_img.file_name = f"images/{os.path.basename(img['path'])}"
            epub_img.content = img_file.read()
            book.add_item(epub_img)

    for idx, chapter_data in enumerate(chapters_data):
        
        html_with_images = chapter_data["content"]

        # Crear el capítulo
        chapter_id = f'chapter_{idx+1}'
        chapter = epub.EpubHtml(
            title=chapter_data["title"],
            file_name=f'{chapter_id}.xhtml'
        )
        
        chapter.content = f"<html><head></head><body>{html_with_images}</body></html>"
        book.add_item(chapter)
        chapters.append(chapter)

        toc_title = chapter_data["title"]
        toc_title = manage_toc_title(toc_title)
        toc.append(epub.Link(f'{chapter_id}.xhtml', toc_title, chapter_id))

    # Añadir capítulos al libro
    book.toc = toc

    # Añadir los capítulos a la columna vertebral del EPUB
    book.spine = chapters

    # Agregar un archivo CSS global para estilos consistentes
    global_css = epub.EpubItem(
        uid="style_default",
        file_name="style/default.css",
        media_type="text/css",
        content="""
            @page { margin: 10pt; }
            body { margin: 5%; padding: 0; }
            p { orphans: 2; widows: 2; }
        """
    )
    book.add_item(global_css)

    # Añadir navegación y tabla de contenidos
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # Escribir el archivo EPUB
    epub.write_epub(output_filename, book, {})

    return output_filename

def convert_pdf_to_chapters(pdf_path, pagina_inicial, pagina_final, rango_size_inicial_titulo,
        rango_size_final_titulo,
        rango_size_inicial_subtitulo,
        rango_size_final_subtitulo, bbox_X_paragraph,
        debug, incluir_imagenes,
        output_folder="contenido_extraido"):
    """
    block.keys() = 'number', 'type', 'bbox', 'lines'
    line.keys() = 'spans', 'wmode', 'dir', 'bbox'
    span.keys() = 'size', 'flags', 'bidi', 'char_flags', 'font', 'color', 'alpha', 'ascender', 'descender', 'text', 'origin', 'bbox'
    """

    # Lectura de Pdf
    doc = fitz.open(pdf_path)
        # Ajustar índices (PyPDF2 usa índices base 0)
    start_idx = pagina_inicial - 1
    end_idx = pagina_final - 1

    pagina_inicio_capitulo = False
    letra_grande_puesta = False

    chapter_html_content = ""
    chapter_title_html   = "" 
    chapter_html_content_prov = ""
    chapter_title_html_prov = ""

    images_info = []
    chapters_data = []
    new_chapter_detected = False
    current_chapter_title = ""
    current_chapter_content = ""
    current_chapter_images = []
    chapter_counter = 0

    prev_span_font = 0
    prev_span_size = 0
    next_span_font = 0
    next_span_size = 0

    for page_num in range(start_idx, end_idx + 1):
        img_idx = 0
        letra_inicial_de_capitulo = None
        # Extracción del pdf
        page = doc[page_num]
            # Extraer bloques de texto para identificar mejor párrafos y títulos
        page_blocks = page.get_text("dict")["blocks"]
            # Extraer imagenes

        # Ordenamos los bloques en el orden 'y'  que salen en la pagina
        page_blocks.sort(key=lambda b: b["bbox"][3])


        for pos_block, block in enumerate(page_blocks):
            es_bloque_imagen = block["type"] == 1
            if es_bloque_imagen and incluir_imagenes:
                imagen = block.get("image", [])
                img_filename = f"{output_folder}/page_{page_num+1}_img_{img_idx+1}.png"
                img_id = f"img_{page_num+1}_{img_idx+1}"
                with open(img_filename, "wb") as img_file:
                    img_file.write(imagen)

                images_info.append({
                                "path": img_filename,
                                "id": img_id,
                                "page": page_num + 1,
                                })

                chapter_html_content += f'<div class="image-container"><img src="images/{os.path.basename(img_filename)}" alt="Imagen del capítulo"/></div>\n\n'

            es_bloque_de_texto = block["type"] == 0 
            bbox_x = block["bbox"][0]
            if es_bloque_de_texto:

                lines = block.get("lines", [])
                cantidad_lineas_bloque = len(lines)
                if lines: 
                    block_html_text = ""
                    for line_idx, line in enumerate(lines):
                        spans = line.get("spans", [])

                        cantidad_de_spans_en_linea = len(spans)

                        line_html_text = ""

                        for span_idx, span in enumerate(spans):

                            span_text = span["text"]
                            span_font = span["font"]
                            span_font_size = span["size"]
                            span_is_bold = span_font.lower().find("bold") >= 0
                            span_is_italic = span_font.lower().find("italic") >= 0

                            if len(span_text) == 1:
                                if span_text.isalpha() and pos_block != 0:
                                    letra_inicial_de_capitulo = span_text
                                    continue

                            span_html_text = span_text
                            if span_is_bold:
                                span_html_text = f"<strong>{span_text}</strong>"
                            if span_is_italic:
                                span_html_text = f"<em>{span_text}</em>"

                            if cantidad_de_spans_en_linea > 1 and (span_is_bold or span_is_italic):
                                line_html_text += span_html_text + " "
                            else:
                                line_html_text += span_html_text
                            

                        block_html_text += line_html_text + " "                        

                    if pos_block > 0:
                        anterior_bloque_es_texto = page_blocks[pos_block - 1]["type"] == 0
                        if anterior_bloque_es_texto:
                            prev_span_font = page_blocks[pos_block - 1].get("lines", [])[0].get("spans", [])[0]["font"]
                            prev_span_size = page_blocks[pos_block - 1].get("lines", [])[0].get("spans", [])[0]["size"]
                    
                    if pos_block + 1 < len(page_blocks):
                        proximo_bloque_es_texto = page_blocks[pos_block + 1]["type"] == 0
                        if proximo_bloque_es_texto:
                            next_span_font = page_blocks[pos_block + 1].get("lines", [])[0].get("spans", [])[0]["font"]
                            next_span_size = page_blocks[pos_block + 1].get("lines", [])[0].get("spans", [])[0]["size"]
                    
                    clean_text = re.sub(r'</?(strong|em)>', '', block_html_text)
                    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
                    clean_words = clean_text.split()

                    letra_inicial_de_capitulo = (
                        len(clean_text) == 1
                        and clean_text.isalpha()
                    )

                    # Para Letra grande inicial de Capitulo
                    if (letra_inicial_de_capitulo 
                        and not letra_grande_puesta 
                        and len(clean_text) > 70):
                            block_html_text = f"{letra_inicial_de_capitulo}" + block_html_text
                            letra_grande_puesta = True
                            clean_text = re.sub(r'</?(strong|em)>', '', block_html_text)
                            clean_words = clean_text.split()

                    
                    block_is_membrete = (
                        span_font_size < 11
                        and not (clean_text[0] == '-' or clean_text[0] == '—')
                        and not span_is_bold
                        and not clean_text.endswith('.')
                        and len(clean_words) <= 12
                        and cantidad_lineas_bloque == 1
                        and (pos_block == 0 or block == page_blocks[-1])
                    )
                    
                    rit = rango_size_inicial_titulo
                    block_is_chapter_title = (
                                (span_font_size > rit or bbox_x > bbox_X_paragraph)
                                and span_is_bold
                                and cantidad_lineas_bloque <= 3 
                                and len(clean_text) < 70
                                and clean_text[0].isupper()
                                and len(clean_words) <= 12 
                                and not clean_text.endswith(('.', ';', ','))
                                and not re.match(r"^-+$", clean_text)
                                and clean_text.isupper()
                                and (bool(re.match(r'^\s*[1-9]*', clean_text)) or bool(re.match(r"^I*V*", clean_text)))
                    )

                    block_is_chapter_title_unique = (
                        block_is_chapter_title
                        and (pos_block == 0 or (pos_block == 1 and prev_span_size < 12))
                        and (span_font != prev_span_font
                        or span_font_size != prev_span_size)
                        and (span_font != next_span_font
                        or span_font_size != next_span_size)
                        and len(clean_text) > 3
                    )
                    

                    block_is_chapter_title_beggining = (
                        block_is_chapter_title
                        and (pos_block == 0 or pos_block == 1)
                        and span_font != prev_span_font
                        and span_font_size != prev_span_size
                        and (span_font == next_span_font
                        or span_font_size == next_span_size)
                    )

                    block_is_chapter_title_continuation = (
                        block_is_chapter_title
                        and (1 <= pos_block <= 5)
                        and (span_font == prev_span_font
                        or span_font_size == prev_span_size)
                        and (span_font == next_span_font
                        or span_font_size == next_span_size)
                    )

                    block_is_chapter_title_final = (
                        block_is_chapter_title
                        and (1 <= pos_block <= 5)                                                    
                        and (span_font == prev_span_font
                        or span_font_size == prev_span_size)
                        and (span_font != next_span_font
                        or span_font_size != next_span_size)
                    )

                    ris = rango_size_inicial_subtitulo
                    rfs = rango_size_final_subtitulo

                    block_is_subtitle = (
                                ris < span_font_size < rfs
                                and bbox_x == bbox_X_paragraph
                                and span_is_bold
                                and len(clean_words) <= 8 
                                and not clean_text.endswith(('.', ':', ';')) 
                                and len(clean_text) < 100
                                and cantidad_lineas_bloque == 1
                                and clean_text.isupper()
                                and (span_font_size != next_span_size or span_font != next_span_font)
                    )

                    block_is_parrafo_completo = (
                        clean_text.endswith('.')
                        and clean_text[0].isupper()
                        and len(clean_text) > 100
                    )

                    block_is_quote = (
                        not block_is_chapter_title
                        and not block_is_subtitle
                        and span_is_italic
                        and bbox_x > bbox_X_paragraph
                    )

                    block_is_quote_author = (
                        (clean_text[0] == '-' or clean_text[0] == '—')
                        and len(clean_text) < 40
                        and not clean_text.endswith('.')
                        and not block_is_chapter_title
                    )

                    block_is_parrafo_parte_inicial = (
                                not clean_text.endswith(('.','!'))
                                and not block_is_chapter_title
                                and not block_is_subtitle
                                and len(clean_text) > 50
                    )

                    block_is_parrafo_parte_final = (
                                not clean_text[0].isupper()
                                and not block_is_chapter_title
                                and not block_is_subtitle
                                and clean_text.endswith('.')
                    )

                    if debug:
                        print('clean_text.isupper()',clean_text.isupper())
                        print('cantidad_lineas_bloque <= 3',cantidad_lineas_bloque <= 3)
                        print('clean_text.isupper()',clean_text.isupper())
                        print('block_is_chapter_title', block_is_chapter_title)
                        print('pos_block', pos_block)
                        print('span_font', span_font)
                        print('span_font_size', span_font_size)
                        print('prev_span_font', prev_span_font)
                        print('prev_span_size', prev_span_size)
                        print('next_span_font', next_span_font)
                        print('next_span_size', next_span_size)
                        print('\n\n block_is_membrete', block_is_membrete)
                        print(' block_is_chapter_title_unique', block_is_chapter_title_unique)
                        print(' block_is_chapter_title_beggining', block_is_chapter_title_beggining)
                        print(' block_is_chapter_title_continuation', block_is_chapter_title_continuation)
                        print(' block_is_chapter_title_final', block_is_chapter_title_final)
                        print(' block_is_subtitle', block_is_subtitle)
                        print(' block_is_parrafo_completo', block_is_parrafo_completo) 
                        print(' block_is_quote', block_is_quote) 
                        print(' block_is_quote_author', block_is_quote_author)
                        print(' block_is_parrafo_parte_inicial', block_is_parrafo_parte_inicial)
                        print(' block_is_parrafo_parte_final', block_is_parrafo_parte_final)
                        print('\n\n Clean Text \n\n', clean_text)

                    if block_is_membrete:
                        continue

                    elif block_is_chapter_title_unique:
                        chapter_counter += 1

                        if chapter_counter > 1:

                            chapters_data.append({
                                "title": current_chapter_title,
                                "content": current_chapter_content,
                                "images": current_chapter_images,
                                "chapter_num": chapter_counter - 1
                            })

                            chapter_html_content = ""
                            chapter_title_html = ""

                        chapter_html_content += f"<h1>{block_html_text}</h1>\n\n"
                        chapter_title_html = clean_text

                    elif block_is_chapter_title_beggining:
                        # chapter_html_content += f"<h1>{block_html_text}"
                        chapter_html_content_prov += f"<h1>{block_html_text}"
                        chapter_title_html_prov += clean_text + " "

                    elif block_is_chapter_title_continuation:
                        # chapter_html_content += f"{block_html_text}"
                        chapter_html_content_prov += f"{block_html_text}"
                        chapter_title_html_prov += clean_text + " "

                    elif block_is_chapter_title_final:
                        chapter_counter += 1

                        if chapter_counter > 1:

                            chapters_data.append({
                                "title": current_chapter_title,
                                "content": current_chapter_content,
                                "images": current_chapter_images,
                                "chapter_num": chapter_counter - 1
                            })

                            chapter_html_content = ""
                            chapter_title_html = ""
                            # chapter_html_content_prov = ""
                            # chapter_title_html_prov = ""

                        chapter_html_content += chapter_html_content_prov + f"{block_html_text}</h1>\n\n"
                        chapter_title_html += chapter_title_html_prov + clean_text

                        chapter_html_content_prov = ""
                        chapter_title_html_prov = ""

                    elif block_is_subtitle:
                        chapter_html_content += f"<h2>{block_html_text}</h2>\n\n"

                    elif block_is_parrafo_completo:
                        chapter_html_content += f"<p>{block_html_text}</p>\n\n"

                    elif block_is_quote:
                        chapter_html_content += f"<blockquote>{block_html_text}</blockquote>\n\n"

                    elif block_is_quote_author:
                        chapter_html_content += f"<p style='text-align: right;'><em>{block_html_text}</em></p>\n\n"
                        
                    elif block_is_parrafo_parte_inicial:
                        chapter_html_content += f"<p>{block_html_text} "

                    elif block_is_parrafo_parte_final:
                        chapter_html_content += f"{block_html_text}</p>\n\n"

                    else:
                        # Block es parrafo normal
                        chapter_html_content += f"<p>{block_html_text}</p>\n\n"

                    current_chapter_content = chapter_html_content
                    current_chapter_title = chapter_title_html


        if page_num == end_idx:
            # Al final de la última página, guardar el capítulo
            chapters_data.append({
                "title": current_chapter_title,
                "content": current_chapter_content,
                "images": current_chapter_images,
                "chapter_num": chapter_counter
            })
 
    if debug:
        for chapter in chapters_data:
            print('title: ', chapter['title'])
            print(chapter['content'])
            print('chap num:', chapter['chapter_num'])

    return chapters_data, images_info

def convert_pdf_to_epub(archivio_pdf,
                        titulo,
                        autor,
                        pagina_incial,
                        pagina_final,
                        rango_size_inicial_titulo,
                        rango_size_final_titulo,
                        rango_size_inicial_subtitulo,
                        rango_size_final_subtitulo,
                        bbox_X_paragraph,
                        titulo_archivo_epub_resultante,
                        incluir_imagenes=True,
                        debug=False):

    chapters_data, images_info = convert_pdf_to_chapters(
                                                    archivio_pdf,
                                                    pagina_incial,
                                                    pagina_final,
                                                    rango_size_inicial_titulo,
                                                    rango_size_final_titulo,
                                                    rango_size_inicial_subtitulo,
                                                    rango_size_final_subtitulo,
                                                    bbox_X_paragraph,
                                                    debug,
                                                    incluir_imagenes
    )

    epub_path = convert_chapters_to_Epub(titulo, autor, chapters_data, images_info, titulo_archivo_epub_resultante)

    print(f"EPUB creado exitosamente: {epub_path}")
    print("Para convertirlo a formato MOBI para Kindle, puedes usar Calibre.")

if __name__ == "__main__":

    archivio_pdf                    = "pdfs\The Beginning of Infinity.pdf"
    titulo                          = "TBOI"
    autor                           = "David Deutsch"
    pagina_incial                   = 34
    pagina_final                    = 34
    rango_size_inicial_titulo       = 17
    rango_size_final_titulo         = 24
    rango_size_inicial_subtitulo    = 15
    rango_size_final_subtitulo      = 17
    bbox_X_paragraph                = 72.0
    titulo_archivo_epub_resultante  = "epubs/TheBeggining13-04.epub"
    incluir_imagenes                = True
    debug                           = False

    convert_pdf_to_epub(archivio_pdf,
                        titulo,
                        autor,
                        pagina_incial,
                        pagina_final,
                        rango_size_inicial_titulo,
                        rango_size_final_titulo,
                        rango_size_inicial_subtitulo,
                        rango_size_final_subtitulo,
                        bbox_X_paragraph,
                        titulo_archivo_epub_resultante,
                        incluir_imagenes,
                        debug)
    
    