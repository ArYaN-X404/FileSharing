import os
import logging
from dotenv import load_dotenv
from logging.handlers import RotatingFileHandler

load_dotenv()

# Telegram Bot Credentials
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
APP_ID = int(os.environ.get("APP_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")

# Private Video Storage Channel/Group ID
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "0"))

# Force Subscription Channel (Optional)
FORCE_SUB_CHANNEL = os.environ.get("FORCE_SUB_CHANNEL", None)
if FORCE_SUB_CHANNEL:
    try:
        FORCE_SUB_CHANNEL = int(FORCE_SUB_CHANNEL)
    except ValueError:
        pass

# Auto Delete settings
AUTO_DELETE_TIME = int(os.environ.get("AUTO_DELETE_TIME", "0"))
AUTO_DEL_SUCCESS_MSG = os.environ.get("AUTO_DEL_SUCCESS_MSG", "File will be deleted soon")
DISABLE_CHANNEL_BUTTON = os.environ.get("DISABLE_CHANNEL_BUTTON", "False").lower() == "true"
BOT_STATS_TEXT = os.environ.get("BOT_STATS_TEXT", "<b>BOT UPTIME</b>\n{uptime}")
USER_REPLY_TEXT = os.environ.get("USER_REPLY_TEXT", "Don't send me messages directly!")

# Owner & Admins
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
ADMINS = [OWNER_ID] if OWNER_ID else []
try:
    extra_admins = os.environ.get("ADMINS", "")
    if extra_admins:
        for x in extra_admins.split():
            ADMINS.append(int(x))
except ValueError:
    pass

ADMINS = list(set([x for x in ADMINS if x != 0]))

# Security: HMAC Secret for Token Signing
HMAC_TOKEN_SECRET = os.environ.get("HMAC_TOKEN_SECRET", "cds_super_secret_hmac_key_2026")

# Database (PostgreSQL or SQLite)
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///cds.db")

# Supabase Cloud Integration
USE_SUPABASE = os.environ.get("USE_SUPABASE", "false").lower() == "true"
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

# Delivery Protection
PROTECT_CONTENT = os.environ.get("PROTECT_CONTENT", "True").lower() == "true"

PORT = int(os.environ.get("PORT", "8080"))
TG_BOT_WORKERS = int(os.environ.get("TG_BOT_WORKERS", "4"))

START_PIC = os.environ.get("START_PIC", "")
START_MSG = os.environ.get(
    "START_MESSAGE",
    (
        "<b>Welcome {first},</b>\n\n"
        "<blockquote>"
        "<b>EduVault Delivery Engine</b>\n"
        "Your secure gateway to high-performance video streaming."
        "</blockquote>\n\n"
        "To access your lectures, select a topic from the dashboard."
    )
)

LOG_FILE_NAME = "filesharingbot.txt"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s - %(levelname)s] - %(name)s - %(message)s",
    datefmt='%d-%b-%y %H:%M:%S',
    handlers=[
        RotatingFileHandler(
            LOG_FILE_NAME,
            maxBytes=50000000,
            backupCount=10
        ),
        logging.StreamHandler()
    ]
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)

def LOGGER(name: str) -> logging.Logger:
    return logging.getLogger(name)
