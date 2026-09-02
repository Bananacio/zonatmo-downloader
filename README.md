# ZonaTMO Manga Downloader

Script para descargar mangas de ZonaTMO y compilarlos en PDF o CBZ por volumen.
El downloader obtiene los capítulos desde la estructura HTML del manga, accede
al visor, descarga sus páginas y genera los archivos finales.

## Características

- Navega la lista de capítulos de un manga.
- Accede al visor del capítulo.
- Extrae URLs de imágenes del manga.
- Descarga las páginas con reintentos.
- Genera salida en PDF o CBZ.
- Permite definir volúmenes, capítulos y formatos desde la línea de comandos.
- Permite listar capítulos sin descargar y activar logs de diagnóstico.
- Reconoce capítulos decimales, por ejemplo `76.5`.
- Permite controlar cuántas páginas se descargan simultáneamente.

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

En fish, usar:

```fish
source venv/bin/activate.fish
```

2. Instalar dependencias:

```bash
pip install requests playwright pillow img2pdf
```

3. Instalar el navegador de Playwright:

```bash
python -m playwright install chromium
```

## Ejemplos de uso

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

### Probar un solo capítulo

Es recomendable validar primero un capítulo antes de descargar un volumen
completo:

```bash
python manga_downloader.py \
  --url "https://zonatmo.org/library/manga/19942/uma-musume-cinderella-gray" \
  --name "Uma_Musume_Cinderella_Gray" \
  --format pdf \
  --chapter 76 \
  --headless \
  --debug
```

También se pueden seleccionar capítulos decimales o rangos:

```bash
python manga_downloader.py \
  --url "https://zonatmo.org/library/manga/19942/uma-musume-cinderella-gray" \
  --name "Uma_Musume_Cinderella_Gray" \
  --format cbz \
  --chapters 76 76.5 77-79 \
  --parallel-downloads 3 \
  --headless
```

`--parallel-downloads` controla la cantidad máxima de páginas descargándose al
mismo tiempo. El valor predeterminado es `1`, y conserva la descarga secuencial.
Para acelerar sin exigir demasiado al servidor, se recomienda empezar con `3` o
`4`:

```bash
python manga_downloader.py \
  --volumes 9:76-85 \
  --parallel-downloads 4 \
  --delay-captures 0.1 \
  --headless
```

### Listar capítulos

Para comprobar qué capítulos detecta el sitio sin iniciar una descarga:

```bash
python manga_downloader.py \
  --url "https://zonatmo.org/library/manga/19942/uma-musume-cinderella-gray" \
  --list-chapters \
  --headless \
  --debug
```

## Opciones disponibles

- `--url`: URL del manga.
- `--name`: nombre base para los archivos generados.
- `--format`: `pdf`, `cbz` o `both`.
- `--headless`: ejecuta el navegador sin interfaz.
- `--debug`: muestra logs detallados para diagnosticar navegación y descargas.
- `--list-chapters`: muestra los capítulos detectados y termina sin descargar.
- `--chapter`: capítulo exacto; se puede repetir, por ejemplo `--chapter 76 --chapter 77`.
- `--chapters`: capítulos individuales o rangos, por ejemplo `76 76.5 77-79`.
- `--volumes`: volúmenes personalizados en formato `N:inicio-fin`.
- `--delay-captures`: segundos entre descargas de páginas; por defecto `0.5`.
- `--delay-chapters`: segundos entre capítulos; por defecto `2`.
- `--max-retries`: intentos máximos por imagen; por defecto `3`.
- `--parallel-downloads`: páginas simultáneas; por defecto `1`.

Las descargas concurrentes tienen un límite para evitar saturar la conexión o
activar medidas anti-bot. Si aparecen errores `Connection reset by peer`, bajar
este valor a `2` o `3` y aumentar `--delay-captures`.

## Carpetas generadas

- `temp/`: imágenes temporales por capítulo.
- `output/`: PDFs y CBZ finales.

Los capítulos se guardan temporalmente con nombres como:

```text
temp/
├── cap_76/
├── cap_76_5/
└── cap_77/
```

Cada capítulo completo contiene un archivo `complete.txt`. Si el proceso se
interrumpe antes de terminar, se puede volver a ejecutar el mismo comando: los
capítulos marcados como completos se saltan y los demás se intentan descargar
nuevamente.

## Diagnóstico

Si un capítulo no se descarga, ejecutar primero `--list-chapters --debug` para
confirmar que la lista y los enlaces `view_uploads` se están detectando. Luego
probar un capítulo puntual con `--chapter` y `--debug`.

El sitio puede cambiar su estructura HTML, requerir tiempos de espera mayores o
aplicar medidas anti-bot. En ese caso se pueden aumentar los tiempos:

```bash
python manga_downloader.py \
  --chapter 76 \
  --delay-captures 1 \
  --delay-chapters 4 \
  --max-retries 5 \
  --debug
```

Los archivos temporales y los resultados generados están excluidos por
`.gitignore`; no deben subirse al repositorio.

## Nota importante

El sitio de origen puede cambiar su estructura HTML o aplicar medidas anti-bot. En esos casos, es posible que el scraper necesite pequeños ajustes en los selectores.

## Licencia

MIT
