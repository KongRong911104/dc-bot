import os
import asyncio
import logging
import tempfile
import discord
import google.generativeai as genai
from discord.ext import commands
from google.api_core import exceptions as google_exceptions

logger = logging.getLogger('discord_bot')

class GeminiCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # 設定 Gemini API Key
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
        # 使用用戶要求的 'gemini-2.5-flash'
        self.model = genai.GenerativeModel('gemini-2.5-flash')

    @commands.Cog.listener()
    async def on_message(self, message):
        """
        監聽所有訊息，若機器人被提及則觸發 AI 回應
        """
        # 忽略機器人自己的訊息
        if message.author == self.bot.user:
            return

        # 檢查是否被提及
        if self.bot.user.mentioned_in(message):
            await self.handle_gemini_interaction(message)

    async def handle_gemini_interaction(self, message):
        """
        處理 Gemini AI 互動邏輯 (包含 Grok 風格對話、多媒體與 File API)
        """
        async with message.channel.typing():
            temp_files = [] # 追蹤暫存檔
            try:
                # 1. 清理提及標籤，取得純文字
                clean_content = message.content.replace(f'<@!{self.bot.user.id}>', '').replace(f'<@{self.bot.user.id}>', '').strip()
                
                # --- [對話脈絡處理] ---
                context_text = ""
                if message.reference and message.reference.message_id:
                    try:
                        # 獲取被回覆的原始訊息 (Grok 風格)
                        ref_msg = await message.channel.fetch_message(message.reference.message_id)
                        author_name = ref_msg.author.display_name
                        ref_content = ref_msg.content if ref_msg.content else "[多媒體訊息]"
                        context_text = f"【對話脈絡】\n{author_name} 之前說：'{ref_content}'\n\n現在使用者的回覆：\n"
                    except Exception as e:
                        logger.error(f"取得引用上下文失敗: {e}")

                # 2. 建立系統提示與組裝請求
                system_instruction = (
                    "你是一個友善的台灣真人助理。請使用繁體中文(台灣習慣用語)回答。"
                    "回應內容需簡短精確，並使用 Discord Markdown 格式。"
                )
                final_prompt = f"{context_text}'{clean_content if clean_content else '你好！'}'"
                
                # content_parts 是傳送給 Gemini 的核心清單
                content_parts = [system_instruction, final_prompt]

                # 3. 附件處理 (多模態支援)
                for attachment in message.attachments:
                    mime_type = attachment.content_type or ""
                    
                    # 圖片直接讀取內容 (Byte Data)
                    if "image" in mime_type:
                        image_data = await attachment.read()
                        content_parts.append({
                            "mime_type": mime_type,
                            "data": image_data
                        })
                    
                    # 影片使用 Google File API 上傳處理
                    elif "video" in mime_type or attachment.filename.lower().endswith('.mp4'):
                        # 建立暫存檔
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                            await attachment.save(tmp.name)
                            temp_files.append(tmp.name)
                            tmp_path = tmp.name

                        # 非阻塞呼叫 File API 上傳
                        video_file = await asyncio.to_thread(
                            genai.upload_file, 
                            path=tmp_path, 
                            mime_type="video/mp4"
                        )
                        
                        # 輪詢確認影片處理狀態
                        while video_file.state.name == "PROCESSING":
                            await asyncio.sleep(2)
                            video_file = await asyncio.to_thread(genai.get_file, video_file.name)
                        
                        if video_file.state.name == "ACTIVE":
                            content_parts.append(video_file)
                        else:
                            logger.error(f"影片處理失敗: {video_file.state.name}")

                # 4. 向 Gemini 請求回應 (使用 asyncio.to_thread 防止阻塞)
                response = await asyncio.to_thread(self.model.generate_content, content_parts)
                response_text = response.text

                # 5. 發送回覆 (處理 2000 字限制)
                if len(response_text) > 2000:
                    for i in range(0, len(response_text), 2000):
                        await message.reply(response_text[i:i+2000], mention_author=False)
                else:
                    await message.reply(response_text, mention_author=False)

            except google_exceptions.ResourceExhausted:
                await message.reply("⚠️ 超哇！API 次數達到上限了，請稍後再試，或贊助開發者升級付費版。", mention_author=False)
            except Exception as e:
                logger.error(f"Gemini 互動出錯: {e}")
                await message.reply(f"❌ 發生技術錯誤：{e}", mention_author=False)
            finally:
                # 清理本機暫存檔案
                for f_path in temp_files:
                    try:
                        if os.path.exists(f_path):
                            os.remove(f_path)
                    except Exception as clean_error:
                        logger.error(f"清理暫存檔失敗 {f_path}: {clean_error}")

async def setup(bot):
    await bot.add_cog(GeminiCog(bot))
