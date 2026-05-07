# NextStep AI Job Matching Server

AI server cho he thong NextStep, phu trach crawl job, trich xuat CV, so sanh CV voi job, phan tich skill gap, tao roadmap hoc tap va dua ra nhan xet bang AI.

## Tinh Nang Noi Bat

- **Crawl job tu TopDev**: lay title, cong ty, dia diem, muc luong, level, kinh nghiem, deadline, mo ta job va danh sach skill.
- **Lam sach du lieu job**: tach HTML/script/entity, chuan hoa khoang trang, luu ca `description_raw` va `description_clean`.
- **Chuan hoa skill va alias**: nhan dien cac cach viet tat/ten goi tuong duong nhu `TS -> TypeScript`, `JS -> JavaScript`, `k8s -> Kubernetes`, `postgres -> PostgreSQL`, `asp.net -> .NET`.
- **Loc role label khong phai skill**: cac cum nhu `Full-Stack`, `Backend`, `Frontend`, `Developer`, `Engineer` khong bi dua vao skill gap/roadmap.
- **Phan tich CV**: doc CV tu text hoac file PDF/DOCX/TXT, trich xuat level, so nam kinh nghiem, dia diem, chung chi va skill.
- **So sanh CV voi job**: tinh diem match, skill match, keyword match, title/level match va ATS readability.
- **Gap analysis**: phan loai skill thieu, skill yeu, gap ve kinh nghiem, level va chung chi.
- **Roadmap hoc tap**: tao cac phase hoc tap, uoc tinh so tuan, muc do kho, thoi gian hoan thanh va tai nguyen hoc.
- **AI review**: dung Gemini de nhan xet CV voi job, dua ra diem manh, rui ro, goi y cai thien va verdict. Neu AI loi, server tra fallback review dua tren score/gap/roadmap.
- **Luu lich su phan tich**: luu ket qua vao `cv_analysis_results`, `cv_skills`, `skill_gaps` de xem lai.
- **Embedding job**: dong bo embedding cho job de phuc vu tim kiem/goi y ve sau.

## Cong Nghe Su Dung

- FastAPI
- SQLAlchemy + Alembic
- PostgreSQL/Supabase
- Google Gemini API
- PyMuPDF cho PDF
- BeautifulSoup + Selenium cho crawler
- pgvector cho embedding

## Cau Truc Chinh

```text
ai_job_server/
  app/
    api/v1/endpoints/      # REST API endpoints
    services/              # Xu ly AI, crawl, matching, roadmap
    models/                # SQLAlchemy models
    schemas/               # Pydantic schemas
    data/                  # Baseline data, skill relation groups
  migrations/versions/     # Alembic migrations
  scripts/                 # Script crawl, seed, cleanup, validation
  requirements.txt
  README.md
```

## Cai Dat

### 1. Tao va kich hoat virtual environment

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 2. Cai dependencies

```powershell
pip install -r requirements.txt
```

`python-multipart` bat buoc neu dung endpoint upload file CV `/api/v1/cv/ingest-file`.

### 3. Tao file moi truong

```powershell
Copy-Item .env.example .env
```

Dien cac bien quan trong trong `.env`:

```env
DATABASE_URL=postgresql://postgres.<project-ref>:<password>@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres?sslmode=require
GEMINI_API_KEY=your_gemini_api_key
CV_AI_ENRICHMENT_ENABLED=True
JWT_ACCESS_SECRET=replace_me_access_secret
JWT_ACCESS_EXPIRES_IN=1h
```

Neu dung Supabase, nen dung pooler host/port:

```env
DB_HOST=aws-1-ap-northeast-1.pooler.supabase.com
DB_PORT=6543
DB_NAME=postgres
```

### 4. Chay migration

```powershell
alembic upgrade head
```

Server cung co co che tao bo sung mot so bang/cot can thiet khi chay, nhung nen chay migration de DB dong bo ro rang.

## Chay Server

```powershell
uvicorn app.main:app --reload --port 9001
```

Mo Swagger UI:

```text
http://127.0.0.1:9001/docs
```

## Huong Dan Su Dung API

### 1. Crawl job TopDev

Swagger group: **Jobs**

- `POST /api/v1/jobs/crawl`: crawl 1 job URL.
- `POST /api/v1/jobs/crawl-batch`: crawl nhieu job URL.
- `GET /api/v1/jobs/`: xem danh sach job da crawl.
- `GET /api/v1/jobs/{job_id}`: xem chi tiet job.
- `GET /api/v1/jobs/{job_id}/skills`: xem skill cua job.

Vi du crawl batch:

```json
{
  "urls": [
    "https://topdev.vn/detail-jobs/example-job-123"
  ]
}
```

### 2. So sanh CV voi job bang text

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

Ket qua tra ve gom:

