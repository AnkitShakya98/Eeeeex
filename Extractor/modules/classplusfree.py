
import os
import asyncio
import aiohttp
import logging
import time
import re
import aiofiles
from Extractor import app
from typing import Dict, List, Any, Tuple
from pyrogram import filters
from pyrogram.client import Client
from pyrogram.types import Message
from bs4 import BeautifulSoup
from motor.motor_asyncio import AsyncIOMotorClient
from base64 import b64encode
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import datetime
import pytz

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Environment variables
#PREMIUM_LOGS = os.environ.get("PREMIUM_LOGS", "")
#CHANNEL_ID = -1002589673708
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
MONGO_URL = os.environ.get("MONGO_URI")

# MongoDB setup
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client["classplus_db"]
collection = db["user_extractions"]

# Constants
txt_dump = -1003884699177
#txt_dump = PREMIUM_LOGS
appname = "Classplus"
MAX_EXTRACTS_PER_DAY = 5
KEY = rb'm98=p?h,u6*I>A.|*()&7-.?\:2{Yr+-'  # Raw byte string
IV = b'*}~;&;$;*:-![@;>'
api = "https://api.classplusapp.com"
batch_dict = {}

async def sanitize_bname(bname: str, max_length: int = 50) -> str:
    bname = re.sub(r'[\\/:*?"<>|\t\n\r]+', '_', bname).strip()
    if len(bname) > max_length:
        bname = bname[:max_length]
    return bname

# Encryption Functions
def enc_url(url: str) -> str:
    try:
        cipher = AES.new(KEY, AES.MODE_CBC, IV)
        ciphertext = cipher.encrypt(pad(url.encode(), AES.block_size))
        encrypted_url = "enc://:" + b64encode(ciphertext).decode('utf-8')
        logger.info(f"Encrypted URL: {url} -> {encrypted_url}")
        return encrypted_url
    except Exception as e:
        logger.error(f"Encryption failed for URL {url}: {e}")
        return url

def split_name_url(line: str) -> Tuple[str, str | None]:
    if ': ' in line:
        parts = line.rsplit(': ', 1)
        name = parts[0].strip()
        url = parts[1].strip()
        if re.match(r"https?://", url):
            logger.info(f"Valid URL found: {url}")
            return name, url
        else:
            logger.warning(f"Invalid URL format in line: {line}")
    else:
        logger.warning(f"No ': ' separator in line: {line}")
    return line.strip(), None

def encrypt_file(input_file: str) -> str:
    output_file = "encrypted_" + input_file
    encrypted_count = 0
    try:
        with open(input_file, "r", encoding="utf-8") as f, open(output_file, "w", encoding="utf-8") as out:
            for line in f:
                name, url = split_name_url(line)
                if url:
                    enc = enc_url(url)
                    out.write(f"{name}: {enc}\n")
                    encrypted_count += 1
                else:
                    out.write(line.strip() + "\n")
        logger.info(f"Encrypted {encrypted_count} URLs in {output_file}")
    except Exception as e:
        logger.error(f"Error encrypting file {input_file}: {e}")
    return output_file

