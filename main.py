import os
import asyncio
import logging
import aiohttp
import datetime
from zoneinfo import ZoneInfo
import discord
import tempfile
from discord.ext import tasks, commands
from dotenv import load_dotenv
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions

# 載入環境變數
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WEATHER_API = os.getenv("WEATHER_API")

# 設定日誌 (繁體中文)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger('discord_bot')

# 設定時區 (Asia/Taipei)
TAIWAN_TZ = ZoneInfo('Asia/Taipei')

# 設定 Gemini 2.0 Flash
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # 必須開啟以讀取訊息內容
        intents.members = True          # 有助於準確處理提及功能
        # 移除原本的 Slash 指令邏輯，改為主要監聽 on_message
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # 啟動每日提醒定時任務
        self.daily_reminder.start()
        logger.info("已啟動每日氣象提醒定時任務 (預設 07:00)")

    async def on_ready(self):
        logger.info(f"機器人已上線: {self.user.name} (ID: {self.user.id})")
        logger.info("目前運作模式：監聽 @提及 觸發 Gemini 回應")

    async def on_message(self, message):
        # 忽略機器人自己的訊息，避免無限迴圈
        if message.author == self.user:
            return

        # 檢查訊息中是否提及機器人
        if self.user.mentioned_in(message):
            await self.handle_gemini_interaction(message)
        
        # 處理其他可能存在的指令
        await self.process_commands(message)

    async def handle_gemini_interaction(self, message):
        """
        處理 Gemini 的互動邏輯，包含文字與多媒體附件
        """
        # 在頻道中顯示「正在輸入中...」
        async with message.channel.typing():
            try:
                # 取得純文字內容 (移除提及標籤)
                clean_content = message.content.replace(f'<@!{self.user.id}>', '').replace(f'<@{self.user.id}>', '').strip()
                
                # 建立傳送給 Gemini 的內容清單
                # 加入系統提示，確保回應符合台灣習慣
                prompt_prefix = "請以繁體中文及台灣人習慣用語回答以下請求。如果我有提供圖片或影片，請一併分析，內容稍微簡短精確，不要長篇大論，但也不要像簡答，且只使用原生discord能正常顯示的格式回應"
                content_parts = [prompt_prefix, clean_content if clean_content else "你好！請問有什麼我可以幫你的嗎？"]

                temp_files = [] # 用於暫存下載的影片
                
                # 檢查附件 (多模態支援)
                for attachment in message.attachments:
                    content_type = attachment.content_type if attachment.content_type else ""
                    
                    # 處理圖片 (JPG/PNG)
                    if "image" in content_type:
                        logger.info(f"偵測到圖片附件: {attachment.filename}")
                        image_data = await attachment.read()
                        content_parts.append({
                            "mime_type": content_type,
                            "data": image_data
                        })
                    
                    # 處理影片 (MP4)
                    elif "video/mp4" in content_type or attachment.filename.lower().endswith('.mp4'):
                        logger.info(f"偵測到影片附件: {attachment.filename}，準備上傳至 File API")
                        
                        # Gemini 影片分析建議使用 File API 上傳
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                            await attachment.save(tmp.name)
                            temp_files.append(tmp.name)
                            
                        # 上傳檔案至 Google Gemini File API
                        video_file = await asyncio.to_thread(genai.upload_file, path=tmp.name, mime_type="video/mp4")
                        
                        # 等待影片處理完成
                        while video_file.state.name == "PROCESSING":
                            await asyncio.sleep(2)
                            video_file = await asyncio.to_thread(genai.get_file, video_file.name)
                        
                        if video_file.state.name == "FAILED":
                            logger.error("Gemini 影片處理失敗")
                            continue
                            
                        content_parts.append(video_file)

                # 呼叫 Gemini 2.5 Flash 產生回應
                logger.info(f"正在向 Gemini 2.5 Flash 發送請求 (來源: {message.author})")
                response = await asyncio.to_thread(model.generate_content, content_parts)
                
                response_text = response.text
                
                # 處理 Discord 的 2000 字元限制
                if len(response_text) > 2000:
                    for i in range(0, len(response_text), 2000):
                        await message.reply(response_text[i:i+2000], mention_author=False)
                else:
                    await message.reply(response_text, mention_author=False)

            except google_exceptions.ResourceExhausted:
                # 處理 429 錯誤 (API 額度用盡或頻率限制)
                logger.warning("觸發 Gemini API 429 錯誤：配額已達上限")
                await message.reply("超哇!API次數使用到上限了，請等一下也可能等到明天", mention_author=False)
            except Exception as e:
                logger.error(f"Gemini 處理過程中發生錯誤: {e}")
                await message.reply(f"我現在遇到了一點技術問題... (錯誤代碼: {str(e)})", mention_author=False)
            finally:
                # 清理本機暫存檔案
                for f_path in temp_files:
                    if os.path.exists(f_path):
                        os.remove(f_path)

    # ==========================================
    # Module A: 每日提醒 (保留原始邏輯)
    # ==========================================
    @tasks.loop(time=datetime.time(hour=7, minute=0, tzinfo=TAIWAN_TZ))
    async def daily_reminder(self):
        logger.info("正在執行每日定時氣象提醒任務...")
        channel_id = os.getenv("DISCORD_CHANNEL_ID")
        if not channel_id:
            logger.warning("未設定 DISCORD_CHANNEL_ID，無法發送每日提醒")
            return

        channel = self.get_channel(int(channel_id))
        if not channel:
            return

        try:
            # 獲取天氣資料
            weather = await get_weather_data()
            now = datetime.datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d")
            
            check_msg = ""
            if weather:
                for local_weather in weather:
                    # 原邏輯保留：顯示雨量機率與紫外線指數
                    mix_msg = ""
                    uv_msg = ""
                    mix_msg += f"{local_weather['mix_info']}"
                    if eval(local_weather['uv_index']) >=6:
                        uv_msg += f"，紫外線等級為「{local_weather['uv_level']}」注意防曬\n"
                    if mix_msg != uv_msg:
                        check_msg += f"{local_weather['location']}："+mix_msg+uv_msg+"\n"
            
            if check_msg:
                reminder_msg = f"早安！今天是{now} 氣象預報\n\n{check_msg}"
                await channel.send(reminder_msg)
                logger.info("每日氣象提醒發送成功")
        except Exception as e:
            logger.error(f"執行每日提醒時出錯: {e}")

