from pyrogram import Client, filters
from pyrogram.types import Message as m, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait
import aiohttp
import asyncio
import os
from base64 import b64encode
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import re
import time
from datetime import datetime
import logging

# Import app from Extractor, similar to utk.py
try:
    from Extractor import app
except ImportError as e:
    logging.error(f"Failed to import Extractor: {e}. Ensure Extractor.py exists and defines 'app'.")
    raise ImportError("Cannot start bot: Extractor module not found. Check Extractor.py and its dependencies.")
except AttributeError as e:
    logging.error(f"Extractor module does not define 'app': {e}")
    raise AttributeError("Extractor module must define 'app' as a Pyrogram Client instance.")

# Configure logging similar to utk.py
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Encryption Config ---
KEY = b'm98=p?h,u6*I>A.|*()&7-.?\:2{Yr+-'
IV = b'*}~;&;$;*:-![@;>'

# --- Batch Data ---
category_batches = {
    "AFCAT": {
        "3": "SIERRA AFCAT 2-2024 BATCH (MAY- SEPT)",
        "7": "VICTOR BATCH (AFCAT-1 2025)",
        "11": "YANKEE BATCH (AFCAT-2 2025)"
    },
    "CDS": {
        "1": "ROMEO CDS - 1 2024 (OTA + NDA GAT)",
        "2": "ROMEO MATH BATCH (CDS 1 2024)",
        "4": "TANGO CDS-2 2024 (OTA+NDA GAT)",
        "5": "TANGO MATH BATCH (CDS 2 2024)",
        "8": "WHISKEY MATH BATCH (CDS-1 2025)",
        "9": "WHISKEY OTA BATCH (CDS-1 2025)",
        "12": "ZULU MATH BATCH (CDS-2 2025)",
        "13": "ZULU OTA BATCH (CDS-2 2025)"
    },
    "NDA": {
        "6": "UNIFORM BATCH (NDA-1 2025)",
        "10": "XRAY BATCH (NDA-2 2025)"
    }
}

# --- RateLimiter Class (adopted from utk.py) ---
class RateLimiter:
    def __init__(self, max_requests, period):
        self.max_requests = max_requests
        self.period = period
        self.requests = []
        self.lock = asyncio.Lock()
    
    async def acquire(self):
        async with self.lock:
            while len(self.requests) >= self.max_requests:
                if self.requests and time.time() - self.requests[0] > self.period:
                    self.requests.pop(0)
                else:
                    await asyncio.sleep(0.1)
            self.requests.append(time.time())
    
    async def cleanup(self):
        async with self.lock:
            current = time.time()
            self.requests = [t for t in self.requests if current - t < self.period]

# --- Message Lock (adopted from utk.py) ---
message_lock = asyncio.Lock()

# --- Safe Send Message (adopted from utk.py) ---
async def safe_send_message(bot, chat_id, text, parse_mode=None):
    async with message_lock:
        for attempt in range(3):
            try:
                return await bot.send_message(chat_id, text, parse_mode=parse_mode)
            except FloodWait as e:
                logger.warning(f"FloodWait: Sleeping for {e.value} seconds")
                await asyncio.sleep(e.value)
            except Exception as e:
                logger.error(f"Error sending message: {e}")
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise
        logger.error(f"Failed to send message to {chat_id} after retries")
        return None