async def check_user_limit(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    current_date = datetime.datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%Y-%m-%d")
    try:
        user_record = await collection.find_one({"user_id": user_id, "extraction_date": current_date})
        if user_record:
            extraction_count = user_record.get("extraction_count", 0)
            if extraction_count >= MAX_EXTRACTS_PER_DAY:
                return False
        return True
    except Exception as e:
        logger.error(f"Error checking user limit for {user_id}: {e}")
        return False

async def update_user_extraction(app: Client, user_id: int, bname: str, batch_id: str, org_code: str, video_count: int, pdf_count: int, image_count: int):
    current_date = datetime.datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%Y-%m-%d")
    local_time = datetime.datetime.now(pytz.timezone('Asia/Kolkata'))
    try:
        user_record = await collection.find_one({"user_id": user_id, "extraction_date": current_date})
        if user_record:
            await collection.update_one(
                {"user_id": user_id, "extraction_date": current_date},
                {
                    "$inc": {"extraction_count": 1},
                    "$push": {
                        "extractions": {
                            "batch_name": bname,
                            "batch_id": batch_id,
                            "org_code": org_code,
                            "video_count": video_count,
                            "pdf_count": pdf_count,
                            "image_count": image_count,
                            "timestamp": local_time.isoformat()
                        }
                    }
                }
            )
        else:
            await collection.insert_one({
                "user_id": user_id,
                "username": (await app.get_users(user_id)).first_name,
                "extraction_date": current_date,
                "extraction_count": 1,
                "extractions": [{
                    "batch_name": bname,
                    "batch_id": batch_id,
                    "org_code": org_code,
                    "video_count": video_count,
                    "pdf_count": pdf_count,
                    "image_count": image_count,
                    "timestamp": local_time.isoformat()
                }]
            })
        logger.info(f"Updated extraction record for user {user_id}")
    except Exception as e:
        logger.error(f"Error updating user extraction for {user_id}: {e}")

def count_urls(file_path: str) -> Tuple[int, int, int]:
    pdf_count = 0
    video_count = 0
    total_links = 0
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            for line in file:
                name, url = split_name_url(line)
                if url:
                    total_links += 1
                    if url.endswith(".pdf"):
                        pdf_count += 1
                    elif ".m3u8" in url:
                        video_count += 1
    except Exception as e:
        logger.error(f"Error counting URLs in {file_path}: {e}")
    return total_links, pdf_count, video_count

def count_batches_and_format_ids(file_path: str) -> Tuple[int, str]:
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            lines = file.readlines()
        batch_ids = [line.split(" - ")[0].strip() for line in lines if " - " in line]
        total_batches = len(batch_ids)
        formatted_batch_ids = "&".join(batch_ids)
        logger.info(f"Counted {total_batches} batches in {file_path}")
        return total_batches, formatted_batch_ids
    except Exception as e:
        logger.error(f"Error counting batches in {file_path}: {e}")
        return 0, ""

def modify_urls_in_txt(input_file: str, output_file: str) -> None:
    try:
        with open(input_file, "r", encoding="utf-8") as file:
            content = file.read()
        modified_content = re.sub(
            r"(https://media-cdn\.classplusapp\.com/drm)/wv(/[\w\d]+/)[\w\d]+/master\.m3u8",
            r"\1\2playlist.m3u8",
            content
        )
        with open(output_file, "w", encoding="utf-8") as file:
            file.write(modified_content)
        logger.info(f"Modified URLs in {input_file} and saved to {output_file}")
    except Exception as e:
        logger.error(f"Error modifying URLs: {e}")

async def get_app_name(org_id: str) -> Tuple[str, str]:
    url = f"https://{org_id}.courses.store/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, "html.parser")
                    meta_title = soup.find("meta", {"property": "og:title"})
                    app_name = meta_title["content"] if meta_title else "❌ App name not found!"
                    meta_image = soup.find("meta", {"property": "og:image"})
                    image_url = meta_image["content"] if meta_image else ""
                    return app_name, image_url
                else:
                    logger.error(f"Failed to fetch app name, status: {response.status}")
                    return f"❌ Error: {response.status}", ""
    except Exception as e:
        logger.error(f"Error fetching app name: {e}")
        return "❌ Error", ""

async def get_list_token(orgid: str) -> str:
    url = f"{api}/v2/course/preview/org/info"
    headers = {'tutorwebsitedomain': f'https://{orgid}.courses.store'}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()
                return data["data"]["hash"]
    except Exception as e:
        logger.error(f"Error fetching list token for {orgid}: {e}")
        return ""

async def fetch_batches_new(orgid: str, list_token: str) -> List[str]:
    HEADERS = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "EN",
        "api-version": "22",
        "origin": f"https://{orgid}.courses.store",
        "referer": f"https://{orgid}.courses.store/",
        "region": "IN",
        "sec-ch-ua": '"Chromium";v="134", "Not:A-Brand";v="24", "Google Chrome";v="134"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "Windows",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "cross-site",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
    }
    batch_details = []
    try:
        async with aiohttp.ClientSession() as session:
            category_url = f"{api}/v2/course/preview/category/list/{list_token}?"
            async with session.get(category_url, headers=HEADERS) as response:
                response.raise_for_status()
                data = await response.json()
            categories = data.get("data", {}).get("categoryList", [])
            if not categories:
                return []
            for category in categories:
                category_id = category["id"]
                list_api = f"{api}/v2/course/preview/similar/{list_token}?filterId=[1]&sortId=[7]&subCatList=&mainCategory={category_id}&limit=200&offset=0"
                async with session.get(list_api, headers=HEADERS) as response:
                    response.raise_for_status()
                    data = await response.json()
                batches = data.get("data", {}).get("coursesData", [])
                for batch in batches:
                    batch_id = str(batch["id"])
                    batch_name = batch["name"]
                    price = batch["price"]
                    batch_details.append(f"{batch_id} - {batch_name} (₹{price})")
                    batch_dict[batch_id] = {
                        "name": batch_name,
                        "imageUrl": batch.get("imageUrl", ""),
                        "price": price
                    }
    except Exception as e:
        logger.error(f"Error fetching new batches for {orgid}: {e}")
    return batch_details

