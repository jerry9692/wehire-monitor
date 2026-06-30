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
CREATE INDEX IF NOT EXISTS idx_articles_content_hash ON articles(content_hash);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  article_id TEXT NOT NULL REFERENCES articles(id),
  company_name TEXT NOT NULL DEFAULT '',
  job_name TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT '',
  apply_channel TEXT,
  email TEXT,
  email_chars TEXT,
  deadline_date TEXT NOT NULL DEFAULT '',
  deadline_inferred INTEGER DEFAULT 0,
  confidence INTEGER DEFAULT 0,
  match_score INTEGER DEFAULT 0,
  source_evidence TEXT,
  warnings TEXT,
  notified_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
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
  model_count INTEGER DEFAULT 0,
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
CREATE INDEX IF NOT EXISTS idx_images_article ON images(article_id);
