import os
import aiohttp
import logging
import discord
import feedparser
from discord.ext import commands, tasks
from dotenv import load_dotenv

# 載入環境變數
load_dotenv()

logger = logging.getLogger('notifier_cog')

class NotifierCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        # 從 .env 讀取設定
        self.twitch_client_id = os.getenv("TWITCH_CLIENT_ID")
        self.twitch_client_secret = os.getenv("TWITCH_CLIENT_SECRET")
        self.twitch_target_user = os.getenv("TWITCH_TARGET_USER")
        self.yt_channel_id = os.getenv("YT_CHANNEL_ID")
        self.notify_channel_id = int(os.getenv("DISCORD_CHANNEL_ID", 0))

        # 狀態暫存
        self.twitch_access_token = None
        self.is_live = False
        self.last_video_id = None
        
        # 啟動背景檢查任務 (預設每 5 分鐘檢查一次)
        self.check_updates.start()

    def cog_unload(self):
        self.check_updates.cancel()

    async def get_twitch_token(self):
        """獲取 Twitch OAuth App Access Token"""
        url = "https://id.twitch.tv/oauth2/token"
        params = {
            "client_id": self.twitch_client_id,
            "client_secret": self.twitch_client_secret,
            "grant_type": "client_credentials"
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self.twitch_access_token = data.get("access_token")
                        logger.info("成功獲取新的 Twitch Access Token")
                    else:
                        logger.error(f"無法獲取 Twitch Token: {resp.status}")
        except Exception as e:
            logger.error(f"Twitch Token 請求發生錯誤: {e}")

    async def check_twitch(self, channel):
        """檢查 Twitch 直播狀態"""
        if not self.twitch_access_token:
            await self.get_twitch_token()

        url = f"https://api.twitch.tv/helix/streams?user_login={self.twitch_target_user}"
        headers = {
            "Client-ID": self.twitch_client_id,
            "Authorization": f"Bearer {self.twitch_access_token}"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 401:  # Token 過期
                        await self.get_twitch_token()
                        return
                    
                    data = await resp.json()
                    streams = data.get("data", [])

                    if streams:
                        if not self.is_live:
                            stream = streams[0]
                            title = stream.get("title", "無標題")
                            game = stream.get("game_name", "未知遊戲")
                            
                            embed = discord.Embed(
                                title=f"🔴 {self.twitch_target_user} 開台啦！",
                                description=f"**標題：** {title}\n**正在玩：** {game}",
                                color=0x6441a5,
                                url=f"https://www.twitch.tv/{self.twitch_target_user}"
                            )
                            embed.set_thumbnail(url=stream.get("thumbnail_url").replace("{width}", "400").replace("{height}", "225"))
                            await channel.send(embed=embed)
                            self.is_live = True
                    else:
                        self.is_live = False
        except Exception as e:
            logger.error(f"檢查 Twitch 狀態時發生錯誤: {e}")

    async def check_youtube(self, channel):
        """透過 RSS 檢查 YouTube 影片更新"""
        rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={self.yt_channel_id}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(rss_url) as resp:
                    if resp.status == 200:
                        content = await resp.text()
                        feed = feedparser.parse(content)
                        
                        if not feed.entries:
                            return

                        latest_video = feed.entries[0]
                        video_id = latest_video.yt_videoid
                        video_title = latest_video.title
                        video_link = latest_video.link

                        # 第一次執行時，記錄目前的 video_id 但不通知
                        if self.last_video_id is None:
                            self.last_video_id = video_id
                            logger.info(f"YouTube 監控初始化，最新影片 ID: {video_id}")
                            return

                        if video_id != self.last_video_id:
                            self.last_video_id = video_id
                            await channel.send(f"🎥 **YouTube 上片通知！**\n【{video_title}】\n傳送門：{video_link}")
        except Exception as e:
            logger.error(f"檢查 YouTube 更新時發生錯誤: {e}")

    @tasks.loop(minutes=5)
    async def check_updates(self):
        """背景循環檢查邏輯"""
        await self.bot.wait_until_ready()
        
        channel = self.bot.get_channel(self.notify_channel_id)
        if not channel:
            logger.warning(f"找不到通知頻道 ID: {self.notify_channel_id}")
            return

        await self.check_twitch(channel)
        await self.check_youtube(channel)

async def setup(bot):
    await bot.add_cog(NotifierCog(bot))