- `extracted_profile`: ho so CV da trich xuat.
- `job_context`: thong tin job.
- `job_match`: diem match va skill match/missing.
- `gap_analysis`: skill thieu, skill yeu, gap level/experience.
- `roadmap`: lo trinh hoc tap va thoi gian uoc tinh.
- `ai_review`: nhan xet AI/fallback ve CV voi job.

### 3. So sanh CV voi job bang file PDF/DOCX/TXT

Swagger group: **CV**

Endpoint:

```text
POST /api/v1/cv/ingest-file
```

Form data:

- `cv_file`: file CV PDF/DOCX/TXT.
- `job_id`: id cua job da crawl.
- `job_url`: co the de trong neu da co `job_id`.
- `timeframe_weeks`: `0` neu khong gioi han thoi gian.
- `max_skills_per_phase`: 1 den 5.

### 4. Xem lai lich su phan tich

- `GET /api/v1/cv/analysis-results`: danh sach analysis da luu.
- `GET /api/v1/cv/analysis-results/{analysis_id}`: xem lai ket qua day du, gom ca `ai_review`.

### 5. API tinh rieng tung phan

Swagger group: **Analyzer**

- `POST /api/v1/analyzer/job-match`: chi tinh diem match.
- `POST /api/v1/analyzer/gap-analysis`: chi tinh gap analysis.

Swagger group: **Roadmap**

- `POST /api/v1/roadmap/generate`: tao roadmap tu danh sach missing/weak skills.

## Scripts Quan Trong

### Seed skill aliases, skill nen tang va course baseline

```powershell
python -B scripts\seed_skill_foundation.py
```

Script nay them/cap nhat aliases nhu `ts`, `js`, `k8s`, `postgres`, `mssql`, dong bo importance tier va course duration.

### Kiem tra pipeline skill

```powershell
python -B scripts\validate_skill_pipeline.py
```

Kiem tra alias mapping, relation groups va course duration.

### Don role label khong phai skill

```powershell
python -B scripts\cleanup_non_skill_role_labels.py
```

Dung khi DB cu da co cac skill sai nhu `Full-Stack`, `Backend`, `Frontend`. Script se xoa lien ket trong `job_skills` va deactivate skill label do.

### Lam sach lai job description cu

```powershell
python -B scripts\backfill_clean_job_descriptions.py
```

Cap nhat lai `description_clean` cho cac job da crawl truoc khi co logic clean moi.

### Crawl job mau

```powershell
python scripts\run_crawl.py
```

Sua URL trong script truoc khi chay neu can crawl job khac.

### Dong bo embedding cho job

```powershell
python -c "from app.db.session import get_standalone_db; from app.services.embedding_service import EmbeddingService; db=get_standalone_db(); print(EmbeddingService.sync_job_embeddings(db, limit=10, only_missing=True)); db.close()"
```

## Du Lieu Chinh Trong DB

- `jobs`: job da crawl.
- `companies`: cong ty.
- `skills`: skill canonical va aliases.
- `job_skills`: skill yeu cau cua job.
- `skill_courses`: tai nguyen hoc va duration.
- `cv_analysis_results`: ket qua phan tich CV-job.
- `cv_skills`: skill trich tu CV.
- `skill_gaps`: gap da luu theo analysis.
- `entity_embeddings`: vector embedding.

## Loi Thuong Gap

### 1. Khong ket noi duoc Supabase

Loi mau:

```text
could not translate host name "aws-1-ap-northeast-1.pooler.supabase.com" to address
```

Kiem tra DNS:

```powershell
nslookup aws-1-ap-northeast-1.pooler.supabase.com
```

Kiem tra Python resolve host:

```powershell
python -c "import socket; print(socket.getaddrinfo('aws-1-ap-northeast-1.pooler.supabase.com', 5432))"
```

Neu DNS da on, restart server FastAPI.

### 2. Upload CV bi loi multipart

```text
Form data requires "python-multipart" to be installed
```

Chay:

```powershell
pip install -r requirements.txt
```

### 3. AI review tra `source: fallback`

Nghia la Gemini khong chay duoc hoac bi tat, server da tao nhan xet fallback tu score/gap/roadmap.

Kiem tra:

```env
GEMINI_API_KEY=...
CV_AI_ENRICHMENT_ENABLED=True
```

### 4. Job van hien `Full-Stack` nhu skill

Chay:

```powershell
python -B scripts\cleanup_non_skill_role_labels.py
```

Sau do phan tich lai CV-job. Ket qua analysis cu se khong tu cap nhat.

## Ghi Chu

- AI server doc `.env` trong thu muc `ai_job_server`.
- Cac script ghi truc tiep vao database qua `DATABASE_URL`.
- Khi thay doi aliases, cleanup hoac migration, nen restart server de dam bao code va DB cung trang thai.
