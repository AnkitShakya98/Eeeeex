from pyrogram import Client, filters
from pyrogram.types import Message as m
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
import concurrent.futures

# Import app from Extractor
try:
    from Extractor import app
except ImportError as e:
    logging.error(f"Failed to import Extractor: {e}. Ensure Extractor.py exists and defines 'app'.")
    raise ImportError("Cannot start bot: Extractor module not found. Check Extractor.py and its dependencies.")
except AttributeError as e:
    logging.error(f"Extractor module does not define 'app': {e}")
    raise AttributeError("Extractor module must define 'app' as a Pyrogram Client instance.")

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Encryption Config ---
KEY = b'm98=p?h,u6*I>A.|*()&7-.?\:2{Yr+-'
IV = b'*}~;&;$;*:-![@;>'

# --- Concurrency Config ---
MAX_CONCURRENT_REQUESTS = 40
semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

# --- Message Lock ---
message_lock = asyncio.Lock()

# --- Safe Send Message ---
async def safe_send_message(bot, chat_id, text, parse_mode=None):
    async with message_lock:
        for attempt in range(3):
            try:
                logger.info(f"Sending message to {chat_id}: {text[:50]}...")
                return await bot.send_message(chat_id, text, parse_mode=parse_mode)
            except FloodWait as e:
                logger.warning(f"FloodWait: Sleeping for {e.value} seconds")
                await asyncio.sleep(e.value + 1)
            except Exception as e:
                logger.error(f"Error sending message: {e}")
                if attempt < 2:
                    await asyncio.sleep(1)
                else:
                    raise
        logger.error(f"Failed to send message to {chat_id} after retries")
        return None

# --- Fetch API with Semaphore ---
async def fetch_api(session, url, retries=3):
    async with semaphore:
        for attempt in range(retries):
            try:
                logger.info(f"Fetching API: {url}")
                async with asyncio.timeout(30):
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            return await resp.json()
                        else:
                            logger.error(f"API returned status {resp.status} for {url}")
                            return None
            except asyncio.TimeoutError:
                logger.error(f"API request timed out (attempt {attempt + 1}/{retries}): {url}")
                if attempt < retries - 1:
                    await asyncio.sleep(1)
            except aiohttp.ClientError as e:
                logger.error(f"API request failed (attempt {attempt + 1}/{retries}): {e}")
                if attempt < retries - 1:
                    await asyncio.sleep(1)
        return None

# --- Encryption Function ---
def enc_url(url):
    cipher = AES.new(KEY, AES.MODE_CBC, IV)
    ciphertext = cipher.encrypt(pad(url.encode(), AES.block_size))
    return "enc://:" + b64encode(ciphertext).decode('utf-8')

# --- Split Name and URL ---
def split_name_url(line):
    if ': ' in line:
        parts = line.rsplit(': ', 1)
        name = parts[0].strip()
        url = parts[1].strip()
        if re.match(r"https?://", url):
            return name, url
    return line.strip(), None

# --- Encrypt File URLs ---
def encrypt_file(input_file, items):
    output_file = f"encrypted_{input_file}"
    with open(output_file, "w", encoding="utf-8") as out:
        for line in items:
            name, url = split_name_url(line)
            if url:
                enc = enc_url(url)
                out.write(f"{name}: {enc}\n")
            else:
                out.write(line.strip() + "\n")
    return output_file

# --- Count URLs ---
def count_urls(items):
    total_links = len(items)
    pdf_links = sum(1 for line in items if ".pdf" in line.lower())
    video_links = total_links - pdf_links
    return total_links, pdf_links, video_links

