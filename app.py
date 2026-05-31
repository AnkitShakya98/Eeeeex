import os
from flask import Flask
from threading import Thread
import asyncio
import importlib
from pyromod import listen
from pyrogram import idle
from Extractor import app
from Extractor.modules import ALL_MODULES
from myutils.cleanup import start_cleanup_scheduler
from pyrogram import utils

def get_peer_type_new(peer_id: int) -> str:
    peer_id_str = str(peer_id)
    if not peer_id_str.startswith("-"):
        return "user"
    elif peer_id_str.startswith("-100"):
        return "channel"
    else:
        return "chat"

utils.get_peer_type = get_peer_type_new
# Flask app
flask_app = Flask(__name__)

@flask_app.route('/')
def hello_world():
    return 'Hello from Tech VJ'

def run_flask():
    flask_app.run(host='0.0.0.0', port=1000)

async def main():
    for module in ALL_MODULES:
        importlib.import_module("Extractor.modules." + module)
        print(f"Loaded module: {module}")
    
    scheduler = start_cleanup_scheduler()
    Thread(target=run_flask, daemon=True).start()
    
    await app.start()
    print("Bot is running...")
    await idle()

loop = asyncio.get_event_loop()
loop.run_until_complete(main())
