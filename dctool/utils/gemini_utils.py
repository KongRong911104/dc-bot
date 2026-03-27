import os
import asyncio
import logging
import google.generativeai as genai

logger = logging.getLogger('gemini_utils')

async def upload_and_wait(file_path, mime_type):
    """
    Upload file to Gemini File API and wait for it to be processed (ACTIVE).
    Supports Video and PDF.
    """
    try:
        # 非阻塞上傳
        uploaded_file = await asyncio.to_thread(
            genai.upload_file, path=file_path, mime_type=mime_type
        )
        logger.info(f"File uploaded: {uploaded_file.name}, State: {uploaded_file.state.name}")

        # 輪詢狀態
        while uploaded_file.state.name == "PROCESSING":
            await asyncio.sleep(3)
            uploaded_file = await asyncio.to_thread(genai.get_file, uploaded_file.name)
        
        if uploaded_file.state.name == "ACTIVE":
            return uploaded_file
        else:
            logger.error(f"File processing failed: {uploaded_file.state.name}")
            return None
    except Exception as e:
        logger.error(f"Error in upload_and_wait: {e}")
        return None

async def process_attachments(attachments):
    """
    Process Discord attachments and convert them to Gemini content parts.
    Returns: (list of parts, list of temp_file_paths)
    """
    parts = []
    temp_files = []
    
    for attachment in attachments:
        mime_type = attachment.content_type or ""
        
        # 1. 處理圖片 (Image)
        if "image" in mime_type:
            img_data = await attachment.read()
            parts.append({"mime_type": mime_type, "data": img_data})
        
        # 2. 處理影片或 PDF (Video or PDF via File API)
        elif "video" in mime_type or "application/pdf" in mime_type or attachment.filename.lower().endswith(('.mp4', '.pdf')):
            suffix = ".pdf" if "pdf" in mime_type or attachment.filename.lower().endswith('.pdf') else ".mp4"
            
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                await attachment.save(tmp.name)
                temp_files.append(tmp.name)
                
                # 決定上傳的 mime_type
                upload_mime = "application/pdf" if suffix == ".pdf" else "video/mp4"
                gemini_file = await upload_and_wait(tmp.name, upload_mime)
                if gemini_file:
                    parts.append(gemini_file)
                    
    return parts, temp_files