# --- Fetch Videos and PDFs for a Single Course ---
async def fetch_videos_and_pdfs(session, course_id, course_title):
    url_chapters = f"https://auth.ssccglpinnacle.com/api/youtubeChapters/course/{course_id}"
    data = await fetch_api(session, url_chapters)
    if not data:
        logger.error(f"Failed to fetch data for course ID {course_id}")
        return None, None

    items = []
    pdf_urls = []

    # Collect all PDF URLs for parallel fetching
    for chapter in data:
        for topic in chapter.get('topics', []):
            formatted_video = f"({chapter['chapterTitle']}) {topic['videoTitle']}: {topic['videoYoutubeLink']}"
            items.append(formatted_video)
            if "selectedPdf" in topic and topic["selectedPdf"]:
                pdf_urls.append((chapter['chapterTitle'], topic['pdfTitle'], topic['selectedPdf']))

    # Fetch PDFs in parallel
    async def fetch_pdf(chapter_title, pdf_title, pdf_id):
        pdf_url = f"https://auth.ssccglpinnacle.com/api/pdfs/{pdf_id}"
        pdf_data = await fetch_api(session, pdf_url)
        if pdf_data and "cloudFrontUrl" in pdf_data:
            return f"({chapter_title}) {pdf_title}: {pdf_data['cloudFrontUrl']}"
        return None

    pdf_results = await asyncio.gather(
        *(fetch_pdf(chapter_title, pdf_title, pdf_id) for chapter_title, pdf_title, pdf_id in pdf_urls),
        return_exceptions=True
    )

    # Add successful PDF results to items
    for result in pdf_results:
        if isinstance(result, str):
            items.append(result)

    if not items:
        return None, None

    # Save to file
    filename = re.sub(r'[\\/:*?"<>|\t\n\r]+', '', course_title).strip()[:50] + ".txt"
    try:
        with open(filename, "w", encoding="utf-8") as file:
            for item in items:
                file.write(f"{item}\n")
        logger.info(f"Videos and PDFs saved to {filename}")
        return filename, items
    except OSError as e:
        logger.error(f"Failed to write file {filename}: {e}")
        return None, None

