import os
from os import getenv
# ---------------R---------------------------------
API_ID = int(os.environ.get("API_ID", "32310443"))
# ------------------------------------------------
API_HASH = os.environ.get("API_HASH", "c356e2c32fca6e1ad119d5ea7134ae88")
# ----------------D--------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8926386211:AAGxZowZRNL4iyPtGTamCcIb_JG74dNVwNg")
# -----------------A-------------------------------
BOT_USERNAME = os.environ.get("AnkitExtractorBot")
# ------------------X------------------------------
OWNER_ID = int(os.environ.get("OWNER_ID", "6748792256"))
# ------------------X------------------------------
CREATOR_ID = int(os.environ.get("CREATOR_ID", "6748792256"))
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", "-1003884699177"))


SUDO_USERS = list(map(int, getenv("SUDO_USERS", "8085418235").split()))
# ------------------------------------------------
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "-1003884699177"))
# ------------------------------------------------
MONGO_URL = os.environ.get("MONGO_URL", "mongodb+srv://ankitshakya:ankitshakya@cluster0.cupu9yp.mongodb.net/?appName=Cluster0")
# -----------------------------------------------
PREMIUM_LOGS = int(os.environ.get("PREMIUM_LOGS", "-1003884699177"))