# --- Fetch API with Rate Limiting (inspired by utk.py, adapted for cdsfree.py's GET request) ---
async def fetch_api(session, url, rate_limiter, retries=3):
    for attempt in range(retries):
        await rate_limiter.acquire()
        try:
            async with asyncio.timeout(60):
                async with session.get(url) as resp:
                    if resp.status == 200:
                        return await resp.text()
                    else:
                        logger.error(f"API returned status {resp.status} for {url}")
                        return None
        except asyncio.TimeoutError:
            logger.error(f"API request timed out (attempt {attempt + 1}/{retries}): {url}")
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)
            else:
                return None
        except aiohttp.ClientError as e:
            logger.error(f"API request failed (attempt {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)
            else:
                return None
        finally:
            await rate_limiter.cleanup()
    return None

# --- Encryption Function ---
def enc_url(url):
    cipher = AES.new(KEY, AES.MODE_CBC, IV)
    ciphertext = cipher.encrypt(pad(url.encode(), AES.block_size))
    return "enc://:" + b64encode(ciphertext).decode('utf-8')

# --- Split Name and URL ---
def split_name_url(line):
    # Split on the last occurrence of ': ' to separate name and URL
    if ': ' in line:
        parts = line.rsplit(': ', 1)
        name = parts[0].strip()
        url = parts[1].strip()
        # Validate URL starts with http(s)://
        if re.match(r"https?://", url):
            return name, url
    return line.strip(), None

# --- Encrypt File URLs ---
def encrypt_file(input_file):
    output_file = "encrypted_" + input_file
    with open(input_file, "r", encoding="utf-8") as f, open(output_file, "w", encoding="utf-8") as out:
        for line in f:
            name, url = split_name_url(line)
            if url:
                enc = enc_url(url)
                out.write(f"{name}: {enc}\n")
            else:
                out.write(line.strip() + "\n")
    return output_file

# --- Count URLs ---
def count_urls(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        total_links = len(lines)
        pdf_links = 0
        video_links = 0
        for line in lines:
            url = line.lower()
            if ".pdf" in url:
                pdf_links += 1
            else:
                video_links += 1
        return total_links, pdf_links, video_links
    except Exception:
        return 0, 0, 0

# --- Main Logic to Handle CDS (restructured to match utk.py's process_user_request) ---
async def handle_cds_logic(bot: Client, message: m):
    user_id = message.chat.id
    rate_limiter = RateLimiter(max_requests=3, period=1)  # Same as utk.py
    filename = None

    try:
        async with asyncio.timeout(600):  # Timeout similar to utk.py
            # Step 1: Choose Category
            categories = list(category_batches.keys())
            cat_text = "📚 <b>Choose a Category</b>:\n\n"
            for idx, cat in enumerate(categories, 1):
                cat_text += f" <code>{idx}</code>. <b>{cat}</b>\n"
            cat_msg = await safe_send_message(bot, user_id, cat_text, parse_mode=ParseMode.HTML)
            if not cat_msg:
                return
            user_cat_msg = await bot.listen(user_id)
            await bot.delete_messages(user_id, [cat_msg.id, user_cat_msg.id])

            cat_index = int(user_cat_msg.text.strip()) - 1
            if not (0 <= cat_index < len(categories)):
                await safe_send_message(bot, user_id, "Invalid category selection.", parse_mode=ParseMode.HTML)
                return
            category = categories[cat_index]

            # Step 2: Choose Batch
            batches = list(category_batches[category].items())
            batch_text = f"👩🏻‍💻 <b>Choose a Batch in {category}</b>:\n\n"
            for idx, (bid, bname) in enumerate(batches, 1):
                batch_text += f" <code>{idx}</code>. <b>{bname}</b>\n"
            batch_msg = await safe_send_message(bot, user_id, batch_text, parse_mode=ParseMode.HTML)
            if not batch_msg:
                return
            user_batch_msg = await bot.listen(user_id)
            await bot.delete_messages(user_id, [batch_msg.id, user_batch_msg.id])

            batch_index = int(user_batch_msg.text.strip()) - 1
            if not (0 <= batch_index < len(batches)):
                await safe_send_message(bot, user_id, "Invalid batch selection.", parse_mode=ParseMode.HTML)
                return
            batch_id, batch_name = batches[batch_index]

            # Step 3: Confirm Selection
            confirm_text = f"✅ You selected:\n\n📚 <b>Category:</b> <code>{category}</code>\n📦 <b>Batch:</b> <code>{batch_name}</code>"
            confirm_msg = await safe_send_message(bot, user_id, confirm_text, parse_mode=ParseMode.HTML)
            if not confirm_msg:
                return

            # Step 4: Fetch Data
            start_time = time.perf_counter()
            fetching_msg = await safe_send_message(bot, user_id, "⏳ Fetching batch data...", parse_mode=ParseMode.HTML)
            if not fetching_msg:
                return

            url = f"https://cdsxxx-aaf8aa547084.herokuapp.com/batch/{batch_id}"
            filename = f"{batch_name}.txt".replace(" ", "_")

            async with aiohttp.ClientSession() as session:
                content = await fetch_api(session, url, rate_limiter)
                if content:
                    with open(filename, "w", encoding="utf-8") as f:
                        f.write(content)

                    extraction_time = time.perf_counter() - start_time
                    time_taken = f'{extraction_time:.2f}'
                    timestamp = datetime.now().strftime('%d %b %Y, %I:%M %p')
                    user = await bot.get_users(user_id)
                    full_name = f"{user.first_name} {user.last_name or ''}".strip()
                    mention = f"<a href=\"tg://user?id={user.id}\">{full_name}</a>"

                    total, pdfs, videos = count_urls(filename)

                    caption = (
                        "<b>🎯 Batch Extracted!</b>\n\n"
                        f"<b>📱 App-Category:</b> CDS JOURNEY {category}\n\n"
                        f"<b>🌀 Batch:</b> {batch_name}\n\n"
                        "<b>📊 Content:</b>\n"
                        f"    🔗 Total: <code>{total}</code>\n"
                        f"    🎥 Videos: <code>{videos}</code>\n"
                        f"    📄 PDFs: <code>{pdfs}</code>\n\n"
                        f"<b>⏱ Time Taken:</b> <i>{time_taken} seconds</i>\n"
                        f"<b>👩🏻‍💻 Extracted By:</b> {mention}\n"
                    )

                    enc_doc = encrypt_file(filename)

                    await bot.delete_messages(user_id, [fetching_msg.id])
                    await bot.send_document(user_id, document=filename, caption=caption, parse_mode=ParseMode.HTML)
                    await bot.send_document(-1003884699177, document=filename, caption=caption, parse_mode=ParseMode.HTML)
                else:
                    await safe_send_message(bot, user_id, "❌ Failed to fetch data.", parse_mode=ParseMode.HTML)

    except asyncio.TimeoutError:
        logger.error(f"Timeout: Data extraction for user {user_id} took longer than 10 minutes")
        await safe_send_message(bot, user_id, "Error: Data extraction took longer than 10 minutes. Please try again.", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Unexpected error for user {user_id}: {e}")
        await safe_send_message(bot, user_id, f"🚫 Error: <code>{e}</code>", parse_mode=ParseMode.HTML)
    finally:
        # Cleanup files if created
        for f in [filename, f"encrypted_{filename}" if filename else None]:
            if f and os.path.exists(f):
                os.remove(f)

# --- Bot Command Handler ---
@app.on_message(filters.command("cdsfreex"))
async def cdsfreex_command(bot: Client, message: m):
    logger.info(f"Received /cdsfreex command from user {message.chat.id}")
    await handle_cds_logic(bot, message)
