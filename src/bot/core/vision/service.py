"""兼容层：视觉服务位于 ``vision.service``。"""
from vision.service import VisionService, download_images_as_data_urls

__all__ = ["VisionService", "download_images_as_data_urls"]
