#!/usr/bin/env python3
"""
ZonaTMO Manga Downloader
========================
Script para descargar mangas de ZonaTMO y compilarlos en PDF/CBZ
por volúmenes automáticamente.

Autor: [Tu Nombre]
Licencia: MIT
"""

import os
import re
import time
import shutil
import asyncio
import zipfile
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
        if self.browser:
            await self.browser.close()
            logger.info("Navegador cerrado")
    
    async def get_chapter_list(self) -> List[Dict]:
        """Obtiene la lista de capítulos del manga"""
        logger.info(f"Obteniendo lista de capítulos de: {self.config.manga_url}")
        
        page = await self.context.new_page()
        await page.goto(self.config.manga_url, wait_until='networkidle')
        
        # Esperar a que carguen los capítulos
        await page.wait_for_selector('.list-group-item', timeout=10000)
        
        # Extraer información de los capítulos
        chapters = await page.evaluate("""
            () => {
                const chapterElements = document.querySelectorAll('.list-group-item');
                const chapters = [];
                
                chapterElements.forEach((element, index) => {
                    const link = element.querySelector('a');
                    const titleElement = element.querySelector('.chapter-title, .title');
                    
                    if (link && titleElement) {
                        const title = titleElement.textContent.trim();
                        const url = link.href;
                        
                        // Extraer número de capítulo
                        const chapterMatch = title.match(/Capítulo\s+(\d+)/i) || 
                                           title.match(/Chapter\s+(\d+)/i) ||
                                           title.match(/#(\d+)/);
                        
                        const chapterNumber = chapterMatch ? parseInt(chapterMatch[1]) : index + 1;
                        
                        chapters.push({
                            number: chapterNumber,
                            title: title,
                            url: url,
                            index: index
                        });
                    }
                });
                
                // Ordenar por número de capítulo
                return chapters.sort((a, b) => a.number - b.number);
            }
        """)
        
        await page.close()
        logger.info(f"Se encontraron {len(chapters)} capítulos")
        return chapters
    
    async def navigate_to_viewer(self, page: Page, chapter_url: str) -> bool:
        """Navega hasta el visor de imágenes del capítulo"""
        try:
            # Navegar a la página del capítulo
            await page.goto(chapter_url, wait_until='domcontentloaded')
            
            # Esperar y hacer clic en el botón de ver/leer
            read_button = await page.wait_for_selector(
                'a.btn-primary, .btn-ver, .btn-read, [href*="/viewer/"]',
                timeout=10000
            )
            
            if read_button:
                viewer_url = await read_button.get_attribute('href')
                if viewer_url:
                    if not viewer_url.startswith('http'):
                        base_url = chapter_url.split('/library/')[0]
                        viewer_url = base_url + viewer_url
                    
                    await page.goto(viewer_url, wait_until='networkidle')
                    
                    # Esperar a que el visor cargue completamente
                    await page.wait_for_timeout(2000)
                    
                    # Verificar si hay contenido del visor
                    has_viewer = await page.evaluate("""
                        () => {
                            return document.querySelector('.viewer-container') !== null ||
                                   document.querySelector('#viewer') !== null ||
                                   document.querySelector('.chapter-content') !== null ||
                                   document.querySelector('img[src*="data"]') !== null;
                        }
                    """)
                    
                    if has_viewer:
                        return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error navegando al visor: {e}")
            return False
    
    async def extract_image_urls(self, page: Page) -> List[str]:
        """Extrae las URLs de las imágenes del capítulo"""
        try:
            # Intentar diferentes selectores para encontrar imágenes
            selectors = [
                '.viewer-container img',
                '#viewer img',
                '.chapter-content img',
                '.reading-content img',
                'img[data-src]',
                'img[src*="http"]'
            ]
            
            image_urls = set()
            
            for selector in selectors:
                try:
                    await page.wait_for_selector(selector, timeout=5000)
                    
                    # Extraer URLs de imágenes
                    urls = await page.evaluate("""
                        (selector) => {
                            const images = document.querySelectorAll(selector);
                            const urls = [];
                            
                            images.forEach(img => {
                                // Verificar data-src primero (lazy loading)
                                const src = img.dataset.src || 
                                          img.dataset.original ||
                                          img.src;
                                
                                if (src && src.startsWith('http')) {
                                    urls.push(src);
                                }
                            });
                            
                            return urls;
                        }
                    """, selector)
                    
                    if urls:
                        image_urls.update(urls)
                        break
                        
                except Exception:
                    continue
            
            # Si no encontramos imágenes con los selectores, intentar con scroll
            if not image_urls:
                logger.warning("No se encontraron imágenes con selectores estándar, intentando con scroll...")
                await self.scroll_page(page)
                
                # Reintentar extracción
                image_urls = await page.evaluate("""
                    () => {
                        const images = document.querySelectorAll('img');
                        const urls = new Set();
                        
                        images.forEach(img => {
                            const src = img.dataset.src || 
                                      img.dataset.original ||
                                      img.src;
                            
                            if (src && src.startsWith('http') && 
                                !src.includes('avatar') && 
                                !src.includes('icon')) {
                                urls.add(src);
                            }
                        });
                        
                        return Array.from(urls);
                    }
                """)
            
            # Filtrar URLs que no son imágenes del manga
            filtered_urls = []
            for url in image_urls:
                if any(domain in url for domain in ['zona', 'tmo', 'manga', 'chapter', 'page']):
                    filtered_urls.append(url)
            
            # Ordenar las URLs si es posible
            filtered_urls.sort(key=lambda x: self.extract_page_number(x))
            
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
            r'\d{3,}'
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
    
    async def download_image(self, url: str, save_path: Path, retry_count: int = 0) -> bool:
        """Descarga una imagen con reintentos"""
        try:
            response = requests.get(
                url,
                headers=self.headers,
                timeout=30,
                stream=True
            )
            
            if response.status_code == 200:
                with open(save_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                return True
            elif retry_count < self.config.max_retries:
                logger.warning(f"Reintentando descarga ({retry_count + 1}/{self.config.max_retries}): {url}")
                await asyncio.sleep(1)
                return await self.download_image(url, save_path, retry_count + 1)
            else:
                logger.error(f"Error descargando imagen: HTTP {response.status_code} - {url}")
                return False
                
        except Exception as e:
            if retry_count < self.config.max_retries:
                logger.warning(f"Error descargando imagen, reintentando: {e}")
                await asyncio.sleep(1)
                return await self.download_image(url, save_path, retry_count + 1)
            else:
                logger.error(f"Error descargando imagen {url}: {e}")
                return False
    
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
            successful_downloads = 0
            for i, url in enumerate(image_urls, 1):
                file_extension = Path(url.split('?')[0]).suffix or '.jpg'
                image_path = chapter_dir / f"pag_{i:03d}{file_extension}"
                
                if await self.download_image(url, image_path):
                    successful_downloads += 1
                    logger.debug(f"Descargada imagen {i}/{len(image_urls)}")
                
                # Pequeño delay para evitar sobrecargar el servidor
                if i < len(image_urls):
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
            chapter_dir = self.temp_dir / f"cap_{chapter['number']:03d}"
            if chapter_dir.exists():
                # Obtener todas las imágenes del capítulo en orden
                images = sorted(chapter_dir.glob("pag_*"))
                all_images.extend(images)
        
        if all_images:
            try:
                # Convertir imágenes a PDF
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
            chapter_dir = self.temp_dir / f"cap_{chapter['number']:03d}"
            if chapter_dir.exists():
                # Obtener todas las imágenes del capítulo en orden
                images = sorted(chapter_dir.glob("pag_*"))
                all_images.extend(images)
        
        if all_images:
            try:
                # Crear archivo ZIP
                with zipfile.ZipFile(output_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for i, image_path in enumerate(all_images, 1):
                        # Renombrar archivo para orden correcto en CBZ
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
            chapter_dir = self.temp_dir / f"cap_{chapter['number']:03d}"
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
                    chapter_dir = self.temp_dir / f"cap_{chapter['number']:03d}"
                    
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
            
            chapter_dir = self.temp_dir / f"cap_{chapter_number:03d}"
            await self.download_chapter(target_chapter, chapter_dir)
            
            logger.info(f"Capítulo {chapter_number} descargado en: {chapter_dir}")
            
        except Exception as e:
            logger.error(f"Error: {e}")
        finally:
            await self.close_browser()


def main():
    """Función principal"""
    # Configuración del manga
    config = MangaConfig(
        manga_url="https://zonatmo.org/library/manga/19942/uma-musume-cinderella-gray",
        manga_name="Uma_Musume_Cinderella_Gray",
        volumes={
            1: (1, 7),
            2: (8, 16),
            3: (17, 25),
            # Agregar más volúmenes según sea necesario
        },
        output_format="pdf",  # Cambiar a "cbz" o "both" según preferencia
        headless=False,  # Cambiar a True para ejecutar sin interfaz gráfica
        delay_between_captures=0.5,
        delay_between_chapters=2.0,
        max_retries=3
    )
    
    # Crear downloader y ejecutar
    downloader = ZonaTMODownloader(config)
    
    # Ejecutar la descarga
    asyncio.run(downloader.download_manga())


if __name__ == "__main__":
    main()