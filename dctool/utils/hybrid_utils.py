import os
import asyncio
import logging
import tempfile
import google.generativeai as genai

logger = logging.getLogger('hybrid_ai_utils')

async def upload_and_wait(file_path, mime_type):
    """
    專供 Gemini 使用：上傳檔案到 Gemini File API 並等待處理完成。
    支援影片與 PDF。
    """
    try:
        # 使用 asyncio.to_thread 避免阻塞 Discord 導航
        uploaded_file = await asyncio.to_thread(
            genai.upload_file, path=file_path, mime_type=mime_type
        )
        logger.info(f"檔案已上傳至雲端: {uploaded_file.name}, 狀態: {uploaded_file.state.name}")

        # 輪詢狀態直到變為 ACTIVE
        while uploaded_file.state.name == "PROCESSING":
            await asyncio.sleep(3)
            uploaded_file = await asyncio.to_thread(genai.get_file, uploaded_file.name)
        
        if uploaded_file.state.name == "ACTIVE":
            return uploaded_file
        else:
            logger.error(f"雲端檔案處理失敗: {uploaded_file.state.name}")
            return None
    except Exception as e:
        logger.error(f"upload_and_wait 發生錯誤: {e}")
        return None

async def process_attachments(attachments, mode="cloud"):
    """
    處理 Discord 附件並根據模式（local/cloud）轉換格式。
    
    Returns:
        If mode=="cloud": (list of Gemini parts, list of temp_file_paths)
        If mode=="local": (list of bytes for images, [])
    """
    temp_files = []
    
    if mode == "local":
        # --- 本地模式：只處理圖片，轉換為 bytes 列表 ---
        local_image_data = []
        for attachment in attachments:
            mime_type = attachment.content_type or ""
            if "image" in mime_type:
                img_bytes = await attachment.read()
                local_image_data.append(img_bytes)
        return local_image_data, []

    else:
        # --- 雲端模式：原有的 Gemini 處理邏輯 ---
        gemini_parts = []
        for attachment in attachments:
            mime_type = attachment.content_type or ""
            
            # 1. 處理圖片 (直接傳 bytes 給 Gemini)
            if "image" in mime_type:
                img_data = await attachment.read()
                gemini_parts.append({"mime_type": mime_type, "data": img_data})
            
            # 2. 處理影片或 PDF (透過 File API)
            elif "video" in mime_type or "application/pdf" in mime_type or attachment.filename.lower().endswith(('.mp4', '.pdf')):
                suffix = ".pdf" if "pdf" in mime_type or attachment.filename.lower().endswith('.pdf') else ".mp4"
                
                # 建立暫存檔供上傳
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    await attachment.save(tmp.name)
                    temp_files.append(tmp.name)
                    
                    upload_mime = "application/pdf" if suffix == ".pdf" else "video/mp4"
                    gemini_file = await upload_and_wait(tmp.name, upload_mime)
                    
                    if gemini_file:
                        gemini_parts.append(gemini_file)
                        
        return gemini_parts, temp_files