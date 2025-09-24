import os
from dotenv import load_dotenv

# 載入 .env 檔案
basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '../../.env'))

class Config:
    # 讀取環境變數
    DB_USER = os.getenv('DB_USER', 'root')
    DB_PASSWORD = os.getenv('DB_PASSWORD', 'password')
    DB_HOST = os.getenv('DB_HOST', 'localhost')
    DB_PORT = os.getenv('DB_PORT', '3306')
    DB_NAME = os.getenv('DB_NAME', 'cusdesktop')

    SQLALCHEMY_DATABASE_URI = (
        f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SECRET_KEY = os.getenv('SECRET_KEY', 'default-secret-key')

    # === 上傳相關設定 ===
    # 建議將 uploads 存在專案路徑下的 uploads 目錄
    UPLOAD_FOLDER = os.path.join(basedir, '../../uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024   # 16MB
    ALLOWED_EXTENSIONS = {'pdf', 'jpg', 'jpeg', 'png', 'doc', 'docx', 'xlsx', 'xls'}
