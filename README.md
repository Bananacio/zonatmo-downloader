# ZonaTMO Manga Downloader

Script para descargar mangas de ZonaTMO y compilarlos en PDF o CBZ por volumen.

## Características

- Navega la lista de capítulos de un manga.
- Accede al visor del capítulo.
- Extrae URLs de imágenes del manga.
- Descarga las páginas con reintentos.
- Genera salida en PDF o CBZ.
- Permite definir volúmenes y formatos desde la línea de comandos.

## Requisitos

- Python 3.10+
- pip
- Navegador Chromium para Playwright

## Instalación

1. Crear el entorno virtual:

```bash
python -m venv venv
source venv/bin/activate
```

2. Instalar dependencias:

```bash
pip install requests playwright pillow img2pdf
```

3. Instalar el navegador de Playwright:

```bash
python -m playwright install chromium
```

## Uso

Ejemplo básico:

```bash
python manga_downloader.py \
  --url "https://zonatmo.org/library/manga/19942/uma-musume-cinderella-gray" \
  --name "Uma_Musume_Cinderella_Gray" \
  --format pdf \
  --headless
```

Ejemplo con volúmenes personalizados:

```bash
python manga_downloader.py \
  --url "https://zonatmo.org/library/manga/19942/uma-musume-cinderella-gray" \
  --name "Uma_Musume_Cinderella_Gray" \
  --format cbz \
  --volumes 1:1-7 2:8-16 3:17-25 \
  --headless
```

## Opciones disponibles

- `--url`: URL del manga.
- `--name`: nombre base para los archivos generados.
- `--format`: `pdf`, `cbz` o `both`.
- `--headless`: ejecuta el navegador sin interfaz.
- `--volumes`: volúmenes personalizados en formato `N:inicio-fin`.
- `--delay-captures`: tiempo entre descargas de páginas.
- `--delay-chapters`: tiempo entre capítulos.
- `--max-retries`: intentos máximos por imagen.

## Carpetas generadas

- `temp/`: imágenes temporales por capítulo.
- `output/`: PDFs y CBZ finales.

## Nota importante

El sitio de origen puede cambiar su estructura HTML o aplicar medidas anti-bot. En esos casos, es posible que el scraper necesite pequeños ajustes en los selectores.

## Licencia

MIT