async def fetch_batches_old(orgid: str, list_token: str) -> List[str]:
    HEADERS = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "EN",
        "api-version": "22",
        "origin": f"https://{orgid}.courses.store",
        "referer": f"https://{orgid}.courses.store/",
        "region": "IN",
        "sec-ch-ua": '"Chromium";v="134", "Not:A-Brand";v="24", "Google Chrome";v="134"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "Windows",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "cross-site",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
    }
    batch_details = []
    try:
        async with aiohttp.ClientSession() as session:
            list_api = f"{api}/v2/course/preview/similar/{list_token}?filterId=[1]&sortId=[7]&subCatList=&mainCategory=&limit=200&offset=0"
            async with session.get(list_api, headers=HEADERS) as response:
                response.raise_for_status()
                data = await response.json()
            batches = data.get("data", {}).get("coursesData", [])
            for batch in batches:
                batch_id = str(batch["id"])
                batch_name = batch["name"]
                price = batch["price"]
                batch_details.append(f"{batch_id} - {batch_name} (₹{price})")
                batch_dict[batch_id] = {
                    "name": batch_name,
                    "imageUrl": batch.get("imageUrl", ""),
                    "price": price
                }
    except Exception as e:
        logger.error(f"Error fetching old batches for {orgid}: {e}")
    return batch_details

async def get_list_batches(orgid: str) -> str:
    try:
        list_token = await get_list_token(orgid)
        batch_details = await fetch_batches_new(orgid, list_token)
        if not batch_details:
            batch_details = await fetch_batches_old(orgid, list_token)
        return "\n".join(batch_details) if batch_details else "No batches found."
    except Exception as e:
        logger.error(f"Error getting batch list for {orgid}: {e}")
        return "No batches found."

async def download_image(image_url: str, save_path: str = "thumb.jpg") -> str:
    if not image_url:
        return ""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as response:
                if response.status == 200:
                    async with aiofiles.open(save_path, 'wb') as file:
                        await file.write(await response.read())
                    return save_path
    except Exception as e:
        logger.error(f"Error downloading image {image_url}: {e}")
    return ""

async def get_token(orgid: str, bid: str) -> str:
    url = f'{api}/v2/course/preview/org/info?courseId={bid}'
    headers = {'tutorwebsitedomain': f'https://{orgid}.courses.store'}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()
                return data["data"]["hash"]
    except Exception as e:
        logger.error(f"Error fetching token for batch {bid}: {e}")
        return ""

async def get_bname(token: str) -> str:
    url = f'{api}/v2/course/preview/{token}'
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                response.raise_for_status()
                data = await response.json()
                cname = re.sub(r'[^\w\s-]', ' ', data["data"]["details"]["name"])
                iname = data["data"]["orgDetails"]["name"]
                return f"{cname} ({iname})"
    except Exception as e:
        logger.error(f"Error fetching batch name for token {token}: {e}")
        return "Unknown Batch"

