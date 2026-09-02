#!/usr/bin/env python3
"""
ZonaTMO Manga Downloader
========================
Script para descargar mangas de ZonaTMO y compilarlos en PDF/CBZ
por volúmenes automáticamente.

Autor: [Ignacio Cano]
Licencia: MIT
"""

import os
import re
import time
import shutil
import asyncio
import zipfile
import argparse
import math
from typing import Dict, List, Tuple, Optional
from pathlib import Path
import requests
from playwright.async_api import async_playwright, Page, Browser
import img2pdf
from PIL import Image
import json
import logging
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def chapter_number_to_dir_name(chapter_number: float) -> str:
    """Convierte un número de capítulo en un nombre de carpeta seguro."""
    cleaned = str(float(chapter_number)).replace('.', '_')
    return f"cap_{cleaned}"


# Dataclass para configuración
@dataclass
class MangaConfig:
    """Configuración para la descarga del manga"""
    manga_url: str
    manga_name: str
    volumes: Dict[int, Tuple[int, int]]  # {volumen: (cap_inicio, cap_fin)}
    output_format: str = "pdf"  # "pdf" o "cbz"
    headless: bool = True
    delay_between_captures: float = 1.0
    delay_between_chapters: float = 2.0
    max_retries: int = 3
    temp_dir: str = "temp"
    output_dir: str = "output"
    selected_chapters: Optional[List[float]] = None
    list_chapters: bool = False
    debug: bool = False
    parallel_downloads: int = 1

