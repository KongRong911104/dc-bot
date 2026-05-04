import os
import asyncio
import logging
import psutil
import discord
import ollama
from discord.ext import commands
import google.generativeai as genai
from pynvml import nvmlInit, nvmlDeviceGetHandleByIndex, nvmlDeviceGetMemoryInfo, nvmlShutdown

# 引入你寫好的工具包
from .utils.hybrid_utils import process_attachments

logger = logging.getLogger('hybrid_ai_cog')

class HybridAICog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # 讀取環境變數中的 API Key
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        # 2026 推薦使用 flash 版本，速度與成本平衡最好
        self.gemini_model = genai.GenerativeModel('gemini-2.5-flash')
        
        # 設定資源閾值
        self.VRAM_THRESHOLD_GB = 4.0  # 剩餘低於 4GB 就切換 (給遊戲留空間)
        self.RAM_THRESHOLD_PERCENT = 90.0 # 系統記憶體爆了也切換
        
        # 初始化顯卡監控
        try:
            nvmlInit()
            self.nvml_enabled = True
        except Exception as e:
            logger.warning(f"NVML 初始化失敗 (可能是非 NVIDIA 環境): {e}")
            self.nvml_enabled = False

    def get_system_status(self):
        """檢查當前硬體資源狀態"""
        free_vram = 0.0
        ram_usage = psutil.virtual_memory().percent
        
        if self.nvml_enabled:
            try:
                handle = nvmlDeviceGetHandleByIndex(0) # 抓第一張顯卡 (5070 Ti)
                info = nvmlDeviceGetMemoryInfo(handle)
                free_vram = info.free / 1024**3 # 轉為 GB
            except:
                pass
        
        # 決定是否可以使用本地模型
        # 1. VRAM 夠 2. RAM 沒滿 3. Ollama 服務有開
        use_local = free_vram > self.VRAM_THRESHOLD_GB and ram_usage < self.RAM_THRESHOLD_PERCENT
        return use_local, free_vram, ram_usage

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == self.bot.user:
            return

        # 偵測是否有標記機器人
        if self.bot.user.mentioned_in(message):
            await self.handle_ai_interaction(message)

    async def handle_ai_interaction(self, message):
        async with message.channel.typing():
            try:
                # 1. 決定運行模式
                use_local, vram_free, ram_percent = self.get_system_status()
                
                # 2. 檢查是否有複雜檔案 (影片或 PDF 必須走 Gemini)
                has_complex_files = any(
                    "video" in (a.content_type or "") or 
                    "application/pdf" in (a.content_type or "") or
                    a.filename.lower().endswith(('.mp4', '.pdf'))
                    for a in message.attachments
                )

                # 3. 準備基礎 Prompt (延用你原本的上下文邏輯)
                context_text = ""
                if message.reference and message.reference.message_id:
                    ref_msg = await message.channel.fetch_message(message.reference.message_id)
                    ref_author = ref_msg.author.display_name
                    ref_content = ref_msg.content if ref_msg.content else "[媒體內容]"
                    context_text = f"【背景資訊】使用者 {ref_author} 先前說了：'{ref_content}'\n\n"

                clean_content = message.content.replace(f'<@!{self.bot.user.id}>', '').replace(f'<@{self.bot.user.id}>', '').strip()
                system_prompt = "你是一個親切說話簡潔且精確的助理，請用繁體中文台灣人的語言習慣回答。"
                final_input_text = f"{system_prompt}\n\n{context_text}現在使用者對你說：'{clean_content if clean_content else '你好'}'"

                # --- 執行分支 ---
                if not has_complex_files and use_local:
                    await self.run_local_mode(message, final_input_text, vram_free)
                else:
                    await self.run_gemini_mode(message, final_input_text, vram_free if use_local else "N/A")

            except Exception as e:
                logger.error(f"AI Interaction Error: {e}")
                await message.reply(f"❌ 唉呦！處理時發生錯誤了：{e}", mention_author=False)

    async def run_local_mode(self, message, prompt, vram):
        """執行本地推論 (Ollama)"""
        images = []
        for a in message.attachments:
            if "image" in (a.content_type or ""):
                img_data = await a.read()
                images.append(img_data)

        # 決定模型：有圖用 vision，沒圖用 phi4
        target_model = "llama3.2-vision" if images else "phi4"
        
        # 呼叫 Ollama API (非阻塞)
        response = await asyncio.to_thread(
            ollama.chat,
            model=target_model,
            messages=[{'role': 'user', 'content': prompt, 'images': images}],
            keep_alive=0
        )
        
        content = response['message']['content']
        footer = f"\nby {target_model}"
        await self.reply_in_chunks(message, content + footer)
    async def run_gemini_mode(self, message, prompt, vram):
        """執行雲端推論 (Gemini API)"""
        # 呼叫你原本寫在 utils 裡的處理邏輯
        content_parts, temp_files = await process_attachments(message.attachments)
        content_parts.insert(0, prompt)

        # 執行 Gemini 推論
        response = await asyncio.to_thread(self.gemini_model.generate_content, content_parts)
        
        footer = f"\nby gemini 2.5 flash"
        await self.reply_in_chunks(message, response.text + footer)
        # 清理由 process_attachments 產生的暫存檔
        for f in temp_files:
            if os.path.exists(f):
                try: os.remove(f)
                except: pass

    async def reply_in_chunks(self, message, text):
        """處理 Discord 2000 字元限制"""
        if len(text) <= 2000:
            await message.reply(text, mention_author=False)
        else:
            for i in range(0, len(text), 2000):
                chunk = text[i:i+2000]
                if i == 0:
                    await message.reply(chunk, mention_author=False)
                else:
                    await message.channel.send(chunk)

async def setup(bot):
    await bot.add_cog(HybridAICog(bot))