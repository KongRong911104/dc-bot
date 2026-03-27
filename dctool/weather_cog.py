import os
import asyncio
import logging
import datetime
import aiohttp
from zoneinfo import ZoneInfo
from discord.ext import tasks, commands

logger = logging.getLogger('discord_bot')
TAIWAN_TZ = ZoneInfo('Asia/Taipei')

class WeatherCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.weather_api_url = os.getenv("WEATHER_API")
        self.channel_id = os.getenv("DISCORD_CHANNEL_ID")
        # 啟動定時任務
        self.daily_reminder.start()

    def cog_unload(self):
        # 卸載 Cog 時停止任務
        self.daily_reminder.cancel()

    @tasks.loop(time=datetime.time(hour=7, minute=0, tzinfo=TAIWAN_TZ))
    async def daily_reminder(self):
        """
        每日早上 07:00 執行氣象提醒
        """
        logger.info("執行每日定時氣象提醒任務...")
        if not self.channel_id:
            logger.warning("未設定 DISCORD_CHANNEL_ID，無法發送提醒")
            return

        channel = self.bot.get_channel(int(self.channel_id))
        if not channel:
            # 如果 get_channel 沒抓到，嘗試 fetch (有助於剛啟動時)
            try:
                channel = await self.bot.fetch_channel(int(self.channel_id))
            except:
                return

        try:
            weather_data = await self.fetch_weather_data()
            if not weather_data:
                return

            now_str = datetime.datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d")
            report = f"☀️ **早安！今天是 {now_str} 氣象預報**\n\n"
            
            for loc in weather_data:
                # 組合氣象資訊
                msg = f"📍 **{loc['location']}**：{loc['description']}"
                # 如果紫外線較高，增加警告
                if loc['uv_index'] and float(loc['uv_index']) >= 6:
                    msg += f" (⚠️ 紫外線等級：{loc['uv_level']}，請注意防曬)"
                report += msg + "\n"

            await channel.send(report)
            logger.info("✅ 每日氣象提醒發送成功")
        except Exception as e:
            logger.error(f"執行氣象任務時出錯: {e}")

    async def fetch_weather_data(self):
        """
        從中央氣象署 API 獲取氣象資料
        """
        if not self.weather_api_url:
            return None
            
        now = datetime.datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d")
        # 設定請求範圍 (今日 07:00 ~ 19:00)
        url = f"{self.weather_api_url}&timeFrom={now}T07%3A00%3A00"
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.error(f"氣象 API 請求失敗，狀態碼: {response.status}")
                        return None
                    
                    data = await response.json()
                    results = []
                    # 解析 API 回傳結構
                    for locations in data.get("records", {}).get("Locations", []):
                        for loc in locations.get("Location", []):
                            info = {
                                "location": loc.get("LocationName"),
                                "description": "暫無資料",
                                "uv_index": "0",
                                "uv_level": "未知"
                            }
                            for element in loc.get("WeatherElement", []):
                                e_name = element.get("ElementName")
                                time_list = element.get("Time", [])
                                if not time_list: continue
                                
                                val = time_list[0].get("ElementValue", [{}])[0]
                                if e_name == "天氣預報綜合描述":
                                    info["description"] = val.get("WeatherDescription", "")
                                elif e_name == "紫外線指數":
                                    info["uv_index"] = val.get("UVIndex", "0")
                                    info["uv_level"] = val.get("UVExposureLevel", "未知")
                            results.append(info)
                    return results
            except Exception as e:
                logger.error(f"解析氣象資料失敗: {e}")
                return None

async def setup(bot):
    await bot.add_cog(WeatherCog(bot))
