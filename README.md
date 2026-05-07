# NextStep AI Job Matching Server

AI server cho hệ thống NextStep, phụ trách crawl job, trích xuất CV, so sánh CV với job, phân tích skill gap, tạo roadmap học tập và đưa ra nhận xét bằng AI.

## Tính Năng Nổi Bật

- **Crawl job từ TopDev**: lấy title, công ty, địa điểm, mức lương, level, kinh nghiệm, deadline, mô tả job và danh sách skill.
- **Làm sạch dữ liệu job**: tách HTML/script/entity, chuẩn hóa khoảng trắng, lưu cả `description_raw` và `description_clean`.
- **Chuẩn hóa skill và alias**: nhận diện các cách viết tắt/tên gọi tương đương như `TS -> TypeScript`, `JS -> JavaScript`, `k8s -> Kubernetes`, `postgres -> PostgreSQL`, `asp.net -> .NET`.
- **Lọc role label không phải skill**: các cụm như `Full-Stack`, `Backend`, `Frontend`, `Developer`, `Engineer` không bị đưa vào skill gap/roadmap.
- **Phân tích CV**: đọc CV từ text hoặc file PDF/DOCX/TXT, trích xuất level, số năm kinh nghiệm, địa điểm, chứng chỉ và skill.
- **So sánh CV với job**: tính điểm match, skill match, keyword match, title/level match và ATS readability.
- **Gap analysis**: phân loại skill thiếu, skill yếu, gap về kinh nghiệm, level và chứng chỉ.
- **Roadmap học tập**: tạo các phase học tập, ước tính số tuần, mức độ khó, thời gian hoàn thành và tài nguyên học.
- **AI review**: dùng Gemini để nhận xét CV với job, đưa ra điểm mạnh, rủi ro, gợi ý cải thiện và verdict. Nếu AI lỗi, server trả fallback review dựa trên score/gap/roadmap.
- **Lưu lịch sử phân tích**: lưu kết quả vào `cv_analysis_results`, `cv_skills`, `skill_gaps` để xem lại.
- **Embedding job**: đồng bộ embedding cho job để phục vụ tìm kiếm/gợi ý về sau.

## Công Nghệ Sử Dụng

- FastAPI
- SQLAlchemy + Alembic
- PostgreSQL/Supabase
- Google Gemini API
- PyMuPDF cho PDF
- BeautifulSoup + Selenium cho crawler
- pgvector cho embedding

## Cấu Trúc Chính

```text
ai_job_server/
  app/
    api/v1/endpoints/      # REST API endpoints
    services/              # Xử lý AI, crawl, matching, roadmap
    models/                # SQLAlchemy models
    schemas/               # Pydantic schemas
    data/                  # Baseline data, skill relation groups
  migrations/versions/     # Alembic migrations
  scripts/                 # Script crawl, seed, cleanup, validation
  requirements.txt
  README.md
```

## Cài Đặt

### 1. Tạo và kích hoạt virtual environment

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 2. Cài dependencies

```powershell
pip install -r requirements.txt
```

`python-multipart` bắt buộc nếu dùng endpoint upload file CV `/api/v1/cv/ingest-file`.

### 3. Tạo file môi trường

```powershell
Copy-Item .env.example .env
```

Điền các biến quan trọng trong `.env`:

```env
DATABASE_URL=postgresql://postgres.<project-ref>:<password>@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres?sslmode=require
GEMINI_API_KEY=your_gemini_api_key
CV_AI_ENRICHMENT_ENABLED=True
JWT_ACCESS_SECRET=replace_me_access_secret
JWT_ACCESS_EXPIRES_IN=1h
```

Nếu dùng Supabase, nên dùng pooler host/port:

```env
DB_HOST=aws-1-ap-northeast-1.pooler.supabase.com
DB_PORT=6543
DB_NAME=postgres
```

### 4. Chạy migration

```powershell
alembic upgrade head
```

Server cũng có cơ chế tạo bổ sung một số bảng/cột cần thiết khi chạy, nhưng nên chạy migration để DB đồng bộ rõ ràng.

## Chạy Server

```powershell
uvicorn app.main:app --reload --port 9001
```

Mở Swagger UI:

```text
http://127.0.0.1:9001/docs
```

## Hướng Dẫn Sử Dụng API

### 1. Crawl job TopDev

Swagger group: **Jobs**

- `POST /api/v1/jobs/crawl`: crawl 1 job URL.
- `POST /api/v1/jobs/crawl-batch`: crawl nhiều job URL.
- `GET /api/v1/jobs/`: xem danh sách job đã crawl.
- `GET /api/v1/jobs/{job_id}`: xem chi tiết job.
- `GET /api/v1/jobs/{job_id}/skills`: xem skill của job.

Ví dụ crawl batch:

```json
{
  "urls": [
    "https://topdev.vn/detail-jobs/example-job-123"
  ]
}
```

### 2. So sánh CV với job bằng text

Swagger group: **CV**

Endpoint:

```text
POST /api/v1/cv/ingest
```

Body:

