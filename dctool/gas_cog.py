import os
import asyncio
import logging
import datetime
import discord
import aiohttp
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo
from discord.ext import tasks, commands

logger = logging.getLogger('discord_bot')
TAIWAN_TZ = ZoneInfo('Asia/Taipei')

class GasCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.channel_id = os.getenv("DISCORD_CHANNEL_ID")
        # 啟動定時任務
        self.sunday_gas_task.start()

    def cog_unload(self):
        self.sunday_gas_task.cancel()

    @tasks.loop(time=datetime.time(hour=12, minute=10, tzinfo=TAIWAN_TZ))
    async def sunday_gas_task(self):
        """
        每週日 12:10 執行油價提醒
        """
        # 僅在週日執行 (weekday == 6)
        if datetime.datetime.now(TAIWAN_TZ).weekday() != 6:
            return

        logger.info("執行每週油價變動提醒任務...")
        if not self.channel_id:
            return

        channel = self.bot.get_channel(int(self.channel_id))
        if not channel:
            try:
                channel = await self.bot.fetch_channel(int(self.channel_id))
            except:
                return

        try:
            gas_data = await self.fetch_gas_data()
            if gas_data:
                await self.send_gas_embed(channel, gas_data)
                logger.info("✅ 週日油價提醒發送成功")
        except Exception as e:
            logger.error(f"執行油價任務時出錯: {e}")

    async def fetch_gas_data(self):
        """
        爬取油價資訊 (Goodlife 網站)
        """
        url = "https://gas.goodlife.tw/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200: return None
                    
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'lxml')
                    
                    # 解析調幅狀態
                    status_el = soup.select_one('li.main h2')
                    status = status_el.get_text(strip=True) if status_el else "未知"
                    
                    # 解析中油價格
                    cpc_div = soup.find('div', id='cpc')
                    items = cpc_div.find_all('li')
                    
                    return {
                        "status": status,
                        "92": items[0].get_text().replace('92:', '').strip(),
                        "95": items[1].get_text().replace('95油價:', '').strip(),
                        "98": items[2].get_text().replace('98:', '').strip(),
                        "diesel": items[3].get_text().replace('柴油:', '').strip(),
                        "url": url
                    }
            except Exception as e:
                logger.error(f"油價爬蟲失敗: {e}")
                return None

    async def send_gas_embed(self, channel, data):
        """
        發送格式化的油價 Embed 訊息
        """
        embed = discord.Embed(
            title=f"⛽ 油價變動預告：<{data['status']}>",
            description=f"**預計下週一汽油每公升 {data['status']}**",
            color=discord.Color.dark_grey(),
            timestamp=datetime.datetime.now()
        )

        price_text = f"92: {data['92']}  |  95: {data['95']}  |  98: {data['98']}"
        embed.add_field(
            name="今日中油油價參考",
            value=f"```\n{price_text}\n```",
            inline=False
        )

        embed.set_footer(text=f"資料來源：{data['url']}")
        await channel.send(embed=embed)

async def setup(bot):
    await bot.add_cog(GasCog(bot))