# --- Main Logic to Handle Pen ---
async def handle_pen_logic(bot: Client, message: m):
    user_id = message.chat.id
    files_to_cleanup = []

    try:
        async with aiohttp.ClientSession() as session:
            # Step 1: Choose Exam Type
            exam_types = ["ssc", "railway", "Delhi%20Police", "UP%20POLICE"]
            exam_text = "📚 <b>Choose an Exam Type</b>:\n\n"
            for idx, exam in enumerate(exam_types, 1):
                exam_text += f" <code>{idx}</code>. <b>{exam.replace('%20', ' ')}</b>\n"
            exam_msg = await safe_send_message(bot, user_id, exam_text, parse_mode=ParseMode.HTML)
            if not exam_msg:
                return
            user_exam_msg = await bot.listen(user_id)
            await bot.delete_messages(user_id, [exam_msg.id, user_exam_msg.id])

            exam_index = int(user_exam_msg.text.strip()) - 1
            if not (0 <= exam_index < len(exam_types)):
                await safe_send_message(bot, user_id, "Invalid exam type selection.", parse_mode=ParseMode.HTML)
                return
            exam_type = exam_types[exam_index]

            # Step 2: Fetch and Display Courses
            url_courses = f"https://auth.ssccglpinnacle.com/api/videoCourses/{exam_type}"
            courses = await fetch_api(session, url_courses)
            if not courses:
                await safe_send_message(bot, user_id, f"❌ Failed to fetch courses for {exam_type.replace('%20', ' ')}.", parse_mode=ParseMode.HTML)
                return

            course_text = "📚 <b>Available Courses</b>:\n\n"
            for idx, course in enumerate(courses, 1):
                course_text += f" <code>{idx}</code>. <b>{course['courseTitle']} (ID: {course['_id']})</b>\n"
            course_text += "\nEnter the numbers of the courses to fetch (e.g., 1,2,3):"
            course_msg = await safe_send_message(bot, user_id, course_text, parse_mode=ParseMode.HTML)
            if not course_msg:
                return
            user_course_msg = await bot.listen(user_id)
            await bot.delete_messages(user_id, [course_msg.id, user_course_msg.id])

            selected_numbers = user_course_msg.text.strip().split(",")
            selected_courses = []
            for num in selected_numbers:
                try:
                    num = int(num.strip()) - 1
                    if 0 <= num < len(courses):
                        selected_courses.append(courses[num])
                    else:
                        await safe_send_message(bot, user_id, f"Invalid selection: {num + 1}", parse_mode=ParseMode.HTML)
                except ValueError:
                    await safe_send_message(bot, user_id, f"Invalid input: {num}", parse_mode=ParseMode.HTML)

            if not selected_courses:
                await safe_send_message(bot, user_id, "No valid courses selected.", parse_mode=ParseMode.HTML)
                return

            # Step 3: Process Courses in Parallel
            async def process_course(course):
                course_id = course['_id']
                course_title = course['courseTitle']
                confirm_text = f"✅ Processing course:\n\n📚 <b>Course:</b> <code>{course_title}</code> (ID: {course_id})"
                confirm_msg = await safe_send_message(bot, user_id, confirm_text, parse_mode=ParseMode.HTML)
                if not confirm_msg:
                    return

                start_time = time.perf_counter()
                fetching_msg = await safe_send_message(bot, user_id, f"⏳ Fetching data for {course_title}...", parse_mode=ParseMode.HTML)
                if not fetching_msg:
                    return

                filename, items = await fetch_videos_and_pdfs(session, course_id, course_title)
                if filename and items:
                    extraction_time = time.perf_counter() - start_time
                    time_taken = f'{extraction_time:.2f}'
                    user = await bot.get_users(user_id)
                    full_name = f"{user.first_name} {user.last_name or ''}".strip()
                    mention = f"<a href=\"tg://user?id={user.id}\">{full_name}</a>"

                    total, pdfs, videos = count_urls(items)

                    caption = (
                        "<b>🎯 Course Extracted!</b>\n\n"
                        f"<b>📱 Exam Type:</b> {exam_type.replace('%20', ' ')}\n\n"
                        f"<b>🌀 Course:</b> {course_title}\n\n"
                        "<b>📊 Content:</b>\n"
                        f"    🔗 Total: <code>{total}</code>\n"
                        f"    🎥 Videos: <code>{videos}</code>\n"
                        f"    📄 PDFs: <code>{pdfs}</code>\n\n"
                        f"<b>⏱ Time Taken:</b> <i>{time_taken} seconds</i>\n"
                        f"<b>👩🏻‍💻 Extracted By:</b> {mention}\n"
                    )

                    enc_doc = encrypt_file(filename, items)
                    files_to_cleanup.append(filename)
                    files_to_cleanup.append(enc_doc)

                    await bot.delete_messages(user_id, [fetching_msg.id])
                    await bot.send_document(user_id, document=filename, caption=caption, parse_mode=ParseMode.HTML)
                    await bot.send_document(-1003884699177, document=filename, caption=caption, parse_mode=ParseMode.HTML)
                else:
                    await safe_send_message(bot, user_id, f"❌ Failed to fetch data for {course_title}.", parse_mode=ParseMode.HTML)

            # Run course processing in parallel
            await asyncio.gather(*(process_course(course) for course in selected_courses), return_exceptions=True)

    except asyncio.TimeoutError:
        logger.error(f"Timeout: Data extraction for user {user_id} took longer than 10 minutes")
        await safe_send_message(bot, user_id, "Error: Data extraction took longer than 10 minutes. Please try again.", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Unexpected error for user {user_id}: {e}")
        await safe_send_message(bot, user_id, f"🚫 Error: <code>{e}</code>", parse_mode=ParseMode.HTML)
    finally:
        for f in files_to_cleanup:
            if f and os.path.exists(f):
                os.remove(f)

# --- Bot Command Handler ---
@app.on_message(filters.command("pen"))
async def pen_command(bot: Client, message: m):
    logger.info(f"Received /pen command from user {message.chat.id}")
    # Run in a separate task to isolate from other modules
    asyncio.create_task(handle_pen_logic(bot, message))