# ==========================================
# 工具函式：獲取天氣資料 (保留原始邏輯)
# ==========================================
async def get_weather_data():
    """
    從中央氣象署 API 獲取天氣資料邏輯
    """
    now = datetime.datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d")
    url = WEATHER_API+f"{now}T06%3A00%3A00&timeTo={now}T10%3A00%3A00"
    results = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.error(f"氣象 API 請求失敗，狀態碼: {response.status}")
                    return None
                
                data = await response.json()
                for locations in data.get("records", {}).get("Locations", []):
                    for loc in locations.get("Location", []):
                        weather_info = {
                            "location": loc.get("LocationName"),
                            "rain_chance": "0",
                            "mix_info": "",
                            "uv_level": "未知",
                            "uv_index": ""
                        }
                        for element in loc.get("WeatherElement", []):
                            element_name = element.get("ElementName")
                            # 取得第一筆時間資料
                            time_data = element.get("Time", [{}])[0].get("ElementValue", [{}])[0]
                            if element_name == "天氣預報綜合描述":
                                weather_info["mix_info"] = time_data.get("WeatherDescription", "")[:-1]
                            elif element_name == "12小時降雨機率":
                                weather_info["rain_chance"] = time_data.get("ProbabilityOfPrecipitation", "0")
                            elif element_name == "紫外線指數":
                                weather_info["uv_index"] = time_data.get("UVIndex", "0")
                                weather_info["uv_level"] = time_data.get("UVExposureLevel", "未知")
                        results.append(weather_info)
        return results
    except Exception as e:
        logger.error(f"解析氣象資料失敗: {e}")
        return None

# ==========================================
# 啟動機器人
# ==========================================
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.error("環境變數中找不到 DISCORD_TOKEN，請檢查 .env 檔案")
    else:
        bot = MyBot()
        bot.run(DISCORD_TOKEN)