async def get_content(api_url: str) -> Dict:
    headers = {
        'authority': 'api.classplusapp.com',
        'accept': 'application/json, text/plain, */*',
        'accept-language': 'EN',
        'api-version': '22'
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, headers=headers) as response:
                response.raise_for_status()
                return await response.json()
    except Exception as e:
        logger.error(f"Error fetching content from {api_url}: {e}")
        return {}

def transform_url(thumbnail_url: str, name: str, fname: str) -> str:
    url_transforms = {
        r"https://media-cdn.classplusapp.com/videos.classplusapp.com/vod-[a-zA-Z0-9]+/(.*?)/snapshots/[a-zA-Z0-9-]+-\d+\.jpg": 
            lambda match: f"https://media-cdn.classplusapp.com/alisg-cdn-a.classplusapp.com/{match.group(1)}/master.m3u8",
        r"https://media-cdn.classplusapp.com/videos.classplusapp.com/[a-zA-Z0-9]+/[a-zA-Z0-9]+/thumbnail.png": 
            lambda match: None,
        r"https://media-cdn.classplusapp.com/videos.classplusapp.com/[a-zA-Z0-9]+/(.*?)\.jpeg": 
            lambda match: f"https://media-cdn.classplusapp.com/alisg-cdn-a.classplusapp.com/b08bad9ff8d969639b2e43d5769342cc62b510c4345d2f7f153bec53be84fe35/{match.group(1)}/master.m3u8",
        r"https://media-cdn.classplusapp.com(/.*?/.*?/.*?/)thumbnail.png": 
            lambda match: f"https://media-cdn.classplusapp.com{match.group(1)}master.m3u8",
        r"https://cpvideocdn.testbook.com/streams/(.*?)/thumbnail.png": 
            lambda match: f"https://cpvideocdn.testbook.com/{match.group(1)}/playlist.m3u8"
    }
    try:
        for pattern, transform in url_transforms.items():
            match = re.search(pattern, thumbnail_url)
            if match:
                transformed_url = transform(match)
                return f"{fname} {name}: {transformed_url}\n" if transformed_url else ""
    except Exception as e:
        logger.error(f"Error transforming URL {thumbnail_url}: {e}")
    return ""

async def process_folder_content(folder_content: Dict, fname: str = '', token: str = '') -> str:
    fetched_contents = ""
    try:
        for item in folder_content.get('data', []):
            if item.get('contentType') in (2, 3):
                name = item.get('name', '')
                if 'thumbnailUrl' in item:
                    fetched_contents += transform_url(item['thumbnailUrl'], name, fname)
            elif item.get('contentType') == 1:
                folder_id = item.get('id')
                new_fname = f"{fname} {item.get('name', '')}"
                sub_content = await get_content(f"{api}/v2/course/preview/content/list/{token}?folderId={folder_id}&limit=1000")
                fetched_contents += await process_folder_content(sub_content, fname=new_fname, token=token)
    except Exception as e:
        logger.error(f"Error processing folder content: {e}")
    return fetched_contents

def write_to_file(content: str, bname: str) -> str:
    output_file = f"{bname}.txt"
    try:
        filtered_content = "\n".join([line for line in content.split("\n") if "None" not in line])
        with open(output_file, 'w', encoding='utf-8') as file:
            file.write(filtered_content)
        logger.info(f"Content written to {output_file}")
    except Exception as e:
        logger.error(f"Error writing to file {output_file}: {e}")
    return output_file

