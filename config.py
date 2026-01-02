import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024
TEMP_DIR = "temp_downloads"
MAX_CONCURRENT_DOWNLOADS = 15
MAX_DOWNLOADS_PER_USER = 10
VIP_USERS = [7878539493]
MAX_CONCURRENT_CHUNKS = 8
DOWNLOAD_TIMEOUT = 3600
UPLOAD_TIMEOUT = 1800
LOG_LEVEL = "NONE"
SEND_AS_DOC_LIMIT = 20 * 1024 * 1024
COOKIES_FILE = os.path.abspath("cookies.txt")
ADMIN_IDS = [7878539493]
DB_NAME = "database.db"
PREMIUM_PRICE_STARS = 250
PREMIUM_DURATION_DAYS = 30
MAX_VIDEO_DURATION = {
    "free": 900,
    "premium": 10800
}
MAX_PLAYLIST_ITEMS = {
    "free": 0,
    "premium": 50
}