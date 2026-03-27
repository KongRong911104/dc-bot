import os
import logging
import aiohttp
import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
# 載入環境變數
load_dotenv()
TAIWAN_TZ = ZoneInfo('Asia/Taipei')
WEATHER_API = os.getenv("WEATHER_API")
# 設定日誌 (繁體中文)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger('discord_bot')
# ==========================================
# 工具函式：獲取天氣資料 
# ==========================================
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