async def login(
    app: Client,
    user_id: int,
    m: Message,
    course_content: str,
    start_time: float,
    bname: str,
    batch_id: str,
    app_name: str,
    org_code: str,
    video_count: int,
    pdf_count: int,
    image_count: int,
    price: str
):
    try:
        bname = await sanitize_bname(bname)
        file_path = f"{user_id}_{bname}.txt"
        end_time = time.time()
        response_time = end_time - start_time
        minutes = int(response_time // 60)
        seconds = int(response_time % 60)
        user = await app.get_users(user_id)
        credit = f"[{user.first_name}](tg://openmessage?user_id={user.id})"
        formatted_time = (
            f"{minutes} minutes {seconds} seconds"
            if minutes > 0
            else f"{seconds} seconds" if seconds >= 1 else f"{response_time:.2f} seconds"
        )

        caption = (
            f"**App Name :** {app_name} ({org_code})\n"
            f"**Batch Name :** {bname}\n"
            f"🎬 : {video_count} | 📁 : {pdf_count} | 🖼 : {image_count}\n"
            f"Extracted BY: {credit}\n"
            f"**╾───• Txt Extractor •───╼**\n"
            f"Time Taken: {formatted_time}"
        )

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(course_content)
        logger.info(f"Decrypted file written: {file_path}")

        helper_file = f"{bname}_helper.txt"
        modify_urls_in_txt(file_path, helper_file)
        enc_file_path = encrypt_file(helper_file)

        try:
            async with aiofiles.open(enc_file_path, 'rb') as f:
                await m.reply_document(document=file_path, caption=caption, file_name=f"{bname}.txt")
            logger.info(f"Sent decrypted file {file_path} to user {user_id}")

            async with aiofiles.open(file_path, 'rb') as f:
                await app.send_document(txt_dump, file_path, caption=caption, file_name=f"{bname}.txt")
            logger.info(f"Sent decrypted file {file_path} to log channel {txt_dump}")
        except FileNotFoundError:
            logger.error(f"File not found: {enc_file_path} or {file_path}")
            await m.reply_text("❌ Error: File not found during sending.")
        except Exception as e:
            logger.error(f"Error sending document: {e}")
            await m.reply_text(f"❌ Error sending files: {str(e)}")
        finally:
            for f in [file_path, helper_file, enc_file_path]:
                if f and os.path.exists(f):
                    os.remove(f)
                    logger.info(f"Cleaned up file: {f}")

        await update_user_extraction(app, user_id, bname, batch_id, org_code, video_count, pdf_count, image_count)
    except Exception as e:
        logger.error(f"Error in login: {e}")
        await m.reply_text(f"Error: {str(e)}")

@app.on_message(filters.command("cpfree"))
async def newccp_command(app: Client, message: Message):
    user_id = message.chat.id
    try:
        if not await check_user_limit(user_id):
            await message.reply_text(f"❌ You have reached the daily limit of {MAX_EXTRACTS_PER_DAY} extractions. Try again tomorrow.")
            return
        await process_ccp(app, message, user_id)
    except Exception as e:
        logger.error(f"Error in newccp_command: {e}")
        await message.reply_text(f"Error: {str(e)}")

async def process_ccp(app: Client, message: Message, user_id: int):
    loop = asyncio.get_event_loop()
    CONNECTOR = aiohttp.TCPConnector(limit=1000, loop=loop)
    async with aiohttp.ClientSession(connector=CONNECTOR) as session:
        try:
            editable = await app.send_message(message.chat.id, "🚀**enter your **Org ID**  ")
            try:
                org_msg = await app.listen(chat_id=message.chat.id, filters=filters.user(user_id), timeout=30)
                orgid = org_msg.text.strip()
                await org_msg.delete()
            except asyncio.TimeoutError:
                await editable.edit(" You took too long to respond.")
                return

            app_name, image_url = await get_app_name(orgid)
            extracting_prompt = (
                f"🔎 **wait......**\n\n"
                f" processing \n"
            )
            text = await app.send_message(message.chat.id, extracting_prompt)
            final_batches_list = await get_list_batches(orgid)

            unique_batches = "\n".join(set(final_batches_list.split("\n")))
            batches_file = f"{app_name}.txt"
            try:
                with open(batches_file, 'w', encoding='utf-8') as f:
                    f.write(unique_batches)
                total_batches, batch_id_string = count_batches_and_format_ids(batches_file)
                thumb_path = await download_image(image_url)
                batch_string = batch_id_string if len(batch_id_string) <= 500 else "None"
                caption = (
                    f" **Application: {app_name}**\n"
                )
                await app.send_document(user_id, batches_file, caption=caption)
            except Exception as e:
                logger.error(f"Error sending batch list: {e}")
                await text.edit(f"Error sending batch list: {str(e)}")
            finally:
                if os.path.exists(batches_file):
                    os.remove(batches_file)
                if thumb_path and os.path.exists(thumb_path):
                    os.remove(thumb_path)

            await text.delete()
            await editable.edit("**Please enter the batch ID [now you can use & ]**")
            try:
                bid_msg = await app.listen(chat_id=message.chat.id, filters=filters.user(user_id), timeout=60)
                bid_input = bid_msg.text.strip()
                await bid_msg.delete()
            except asyncio.TimeoutError:
                await editable.edit(" You took too long to respond.")
                return

            batch_ids = [bid.strip() for bid in bid_input.split('&')]
            course_ids = [line.split(" - ")[0].strip() for line in final_batches_list.split("\n") if " - " in line]
            invalid_batches = [bid for bid in batch_ids if bid not in course_ids]
            if invalid_batches:
                await editable.edit(f"Invalid batch IDs: {', '.join(invalid_batches)}. Please choose from the list.")
                return

            for bid in batch_ids:
                try:
                    start_time = time.time()
                    token = await get_token(orgid, bid)
                    bname = await get_bname(token)
                    app_name, image_url = await get_app_name(orgid)
                    details = batch_dict.get(bid, {})
                    thumb_path = await download_image(image_url)
                    try:
                        thum = await app.send_photo(
                            chat_id=message.chat.id,
                            photo=thumb_path if thumb_path else None,
                            caption=f"Processing batch: `{bname}`"
                        )
                    except Exception as e:
                        logger.error(f"Error sending thumbnail: {e}")
                        thum = await editable.edit(f"Processing batch: `{bname}` (No thumbnail)")

                    api_url = f'{api}/v2/course/preview/content/list/{token}?folderId=0&limit=1000'
                    content = await get_content(api_url)
                    fetched_contents = await process_folder_content(content, token=token)

                    if not fetched_contents:
                        await app.send_message(user_id, f"❌ No content found for batch `{bid}`.")
                        if thum:
                            await thum.delete()
                        continue

                    output_file1 = write_to_file(fetched_contents, bname)
                    output_file2 = f"{bname}_helper.txt"
                    modify_urls_in_txt(output_file1, output_file2)
                    total_links, pdfs, videos = count_urls(output_file2)

                    await login(
                        app,
                        user_id,
                        message,
                        fetched_contents,
                        start_time,
                        bname,
                        bid,
                        app_name,
                        orgid,
                        videos,
                        pdfs,
                        total_links - videos - pdfs,
                        str(details.get('price', 'N/A'))
                    )
                    if thum:
                        await thum.delete()

                except aiohttp.ClientError as e:
                    logger.error(f"Network error in batch {bid}: {e}")
                    await app.send_message(user_id, f"🌐 **Network Error in Batch {bid}**\n\n🚨 `{str(e)}`")
                except KeyError as e:
                    logger.error(f"API response error in batch {bid}: {e}")
                    await app.send_message(user_id, f"📌 **API Response Error in Batch {bid}**\n\n❗ Missing key: `{str(e)}`")
                except Exception as e:
                    logger.error(f"Unexpected error in batch {bid}: {e}")
                    await app.send_message(user_id, f"⚠️ **Unexpected Error in Batch {bid}**\n\n💢 `{str(e)}`")
                finally:
                    if thumb_path and os.path.exists(thumb_path):
                        try:
                            os.remove(thumb_path)
                            logger.info(f"Cleaned up thumbnail: {thumb_path}")
                        except Exception as e:
                            logger.error(f"Error cleaning up thumbnail {thumb_path}: {e}")

            await editable.delete()
            await app.send_message(user_id, " **Extraction completed!**")
        except Exception as e:
            logger.error(f"Error in process_ccp: {e}")
            error_msg = str(e).replace('<', '').replace('>', '').replace('[', '').replace(']', '')
            await editable.edit(f"**Error: {error_msg}**")
        finally:
            await session.close()
            if CONNECTOR:
                await CONNECTOR.close()
