-- src/wehire_monitor/modules/storage/schema.sql

CREATE TABLE IF NOT EXISTS articles (
  id TEXT PRIMARY KEY,
  account_name TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  publish_time TEXT NOT NULL,
  content_hash TEXT,
  image_hashes TEXT,
  status TEXT NOT NULL,
  prefilter_score INTEGER,
  prefilter_reasons TEXT,
  article_type TEXT,
  raw_html_path TEXT,
  markdown_path TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
CREATE INDEX IF NOT EXISTS idx_articles_publish_time ON articles(publish_time);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  article_id TEXT NOT NULL REFERENCES articles(id),
  company_name TEXT,
  job_name TEXT,
  location TEXT,
  apply_channel TEXT,
  email TEXT,
  deadline_date TEXT,
  deadline_inferred INTEGER,
  confidence INTEGER,
  match_score INTEGER,
  source_evidence TEXT,
  warnings TEXT,
  notified_at TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(company_name, job_name, location, deadline_date)
);
CREATE INDEX IF NOT EXISTS idx_jobs_article ON jobs(article_id);
CREATE INDEX IF NOT EXISTS idx_jobs_match_score ON jobs(match_score);

CREATE TABLE IF NOT EXISTS run_logs (
  run_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  fetched_count INTEGER DEFAULT 0,
  candidate_count INTEGER DEFAULT 0,
  ocr_count INTEGER DEFAULT 0,
  llm_count INTEGER DEFAULT 0,
  vlm_count INTEGER DEFAULT 0,
  cost_estimate REAL DEFAULT 0,
  error_summary TEXT
);

CREATE TABLE IF NOT EXISTS images (
  id TEXT PRIMARY KEY,
  article_id TEXT NOT NULL REFERENCES articles(id),
  index_in_article INTEGER,
  url TEXT,
  local_path TEXT,
  width INTEGER,
  height INTEGER,
  status TEXT,
  created_at TEXT NOT NULL
);
