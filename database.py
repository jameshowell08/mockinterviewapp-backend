from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
import os
from dotenv import load_dotenv

load_dotenv()

SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./mock_interviews.db")

# Fix legacy postgres:// URLs
if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

is_sqlite = SQLALCHEMY_DATABASE_URL.startswith("sqlite")

if is_sqlite:
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False}
    )
else:
    connect_args = {}

    # Optional: DNS fix for local dev only (skip on Cloud Run — it has proper DNS)
    if os.getenv("FORCE_DNS_RESOLVE") == "true":
        try:
            import urllib.parse
            import dns.resolver
            parsed_url = urllib.parse.urlparse(SQLALCHEMY_DATABASE_URL)
            if parsed_url.hostname and "pooler.supabase.com" in parsed_url.hostname:
                resolver = dns.resolver.Resolver(configure=False)
                resolver.nameservers = ["8.8.8.8"]
                ip = resolver.resolve(parsed_url.hostname, "A")[0].to_text()
                connect_args["hostaddr"] = ip
                print(f"[DB] DNS resolved {parsed_url.hostname} → {ip}")
        except Exception as e:
            print(f"[DB] DNS resolve failed, using default: {e}")

    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args=connect_args,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_recycle=300,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()