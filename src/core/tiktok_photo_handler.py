import os
import json
import logging
import subprocess
import tempfile
import requests
import re
import time
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger(__name__)


class TikTokPhotoHandler:
    def __init__(self, temp_dir: str):
        self.temp_dir = temp_dir
        self.gallery_dl_available = self._check_gallery_dl()

    def _check_gallery_dl(self) -> bool:
        try:
            result = subprocess.run(['gallery-dl', '--version'],
                                   capture_output=True, text=True, timeout=5)
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            logger.warning("gallery-dl не установлен")
            return False

    def is_tiktok_photo_url(self, url: str) -> bool:
        return "tiktok.com" in url and "/photo/" in url

    def download_with_gallery_dl(self, url: str, output_dir: Optional[str] = None) -> List[str]:
        if not self.gallery_dl_available:
            raise RuntimeError("gallery-dl не установлен")

        output_dir = output_dir or self.temp_dir
        os.makedirs(output_dir, exist_ok=True)

        try:
            logger.info(f"Используем gallery-dl для: {url}")


            result = subprocess.run(
                ['gallery-dl', '-o', 'extractor.tiktok.archive=null', url],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=output_dir
            )

            logger.info(f"gallery-dl stdout: {result.stdout}")


            downloaded_files = self._find_downloaded_files(output_dir)

            if downloaded_files:
                logger.info(f"gallery-dl нашел {len(downloaded_files)} файлов")
                return downloaded_files

            return []

        except subprocess.TimeoutExpired:
            raise RuntimeError("Превышено время ожидания (>60 сек)")
        except Exception as e:
            logger.error(f"gallery-dl ошибка: {e}")
            return []

    def download_with_requests(self, url: str, output_dir: Optional[str] = None) -> List[str]:
        output_dir = output_dir or self.temp_dir
        os.makedirs(output_dir, exist_ok=True)

        try:

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }

            logger.info(f"Загружаю страницу: {url}")
            response = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
            response.raise_for_status()

            html = response.text


            image_urls = self._extract_image_urls(html)

            if not image_urls:
                logger.error("Изображения не найдены")
                return []

            logger.info(f"Найдено {len(image_urls)} изображений")


            downloaded_files = []

            for idx, img_url in enumerate(image_urls, 1):
                try:

                    img_url = img_url.split('?')[0]

                    if not img_url.startswith('http'):
                        continue

                    logger.info(f"Скачиваю {idx}/{len(image_urls)}")

                    img_response = requests.get(
                        img_url,
                        headers={**headers, "Referer": url},
                        timeout=30,
                        stream=True,
                        allow_redirects=True
                    )
                    img_response.raise_for_status()


                    content_type = img_response.headers.get('content-type', '').lower()
                    ext = self._get_extension(content_type, img_url)


                    file_name = f"tiktok_photo_{idx}.{ext}"
                    file_path = os.path.join(output_dir, file_name)

                    with open(file_path, 'wb') as f:
                        for chunk in img_response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)

                    file_size = os.path.getsize(file_path)

                    if file_size > 1000:
                        downloaded_files.append(file_path)
                        logger.info(f"✓ Скачано: {file_name}")
                    else:
                        os.remove(file_path)
                        logger.warning(f"✗ Файл слишком маленький: {file_name}")

                except Exception as e:
                    logger.warning(f"Ошибка при скачивании {idx}: {e}")
                    continue

            return downloaded_files

        except Exception as e:
            logger.error(f"Ошибка requests: {e}")
            return []

    def _extract_image_urls(self, html: str) -> List[str]:
        """Извлекает URL изображений из HTML"""
        urls = set()


        patterns = [
            r'https://p\d+-[a-z0-9\-]+\.tiktok\.com[^"\s<>]*\.(?:jpg|jpeg|png|webp)',
            r'https://v(?:16|17|18|19|20)-[a-z0-9\-]+\.tiktok\.com[^"\s<>]*\.(?:jpg|jpeg|png|webp)',
            r'"url":"(https://[^"]*\.(?:jpg|jpeg|png|webp)[^"]*)"',
            r'\'url\':\'(https://[^\']*\.(?:jpg|jpeg|png|webp)[^\']*)\'',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, html, re.IGNORECASE)
            urls.update(matches)


        try:
            json_matches = re.findall(r'<script[^>]*type="application/json"[^>]*>({.*?})</script>', html, re.DOTALL)
            for json_str in json_matches:
                try:
                    data = json.loads(json_str)
                    found = self._find_urls_in_json(data)
                    urls.update(found)
                except (json.JSONDecodeError, TypeError):
                    continue
        except Exception as e:
            logger.debug(f"JSON парсинг: {e}")

        clean_urls = []
        for url in urls:
            url = url.replace('\\/', '/')
            if url.startswith('http') and not url in clean_urls:
                clean_urls.append(url)

        return clean_urls

    def _find_urls_in_json(self, obj, found=None):
        if found is None:
            found = set()

        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in ['url', 'imageUrl', 'src'] and isinstance(value, str):
                    if '.jpg' in value.lower() or '.png' in value.lower() or '.webp' in value.lower():
                        found.add(value)
                elif isinstance(value, (dict, list)):
                    self._find_urls_in_json(value, found)
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, str) and ('.jpg' in item.lower() or '.png' in item.lower()):
                    found.add(item)
                elif isinstance(item, (dict, list)):
                    self._find_urls_in_json(item, found)

        return found

    def download_photos(self, url: str, output_dir: Optional[str] = None) -> List[str]:
        output_dir = output_dir or self.temp_dir

        if self.gallery_dl_available:
            result = self.download_with_gallery_dl(url, output_dir)
            if result:
                return result


        return self.download_with_requests(url, output_dir)

    def _find_downloaded_files(self, output_dir: str) -> List[str]:
        image_extensions = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
        current_time = time.time()
        files = []

        try:
            for file_path in Path(output_dir).iterdir():
                if file_path.is_file() and file_path.suffix.lower() in image_extensions:

                    if current_time - file_path.stat().st_mtime < 300:
                        files.append(str(file_path))
        except Exception as e:
            logger.error(f"Ошибка поиска файлов: {e}")

        return sorted(files)

    @staticmethod
    def _get_extension(content_type: str, url: str) -> str:
        content_type = content_type.lower()

        if 'jpeg' in content_type or 'jpg' in content_type:
            return 'jpg'
        elif 'png' in content_type:
            return 'png'
        elif 'webp' in content_type:
            return 'webp'


        for ext in ['webp', 'png', 'jpg', 'jpeg']:
            if f'.{ext}' in url.lower():
                return ext

        return 'jpg'
