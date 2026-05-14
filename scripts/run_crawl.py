import os
import sys
from importlib import import_module

# ĐOẠN NÀY LÀ QUAN TRỌNG NHẤT:
# Nó ép Python phải nhìn vào thư mục gốc ai_job_server để thấy folder 'app'
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
sys.path.append(root_dir)

def start_crawling():
    session_module = import_module("app.db.session")
    crawler_module = import_module("app.services.crawler_service")

    get_standalone_db = getattr(session_module, "get_standalone_db")
    crawler_service = getattr(crawler_module, "CrawlerService")

    db = get_standalone_db()
    urls = [
        # DÁN LINK TOPDEV VÀO ĐÂY (phải là dạng https://topdev.vn/detail-jobs/...)
        "https://topdev.vn/detail-jobs/intern-software-engineer-cong-ty-tnhh-keizu-viet-nam-2100324?src=topdev_home&medium=superhotjobs",
    ]

    results = crawler_service.crawl_jobs(db, urls)
    print(results)
    
    db.close()
    print("--- Xong ---")

if __name__ == "__main__":
    start_crawling()