class ZonaTMODownloader:
    """Clase principal para descargar mangas de ZonaTMO"""
    
    def __init__(self, config: MangaConfig):
        self.config = config
        self.browser: Optional[Browser] = None
        self.context = None
        self.temp_dir = Path(config.temp_dir)
        self.output_dir = Path(config.output_dir)
        
        # Crear directorios si no existen
        self.temp_dir.mkdir(exist_ok=True, parents=True)
        self.output_dir.mkdir(exist_ok=True, parents=True)
        
        # Headers para las peticiones
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
            'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
            'Referer': 'https://zonatmo.org/',
            'Sec-Fetch-Dest': 'image',
            'Sec-Fetch-Mode': 'no-cors',
            'Sec-Fetch-Site': 'cross-site',
        }
    
    async def initialize_browser(self):
        """Inicializa el navegador Playwright"""
        playwright = await async_playwright().start()
        self.browser = await playwright.chromium.launch(
            headless=self.config.headless,
            args=['--disable-blink-features=AutomationControlled']
        )
        self.context = await self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent=self.headers['User-Agent']
        )
        # Eliminar la detección de WebDriver
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        logger.info("Navegador inicializado correctamente")
    
    async def close_browser(self):
        """Cierra el navegador y limpia recursos"""
        if self.context:
            await self.context.close()
            self.context = None
        if self.browser:
            await self.browser.close()
            self.browser = None
            logger.info("Navegador cerrado")
    
    async def get_chapter_list(self) -> List[Dict]:
        """Obtiene la lista de capítulos del manga"""
        logger.info(f"Obteniendo lista de capítulos de: {self.config.manga_url}")

        page = await self.context.new_page()
        await page.goto(self.config.manga_url, wait_until='domcontentloaded', timeout=60000)

        try:
            await page.wait_for_load_state('networkidle', timeout=20000)
        except Exception:
            logger.warning("La carga de capítulos no terminó en networkidle, continuando con la página disponible")

        await page.evaluate(r"""
            () => {
                const toggle = Array.from(document.querySelectorAll('#chapters-hidden, .collapse, .chapter-toggle, .show-more, .btn'))
                    .find((el) => {
                        const text = (el.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase();
                        return /ver todo|mostrar más|mostrar todos|ver más|expand|toggle|more/.test(text);
                    });

                if (toggle) {
                    try {
                        toggle.click();
                    } catch (err) {
                        // no-op
                    }
                }
            }
        """)
        await page.wait_for_timeout(1500)

        chapters = await page.evaluate(r"""
            () => {
                const items = [...document.querySelectorAll('li.upload-link[data-chapter-number]')];
                const seen = new Set();
                const chapters = [];

                for (const item of items) {
                    const rawNumber = item.getAttribute('data-chapter-number');
                    if (!rawNumber) continue;

                    const chapterNumber = Number.parseFloat(rawNumber);
                    if (!Number.isFinite(chapterNumber) || chapterNumber <= 0) continue;

                    const titleEl = item.querySelector('.chapter-number');
                    const title = titleEl ? titleEl.textContent.replace(/\s+/g, ' ').trim() : `Capítulo ${chapterNumber}`;

                    const readLink = item.querySelector('a[href*="/view_uploads/"]');
                    const href = readLink ? readLink.href : '';
                    if (!href) continue;

                    const key = `${chapterNumber}:${href}`;
                    if (seen.has(key)) continue;
                    seen.add(key);

                    chapters.push({
                        number: chapterNumber,
                        title,
                        url: href,
                        index: chapters.length
                    });
                }

                return chapters.sort((a, b) => a.number - b.number);
            }
        """)

        await page.close()
        logger.info(f"Se encontraron {len(chapters)} capítulos")
        return chapters
    
    async def navigate_to_viewer(self, page: Page, chapter_url: str) -> bool:
        """Navega hasta el visor de imágenes del capítulo"""
        try:
            await page.goto(chapter_url, wait_until='domcontentloaded', timeout=60000)
            await page.wait_for_timeout(1500)

            candidate_urls = []
            if '/view_uploads/' in chapter_url:
                candidate_urls = [chapter_url]
            else:
                candidate_urls = await page.evaluate(r"""
                    () => {
                        const anchors = [...document.querySelectorAll('a[href]')];
                        const scored = [];

                        anchors.forEach((anchor) => {
                            const href = anchor.href || '';
                            const text = (anchor.textContent || '').trim().toLowerCase();
                            if (!href || href.includes('javascript:')) return;

                            let score = 0;
                            if (/\/viewer\//i.test(href)) score += 5;
                            if (/\/read\//i.test(href)) score += 5;
                            if (/\/chapter\//i.test(href)) score += 4;
                            if (/\/view_uploads\//i.test(href)) score += 6;
                            if (/leer|read|ver|capitulo|chapter/i.test(text)) score += 2;
                            if (/cover|poster|banner|logo|avatar|icon|download|login|signup/i.test(href + ' ' + text)) score -= 10;

                            if (score > 0) {
                                scored.push({ href, score });
                            }
                        });

                        return scored
                            .sort((a, b) => b.score - a.score)
                            .slice(0, 10)
                            .map(item => item.href);
                    }
                """)

            if not candidate_urls:
                return False

            for viewer_url in candidate_urls:
                if not viewer_url.startswith('http'):
                    base_url = chapter_url.split('/library/')[0]
                    viewer_url = base_url + viewer_url

                try:
                    await page.goto(viewer_url, wait_until='domcontentloaded', timeout=60000)
                    await page.wait_for_timeout(2000)

                    has_images = await page.evaluate(r"""
                        () => {
                            const imgCount = document.querySelectorAll('img').length;
                            return imgCount > 0 ||
                                   document.querySelector('.viewer-container') !== null ||
                                   document.querySelector('#viewer') !== null ||
                                   document.querySelector('.chapter-content') !== null ||
                                   document.querySelector('img[src*="data"]') !== null;
                        }
                    """)

                    if has_images:
                        return True
                except Exception:
                    continue

            return False

        except Exception as e:
            logger.error(f"Error navegando al visor: {e}")
            return False
    
    async def extract_image_urls(self, page: Page) -> List[str]:
        """Extrae las URLs de las imágenes del capítulo"""
        try:
            selectors = [
                '.viewer-container img',
                '#viewer img',
                '.chapter-content img',
                '.reading-content img',
                'img[data-src]',
                'img[src*="http"]',
                'img'
            ]

            image_urls = set()
            matched_selector = None
            for selector in selectors:
                try:
                    await page.wait_for_selector(selector, timeout=5000)
                    urls = await page.evaluate("""
                        (selector) => {
                            const images = document.querySelectorAll(selector);
                            const urls = new Set();

                            images.forEach(img => {
                                const src = img.dataset.src ||
                                            img.dataset.original ||
                                            img.dataset.lazy ||
                                            img.src;

                                if (!src || !src.startsWith('http')) return;
                                if (/avatar|icon|logo|banner|thumbnail/i.test(src)) return;
                                urls.add(src);
                            });

                            return Array.from(urls);
                        }
                    """, selector)

                    if urls:
                        image_urls.update(urls)
                        matched_selector = selector
                        break
                except Exception:
                    continue

            if not image_urls:
                logger.warning("No se encontraron imágenes con selectores estándar, intentando con scroll...")
                matched_selector = 'img (después de scroll)'
                await self.scroll_page(page)
                image_urls = await page.evaluate(r"""
                    () => {
                        const images = document.querySelectorAll('img');
                        const urls = new Set();

                        images.forEach(img => {
                            const src = img.dataset.src || img.dataset.original || img.dataset.lazy || img.src;
                            if (!src || !src.startsWith('http')) return;
                            if (/avatar|icon|logo|banner|thumbnail/i.test(src)) return;
                            if (/\.((jpg|jpeg|png|webp|avif))(\?.*)?$/i.test(src) || /img|page|chapter|manga/i.test(src)) {
                                urls.add(src);
                            }
                        });

                        return Array.from(urls);
                    }
                """)

            filtered_urls = []
            for url in image_urls:
                normalized = url.split('?')[0]
                if re.search(r'\.(jpg|jpeg|png|webp|avif)', normalized, re.IGNORECASE):
                    filtered_urls.append(url)

            filtered_urls = list(dict.fromkeys(filtered_urls))
            filtered_urls.sort(key=lambda x: self.extract_page_number(x))

            if matched_selector:
                logger.debug(f"Selector de imágenes utilizado: {matched_selector}")
            else:
                logger.debug("No se identificó un selector específico para las imágenes")
            if filtered_urls:
                logger.debug(f"Primera URL de imagen: {filtered_urls[0]}")
                logger.debug(f"Última URL de imagen: {filtered_urls[-1]}")
            logger.info(f"Se encontraron {len(filtered_urls)} imágenes")
            return filtered_urls

        except Exception as e:
            logger.error(f"Error extrayendo URLs de imágenes: {e}")
            return []
    
    def extract_page_number(self, url: str) -> int:
        """Extrae el número de página de la URL de la imagen"""
        # Intentar extraer número de página de diferentes formatos de URL
        patterns = [
            r'page[_-](\d+)',
            r'/(\d+)\.(?:jpg|png|webp)',
            r'image[_-](\d+)',
            r'(\d{3,})'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                return int(match.group(1))
        
        return 0
    
    async def scroll_page(self, page: Page):
        """Hace scroll por la página para cargar todas las imágenes lazy"""
        await page.evaluate("""
            async () => {
                await new Promise((resolve) => {
                    let totalHeight = 0;
                    const distance = 100;
                    const timer = setInterval(() => {
                        const scrollHeight = document.body.scrollHeight;
                        window.scrollBy(0, distance);
                        totalHeight += distance;
                        
                        if(totalHeight >= scrollHeight){
                            clearInterval(timer);
                            resolve();
                        }
                    }, 100);
                });
            }
        """)
    
    def _download_image_once(self, url: str, save_path: Path):
        """Realiza una descarga síncrona; se ejecuta fuera del event loop."""
        try:
            with requests.get(
                url,
                headers=self.headers,
                timeout=30,
                stream=True,
                allow_redirects=True
            ) as response:
                content_type = response.headers.get('Content-Type', '').lower()
                if response.status_code == 200 and 'image' in content_type:
                    with open(save_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                    return True, response.status_code, content_type

                return False, response.status_code, content_type
        except Exception as e:
            return False, None, str(e)

    async def download_image(self, url: str, save_path: Path, retry_count: int = 0) -> bool:
        """Descarga una imagen con reintentos sin bloquear otras descargas."""
        success, status_code, detail = await asyncio.to_thread(
            self._download_image_once,
            url,
            save_path
        )

        if success:
            return True

        if retry_count < self.config.max_retries:
            logger.warning(f"Reintentando descarga ({retry_count + 1}/{self.config.max_retries}): {url} (status={status_code}, detalle={detail})")
            await asyncio.sleep(1)
            return await self.download_image(url, save_path, retry_count + 1)

        if status_code is not None:
            logger.error(f"Error descargando imagen: HTTP {status_code} - {url}")
        else:
            logger.error(f"Error descargando imagen {url}: {detail}")
        return False

    async def download_image_with_limit(self, semaphore: asyncio.Semaphore, url: str, save_path: Path, page_number: int, total_pages: int) -> bool:
        """Descarga una imagen respetando el máximo de tareas simultáneas."""
        async with semaphore:
            success = await self.download_image(url, save_path)
            if success:
                logger.debug(f"Descargada imagen {page_number}/{total_pages}")
            return success
    
    async def download_chapter(self, chapter: Dict, chapter_dir: Path) -> bool:
        """Descarga un capítulo completo"""
        logger.info(f"Descargando capítulo {chapter['number']}: {chapter['title']}")
        
        # Crear directorio del capítulo
        chapter_dir.mkdir(exist_ok=True, parents=True)
        
        # Verificar si el capítulo ya fue descargado
        if (chapter_dir / "complete.txt").exists():
            logger.info(f"Capítulo {chapter['number']} ya descargado, saltando...")
            return True
        
        page = await self.context.new_page()
        
        try:
            # Navegar al visor
            if not await self.navigate_to_viewer(page, chapter['url']):
                logger.error(f"No se pudo acceder al visor del capítulo {chapter['number']}")
                await page.close()
                return False
            
            # Extraer URLs de imágenes
            image_urls = await self.extract_image_urls(page)
            
            if not image_urls:
                logger.error(f"No se encontraron imágenes en el capítulo {chapter['number']}")
                await page.close()
                return False
            
            # Descargar imágenes
            download_tasks = []
            semaphore = asyncio.Semaphore(self.config.parallel_downloads)
            for i, url in enumerate(image_urls, 1):
                file_extension = Path(url.split('?')[0]).suffix or '.jpg'
                image_path = chapter_dir / f"pag_{i:03d}{file_extension}"
                download_tasks.append(asyncio.create_task(
                    self.download_image_with_limit(
                        semaphore, url, image_path, i, len(image_urls)
                    )
                ))

            results = await asyncio.gather(*download_tasks)
            successful_downloads = sum(results)

            if self.config.delay_between_captures > 0:
                await asyncio.sleep(self.config.delay_between_captures)
            
            if successful_downloads > 0:
                # Marcar capítulo como completo
                (chapter_dir / "complete.txt").write_text(
                    f"Capítulo {chapter['number']} descargado correctamente\n"
                    f"Imágenes: {successful_downloads}/{len(image_urls)}\n"
                    f"Fecha: {time.strftime('%Y-%m-%d %H:%M:%S')}"
                )
                logger.info(f"Capítulo {chapter['number']} descargado ({successful_downloads} imágenes)")
                return True
            else:
                logger.error(f"No se pudo descargar ninguna imagen del capítulo {chapter['number']}")
                return False
                
        except Exception as e:
            logger.error(f"Error descargando capítulo {chapter['number']}: {e}")
            return False
        finally:
            await page.close()
    
    def compile_pdf(self, chapters: List[Dict], volume_number: int):
        """Compila los capítulos en un archivo PDF"""
        output_file = self.output_dir / f"{self.config.manga_name}_Vol_{volume_number:02d}.pdf"
        logger.info(f"Generando PDF del volumen {volume_number}...")

        all_images = []
        for chapter in chapters:
            chapter_dir = self.temp_dir / chapter_number_to_dir_name(float(chapter['number']))
            if chapter_dir.exists():
                images = sorted(chapter_dir.glob("pag_*"), key=lambda p: p.name)
                all_images.extend(images)

        if all_images:
            try:
                with open(output_file, "wb") as f:
                    f.write(img2pdf.convert([str(img) for img in all_images]))
                logger.info(f"PDF generado: {output_file}")
                return True
            except Exception as e:
                logger.error(f"Error generando PDF: {e}")
                return False
        else:
            logger.error(f"No se encontraron imágenes para el volumen {volume_number}")
            return False
    
    def compile_cbz(self, chapters: List[Dict], volume_number: int):
        """Compila los capítulos en un archivo CBZ"""
        output_file = self.output_dir / f"{self.config.manga_name}_Vol_{volume_number:02d}.cbz"
        logger.info(f"Generando CBZ del volumen {volume_number}...")

        all_images = []
        for chapter in chapters:
            chapter_dir = self.temp_dir / chapter_number_to_dir_name(float(chapter['number']))
            if chapter_dir.exists():
                images = sorted(chapter_dir.glob("pag_*"), key=lambda p: p.name)
                all_images.extend(images)

        if all_images:
            try:
                with zipfile.ZipFile(output_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for i, image_path in enumerate(all_images, 1):
                        arcname = f"page_{i:04d}{image_path.suffix}"
                        zipf.write(image_path, arcname)

                logger.info(f"CBZ generado: {output_file}")
                return True
            except Exception as e:
                logger.error(f"Error generando CBZ: {e}")
                return False
        else:
            logger.error(f"No se encontraron imágenes para el volumen {volume_number}")
            return False
    
    def clean_temp(self, chapters: List[Dict]):
        """Limpia los archivos temporales"""
        for chapter in chapters:
            chapter_dir = self.temp_dir / chapter_number_to_dir_name(float(chapter['number']))
            if chapter_dir.exists():
                shutil.rmtree(chapter_dir)
                logger.debug(f"Eliminado directorio temporal: {chapter_dir}")
    
    async def download_manga(self):
        """Método principal para descargar el manga completo"""
        try:
            # Inicializar navegador
            await self.initialize_browser()
            
            # Obtener lista de capítulos
            chapters = await self.get_chapter_list()

            if self.config.list_chapters:
                logger.info("Capítulos disponibles:")
                for chapter in chapters:
                    logger.info(f" - {chapter['number']}: {chapter['title']}")
                return

            if self.config.selected_chapters:
                selected_numbers = {float(chapter_number) for chapter_number in self.config.selected_chapters}
                chapters = [chapter for chapter in chapters if float(chapter['number']) in selected_numbers]
                logger.info(f"Capítulos filtrados por selección: {[chapter['number'] for chapter in chapters]}")

                if not chapters:
                    logger.error(f"No se encontraron capítulos para la selección: {sorted(selected_numbers)}")
                    return
            
            if not chapters:
                logger.error("No se encontraron capítulos")
                return
            
            # Procesar cada volumen
            for volume_number, (start_chapter, end_chapter) in self.config.volumes.items():
                logger.info(f"\n{'='*50}")
                logger.info(f"Procesando Volumen {volume_number} (Capítulos {start_chapter}-{end_chapter})")
                logger.info(f"{'='*50}\n")
                
                # Filtrar capítulos del volumen
                volume_chapters = [
                    ch for ch in chapters 
                    if start_chapter <= ch['number'] <= end_chapter
                ]
                
                if not volume_chapters:
                    logger.warning(f"No se encontraron capítulos para el volumen {volume_number}")
                    continue
                
                # Descargar capítulos del volumen
                for chapter in volume_chapters:
                    chapter_dir = self.temp_dir / chapter_number_to_dir_name(float(chapter['number']))

                    success = await self.download_chapter(chapter, chapter_dir)
                    if not success:
                        logger.warning(f"Fallo la descarga del capítulo {chapter['number']}, continuando...")
                    
                    # Delay entre capítulos
                    await asyncio.sleep(self.config.delay_between_chapters)
                
                # Compilar volumen
                if self.config.output_format.lower() == "pdf":
                    self.compile_pdf(volume_chapters, volume_number)
                elif self.config.output_format.lower() == "cbz":
                    self.compile_cbz(volume_chapters, volume_number)
                else:
                    # Generar ambos formatos
                    self.compile_pdf(volume_chapters, volume_number)
                    self.compile_cbz(volume_chapters, volume_number)
                
                # Limpiar temporales
                self.clean_temp(volume_chapters)
                
                logger.info(f"Volumen {volume_number} completado\n")
            
            logger.info("\n¡Descarga completada!")
            
        except Exception as e:
            logger.error(f"Error durante la descarga: {e}")
        finally:
            await self.close_browser()
    
    async def download_single_chapter(self, chapter_number: int):
        """Descarga un capítulo individual (para pruebas)"""
        try:
            await self.initialize_browser()
            
            chapters = await self.get_chapter_list()
            
            target_chapter = next(
                (ch for ch in chapters if ch['number'] == chapter_number),
                None
            )
            
            if not target_chapter:
                logger.error(f"No se encontró el capítulo {chapter_number}")
                return
            
            chapter_dir = self.temp_dir / chapter_number_to_dir_name(float(chapter_number))
            await self.download_chapter(target_chapter, chapter_dir)
            
            logger.info(f"Capítulo {chapter_number} descargado en: {chapter_dir}")
            
        except Exception as e:
            logger.error(f"Error: {e}")
        finally:
            await self.close_browser()


def parse_args():
    """Parsea argumentos de línea de comandos"""
    parser = argparse.ArgumentParser(description="Descarga mangas de ZonaTMO y compúlalos en PDF/CBZ.")
    parser.add_argument("--url", default="https://zonatmo.org/library/manga/19942/uma-musume-cinderella-gray", help="URL del manga")
    parser.add_argument("--name", default="Uma_Musume_Cinderella_Gray", help="Nombre base para el archivo de salida")
    parser.add_argument("--format", choices=["pdf", "cbz", "both"], default="pdf", help="Formato de salida")
    parser.add_argument("--headless", action="store_true", default=False, help="Ejecutar navegadores sin interfaz visual")
    parser.add_argument("--debug", action="store_true", default=False, help="Activa logs detallados de diagnóstico")
    parser.add_argument("--list-chapters", action="store_true", default=False, help="Muestra la lista de capítulos y sale sin descargar")
    parser.add_argument("--chapter", type=float, action="append", default=[], help="Capítulo exacto a descargar. Se puede repetir el argumento")
    parser.add_argument("--chapters", nargs="*", default=[], help="Capítulos o rangos a descargar. Ejemplos: 1 3-5 7.5")
    parser.add_argument("--volumes", nargs="*", default=[], help="Volúmenes en formato 1:1-7 2:8-16")
    parser.add_argument("--delay-captures", type=float, default=0.5)
    parser.add_argument("--delay-chapters", type=float, default=2.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--parallel-downloads", type=int, default=1, help="Cantidad máxima de imágenes descargadas simultáneamente")
    return parser.parse_args()


def build_volume_map(raw_volumes: List[str]):
    """Construye el diccionario de volúmenes a partir de argumentos tipo '1:1-7'."""
    if not raw_volumes:
        return {1: (1, 7), 2: (8, 16), 3: (17, 25)}

    parsed = {}
    for item in raw_volumes:
        if ':' not in item:
            continue
        volume, range_value = item.split(':', 1)
        start, end = range_value.split('-', 1)
        parsed[int(volume)] = (int(start), int(end))
    return parsed


def parse_chapter_targets(raw_targets: List[str]) -> List[float]:
    """Parsea valores tipo '1', '3-5', '7.5' y devuelve una lista ordenada."""
    if not raw_targets:
        return []

    values: List[float] = []

    for item in raw_targets:
        if not item:
            continue

        for chunk in str(item).split(','):
            chunk = chunk.strip()
            if not chunk:
                continue

            if '-' in chunk and not chunk.startswith('-'):
                try:
                    start_text, end_text = chunk.split('-', 1)
                    start_value = float(start_text.strip())
                    end_value = float(end_text.strip())
                except ValueError:
                    continue

                start_number = min(start_value, end_value)
                end_number = max(start_value, end_value)

                if start_number.is_integer() and end_number.is_integer():
                    numbers = range(int(math.ceil(start_number)), int(math.floor(end_number)) + 1)
                    values.extend(float(number) for number in numbers)
                else:
                    values.append(start_value)
                    values.append(end_value)
                continue

            try:
                values.append(float(chunk))
            except ValueError:
                continue

    seen = set()
    ordered = []
    for value in sorted(values):
        rounded = round(float(value), 4)
        if rounded not in seen:
            seen.add(rounded)
            ordered.append(rounded)

    return ordered


def main():
    """Función principal"""
    args = parse_args()
    if args.debug:
        logger.setLevel(logging.DEBUG)

    selected_chapters = parse_chapter_targets(args.chapters)
    if args.chapter:
        selected_chapters.extend(float(value) for value in args.chapter)

    if selected_chapters:
        selected_chapters = sorted(set(round(float(value), 4) for value in selected_chapters))

    volumes = build_volume_map(args.volumes)
    if selected_chapters:
        volumes = {1: (0, 999999)}

    config = MangaConfig(
        manga_url=args.url,
        manga_name=args.name,
        volumes=volumes,
        output_format=args.format,
        headless=args.headless,
        delay_between_captures=args.delay_captures,
        delay_between_chapters=args.delay_chapters,
        max_retries=args.max_retries,
        parallel_downloads=max(1, args.parallel_downloads),
        selected_chapters=selected_chapters or None,
        list_chapters=args.list_chapters,
        debug=args.debug
    )

    downloader = ZonaTMODownloader(config)
    asyncio.run(downloader.download_manga())


if __name__ == "__main__":
    main()