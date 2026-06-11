from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.core.config import settings

# 1. Tạo Engine với cấu hình tối ưu cho PostgreSQL
# pool_pre_ping=True giúp tự động kiểm tra lại kết nối nếu database bị ngắt đột ngột
engine = create_engine(
    settings.DATABASE_URL, 
    pool_pre_ping=True,
    pool_recycle=300,
    echo=False  # Đổi thành True nếu bạn muốn xem các câu lệnh SQL cào dữ liệu hiện dưới Terminal
)

# 2. Tạo SessionLocal để quản lý các phiên làm việc
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 3. Hàm hỗ trợ FastAPI quản lý đóng/mở DB (Dependency Injection)
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 4. Bổ sung: Hàm dành riêng cho Script Cào dữ liệu (Standalone Script)
# Vì khi cào dữ liệu bạn thường chạy script độc lập, không qua FastAPI request
def get_standalone_db():
    return SessionLocal()
