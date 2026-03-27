import os
import asyncio
import logging
import discord
from discord.ext import commands
import google.generativeai as genai
from .utils.gemini_utils import process_attachments

logger = logging.getLogger('gemini_cog')

class GeminiCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # 2026 年推薦使用 gemini-3-flash
        self.model = genai.GenerativeModel('gemini-2.5-flash')

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == self.bot.user:
            return

        # 核心功能：被標記時觸發
        if self.bot.user.mentioned_in(message):
            await self.handle_gemini_interaction(message)

    async def handle_gemini_interaction(self, message):
        async with message.channel.typing():
            try:
                # 1. 處理上下文 (包含回覆他人的訊息)
                context_text = ""
                if message.reference and message.reference.message_id:
                    ref_msg = await message.channel.fetch_message(message.reference.message_id)
                    # 抓取被回覆人的名字與內容
                    ref_author = ref_msg.author.display_name
                    ref_content = ref_msg.content if ref_msg.content else "[媒體/附件內容]"
                    context_text = f"【背景資訊】使用者 {ref_author} 先前說了：'{ref_content}'\n\n"

                # 2. 清理當前訊息
                clean_content = message.content.replace(f'<@!{self.bot.user.id}>', '').replace(f'<@{self.bot.user.id}>', '').strip()
                
                # 3. 組合 Prompt 與處理附件 (PDF/影片/圖片)
                system_prompt = "你是一個親切的台灣助理。請用繁體中文回答，內容精確，並針對對話脈絡給予回應。"
                final_input_text = f"{system_prompt}\n\n{context_text}現在使用者對你說：'{clean_content if clean_content else '你好'}'"
                
                # 呼叫工具包處理附件
                content_parts, temp_files = await process_attachments(message.attachments)
                
                # 將文字插入到請求清單的最前面
                content_parts.insert(0, final_input_text)

                # 4. 呼叫 Gemini 產生回應
                response = await asyncio.to_thread(self.model.generate_content, content_parts)
                response_text = response.text

                # 5. 回覆與清理
                await self.reply_in_chunks(message, response_text)

                # 清理暫存檔
                for f in temp_files:
                    if os.path.exists(f): os.remove(f)

            except Exception as e:
                logger.error(f"Gemini interaction error: {e}")
                await message.reply(f"❌ 柏融！處理時噴錯了：{e}", mention_author=False)

    async def reply_in_chunks(self, message, text):
        """處理 Discord 2000 字元限制"""
        if len(text) <= 2000:
            await message.reply(text, mention_author=False)
        else:
            for i in range(0, len(text), 2000):
                await message.channel.send(text[i:i+2000])

async def setup(bot):
    await bot.add_cog(GeminiCog(bot))