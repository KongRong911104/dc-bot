import os
import asyncio
import logging
import aiohttp
from bs4 import BeautifulSoup
import datetime
from zoneinfo import ZoneInfo
import discord
import tempfile
from discord.ext import tasks, commands
from dotenv import load_dotenv
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
import dctool.weather as weather_function
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
        self.sunday_gas_task.start()
        logger.info("已啟動周日汽油價格變動提醒定時任務 (預設 12:10)")

    async def on_ready(self):
        logger.info(f"機器人已上線: {self.user.name} (ID: {self.user.id})")
        logger.info("目前運作模式：監聽 @提及 觸發 Gemini 回應")
        self.tree.clear_commands(guild=None)
        await self.tree.sync()
        logger.info("已成功清空並同步全域 Slash 指令！")
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
        處理 Gemini 的互動邏輯，包含上下文(回覆鏈)、文字與多媒體附件
        """
        # 在頻道中顯示「正在輸入中...」
        async with message.channel.typing():
            try:
                # 1. 取得純文字內容 (移除提及標籤)
                clean_content = message.content.replace(f'<@!{self.user.id}>', '').replace(f'<@{self.user.id}>', '').strip()
                
                # --- [核心修改] 處理上下文 (Grok 模式) ---
                context_text = ""
                # 檢查這則訊息是否是一則「回覆」
                if message.reference and message.reference.message_id:
                    try:
                        # 嘗試獲取被回覆的原始訊息物件
                        #resolved = message.reference.resolved # 有時 Discord 會解析好，但 fetch 比較穩
                        ref_msg = await message.channel.fetch_message(message.reference.message_id)
                        
                        # 整理上下文：誰說了什麼
                        author_name = ref_msg.author.display_name
                        ref_content = ref_msg.content if ref_msg.content else "[純媒體訊息，無文字]"
                        
                        # 如果原始訊息也有附件，可以在這裡加一句提醒 Gemini (雖然無法傳送原始圖片資料)
                        if ref_msg.attachments:
                            ref_content += f" (此訊息包含 {len(ref_msg.attachments)} 個附件)"

                        context_text = f"【對話脈絡】\n使用者 {author_name} 先說了：'{ref_content}'\n\n現在使用者 {message.author.display_name} 回覆他並對你說：\n"
                        logger.info(f"成功獲取引用上下文 (來源: {author_name})")
                    except discord.NotFound:
                        context_text = "【對話脈絡】\n(你正在處理一個回覆，但原始訊息已被刪除)\n\n使用者對你說：\n"
                    except Exception as e:
                        logger.error(f"獲取引用訊息失敗: {e}")
                        context_text = "【對話脈絡】\n(你正在處理一個回覆，但無法讀取原始訊息內容)\n\n使用者對你說：\n"

                # 2. 建立系統提示，確保回應符合台灣習慣
                # 將提示詞結構化，明確區分上下文與當前請求
                system_instruction = (
                    "請以繁體中文及台灣人習慣用語回答。回應內容簡短精確，不要長篇大論。只使用原生Discord能正常顯示的格式"
                    "如果有提供【對話脈絡】，請分析該脈絡後，針對使用者的回覆給出精闢的解答，像真人助理一樣。"
                )
                
                # 組裝最終的文字提示詞
                final_prompt = f"{context_text}'{clean_content if clean_content else "你好！"}'"
                
                # 建立傳送給 Gemini 的內容清單
                # 第一個元素放 System Instruction，第二個放最終提示詞
                content_parts = [system_instruction, final_prompt]

                # --- 附件處理部分維持不變 ---
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
                        
                        # 使用 tempfile 建立安全暫存檔
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                            await attachment.save(tmp.name)
                            temp_files.append(tmp.name)
                            tmp_path = tmp.name # 記錄路徑供下方使用

                        # 上傳檔案至 Google Gemini File API (注意：sdk呼叫需使用 to_thread)
                        video_file = await asyncio.to_thread(
                            genai.upload_file, 
                            path=tmp_path, 
                            mime_type="video/mp4"
                        )
                        
                        # 等待影片處理完成
                        while video_file.state.name == "PROCESSING":
                            await asyncio.sleep(2)
                            video_file = await asyncio.to_thread(genai.get_file, video_file.name)
                        
                        if video_file.state.name == "FAILED":
                            logger.error("Gemini 影片處理失敗")
                            continue
                            
                        content_parts.append(video_file)

                # --- 發送請求與處理回應維持不變 ---
                # 呼叫 Gemini 2.5 Flash 產生回應
                logger.info(f"正在向 Gemini 2.5 Flash 發送請求 (來源: {message.author})，上下文長度: {len(context_text)}")
                
                # model 變數應在 __init__ 中定義好，例如 self.model
                response = await asyncio.to_thread(model.generate_content, content_parts)
                
                response_text = response.text
                
                # 處理 Discord 的 2000 字元限制
                if len(response_text) > 2000:
                    for i in range(0, len(response_text), 2000):
                        # 第一次使用 reply，之後使用 send 避免重複提及 (看你習慣)
                        await message.reply(response_text[i:i+2000], mention_author=False)
                else:
                    await message.reply(response_text, mention_author=False)

            except google_exceptions.ResourceExhausted:
                # 處理 429 錯誤
                logger.warning("觸發 Gemini API 429 錯誤：配額已達上限")
                await message.reply("超哇!API次數使用到上限了請等明天", mention_author=False)
            except Exception as e:
                logger.error(f"Gemini 處理過程中發生錯誤: {e}")
                # 修正 str(e) 的問題
                await message.reply(f"我現在遇到了一點技術問題... 救我!!!!!(錯誤代碼: {e})", mention_author=False)
            finally:
                # 3. 清理本機暫存檔案 (使用防錯處理)
                for f_path in temp_files:
                    try:
                        if os.path.exists(f_path):
                            os.remove(f_path)
                            logger.debug(f"已清理暫存檔: {f_path}")
                    except Exception as clean_error:
                        logger.error(f"清理暫存檔失敗 {f_path}: {clean_error}")

    # ==========================================
    # Module A: 每日提醒 
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
            weather = await weather_function.get_weather_data()
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
    # Module B: 周日提醒
    # ==========================================
    # 每天 12:10 觸發
    @tasks.loop(time=datetime.time(hour=12, minute=10, tzinfo=TAIWAN_TZ))
    async def sunday_gas_task(self):
        # 判斷是否為週日 (weekday == 6)
        if datetime.now(TAIWAN_TZ).weekday() == 6:
            channel_id = os.getenv("DISCORD_CHANNEL_ID")
            channel = self.get_channel(int(channel_id))

        if channel:
            data = await get_gas_data_no_ai()
            await send_gas_embed(channel, data)

async def get_weather_data():
    """
    從中央氣象署 API 獲取天氣資料邏輯
    """
    now = datetime.datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d")
    url = WEATHER_API+f"&timeFrom={now}T07%3A00%3A00&timeTo={now}T19%3A00%3A00"
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
                           # 1. 安全取得 Time 列表
                            time_list = element.get("Time")
                            # logger.info(element_name)
                            # logger.info(element)
                            # 檢查是否有資料，若無資料則給予預設值並跳過
                            if len(time_list)<1:
                                default_values = {
                                    "天氣預報綜合描述": "暫無描述",
                                    "12小時降雨機率": "0",
                                    "紫外線指數": "0"
                                }
                                # 根據 element_name 給予相對應的預設值
                                if element_name == "天氣預報綜合描述": weather_info["mix_info"] = default_values[element_name]
                                elif element_name == "12小時降雨機率": weather_info["rain_chance"] = default_values[element_name]
                                elif element_name == "紫外線指數":
                                    weather_info["uv_index"] = "0"
                                    weather_info["uv_level"] = "無資料"
                                continue

                            # 2. 確定有資料後，安全取出第一筆 ElementValue
                            element_values = time_list[0].get("ElementValue", [])
                            if not element_values:
                                continue

                            time_data = element_values[0]
                            
                            # 3. 執行原本的賦值邏輯 (改用 .get 避免 Key 缺失)
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

async def get_gas_data_no_ai():
    url = "https://gas.goodlife.tw/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                return None
            
            html = await resp.text()
            soup = BeautifulSoup(html, 'lxml')
            
            try:
                # 1. 抓取調幅狀態 (截圖中的 <h2>不 調 整</h2>)
                # 這裡找包含預計字樣下方的第一個 h2
                status_element = soup.select_one('li.main h2')
                status = status_element.get_text(strip=True) if status_element else "未知狀態"
                
                # 2. 抓取中油區塊 (第一個 id="cpc" 的 div)
                cpc_div = soup.find('div', id='cpc')
                items = cpc_div.find_all('li')
                
                # 3. 提取各油種價格 (去除 <h3> 標籤後的文字)
                # items[0] 是 92, items[1] 是 95, items[2] 是 98
                price_92 = items[0].get_text().replace('92:', '').strip()
                price_95 = items[1].get_text().replace('95油價:', '').strip()
                price_98 = items[2].get_text().replace('98:', '').strip()
                price_diesel = items[3].get_text().replace('柴油:', '').strip()
                
                return {
                    "status": status,
                    "92": price_92,
                    "95": price_95,
                    "98": price_98,
                    "diesel": price_diesel,
                    "url": url
                }
            except Exception as e:
                print(f"解析 HTML 失敗: {e}")
                return None
            
async def send_gas_embed(channel, data):
    if not data:
        await channel.send("❌ 無法獲取油價資料，請檢查來源網站。")
        return

    # 建立一個灰色的 Embed，模仿截圖風格
    embed = discord.Embed(
        title=f"油價公告  <{data['status']}>",
        description="",
        color=discord.Color.dark_grey(),
        timestamp=datetime.datetime.now()
    )

    # 設定主要內容區塊
    value_text = f"92: {data['92']}      95: {data['95']}      98: {data['98']}"
    
    embed.add_field(
        name="",
        value=f"```今日中油油價：\n{value_text}\n```", # 使用 code block 讓數字對齊
        inline=False
    )
    
    embed.add_field(
        name="",
        value=f"**周一汽油每公升{data['status']}**",
        inline=False
    )

    embed.set_footer(text=f"Source: {data['url']}")
    
    await channel.send(embed=embed)
# ==========================================
# 啟動機器人
# ==========================================
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.error("環境變數中找不到 DISCORD_TOKEN，請檢查 .env 檔案")
    else:
        bot = MyBot()
        bot.run(DISCORD_TOKEN)