```json
{
  "cv_text": "Paste CV text here...",
  "job_id": 4,
  "timeframe_weeks": 0,
  "max_skills_per_phase": 4
}
```

Kết quả trả về gồm:

- `extracted_profile`: hồ sơ CV đã trích xuất.
- `job_context`: thông tin job.
- `job_match`: điểm match và skill match/missing.
- `gap_analysis`: skill thiếu, skill yếu, gap level/experience.
- `roadmap`: lộ trình học tập và thời gian ước tính.
- `ai_review`: nhận xét AI/fallback về CV với job.

### 3. So sánh CV với job bằng file PDF/DOCX/TXT

Swagger group: **CV**

Endpoint:

```text
POST /api/v1/cv/ingest-file
```

Form data:

- `cv_file`: file CV PDF/DOCX/TXT.
- `job_id`: id của job đã crawl.
- `job_url`: có thể để trống nếu đã có `job_id`.
- `timeframe_weeks`: `0` nếu không giới hạn thời gian.
- `max_skills_per_phase`: 1 đến 5.

### 4. Xem lại lịch sử phân tích

- `GET /api/v1/cv/analysis-results`: danh sách analysis đã lưu.
- `GET /api/v1/cv/analysis-results/{analysis_id}`: xem lại kết quả đầy đủ, gồm cả `ai_review`.

### 5. API tính riêng từng phần

Swagger group: **Analyzer**

- `POST /api/v1/analyzer/job-match`: chỉ tính điểm match.
- `POST /api/v1/analyzer/gap-analysis`: chỉ tính gap analysis.

Swagger group: **Roadmap**

- `POST /api/v1/roadmap/generate`: tạo roadmap từ danh sách missing/weak skills.

## Scripts Quan Trọng

### Seed skill aliases, skill nền tảng và course baseline

```powershell
python -B scripts\seed_skill_foundation.py
```

Script này thêm/cập nhật aliases như `ts`, `js`, `k8s`, `postgres`, `mssql`, đồng bộ importance tier và course duration.

### Kiểm tra pipeline skill

```powershell
python -B scripts\validate_skill_pipeline.py
```

Kiểm tra alias mapping, relation groups và course duration.

### Dọn role label không phải skill

```powershell
python -B scripts\cleanup_non_skill_role_labels.py
```

Dùng khi DB cũ đã có các skill sai như `Full-Stack`, `Backend`, `Frontend`. Script sẽ xóa liên kết trong `job_skills` và deactivate skill label đó.

### Làm sạch lại job description cũ

```powershell
python -B scripts\backfill_clean_job_descriptions.py
```

Cập nhật lại `description_clean` cho các job đã crawl trước khi có logic clean mới.

### Crawl job mẫu

```powershell
python scripts\run_crawl.py
```

Sửa URL trong script trước khi chạy nếu cần crawl job khác.

### Đồng bộ embedding cho job

```powershell
python -c "from app.db.session import get_standalone_db; from app.services.embedding_service import EmbeddingService; db=get_standalone_db(); print(EmbeddingService.sync_job_embeddings(db, limit=10, only_missing=True)); db.close()"
```

## Dữ Liệu Chính Trong DB

- `jobs`: job đã crawl.
- `companies`: công ty.
- `skills`: skill canonical và aliases.
- `job_skills`: skill yêu cầu của job.
- `skill_courses`: tài nguyên học và duration.
- `cv_analysis_results`: kết quả phân tích CV-job.
- `cv_skills`: skill trích từ CV.
- `skill_gaps`: gap đã lưu theo analysis.
- `entity_embeddings`: vector embedding.

## Lỗi Thường Gặp

### 1. Không kết nối được Supabase

Lỗi mẫu:

```text
could not translate host name "aws-1-ap-northeast-1.pooler.supabase.com" to address
```

Kiểm tra DNS:

```powershell
nslookup aws-1-ap-northeast-1.pooler.supabase.com
```

Kiểm tra Python resolve host:

```powershell
python -c "import socket; print(socket.getaddrinfo('aws-1-ap-northeast-1.pooler.supabase.com', 5432))"
```

Nếu DNS đã ổn, restart server FastAPI.

### 2. Upload CV bị lỗi multipart

```text
Form data requires "python-multipart" to be installed
```

Chạy:

```powershell
pip install -r requirements.txt
```

### 3. AI review trả `source: fallback`

Nghĩa là Gemini không chạy được hoặc bị tắt, server đã tạo nhận xét fallback từ score/gap/roadmap.

Kiểm tra:

```env
GEMINI_API_KEY=...
CV_AI_ENRICHMENT_ENABLED=True
```

### 4. Job vẫn hiện `Full-Stack` như skill

Chạy:

```powershell
python -B scripts\cleanup_non_skill_role_labels.py
```

Sau đó phân tích lại CV-job. Kết quả analysis cũ sẽ không tự cập nhật.

## Ghi Chú

- AI server đọc `.env` trong thư mục `ai_job_server`.
- Các script ghi trực tiếp vào database qua `DATABASE_URL`.
- Khi thay đổi aliases, cleanup hoặc migration, nên restart server để đảm bảo code và DB cùng trạng thái.
