import os
import urllib.parse
import dns.resolver
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()
db_url = os.getenv("DATABASE_URL")
if not db_url:
    print("NO DATABASE_URL")
    exit(1)

parsed = urllib.parse.urlparse(db_url)
hostname = parsed.hostname
print(f"Resolving {hostname}...")

resolver = dns.resolver.Resolver(configure=False)
resolver.nameservers = ['8.8.8.8']
try:
    answer = resolver.resolve(hostname, 'A')
    ip_address = answer[0].to_text()
    print(f"Resolved to {ip_address}")
except Exception as e:
    print(f"Failed to resolve: {e}")
    exit(1)

db_url = db_url.replace("postgres://", "postgresql://", 1)

try:
    engine = create_engine(db_url, connect_args={"hostaddr": ip_address}, pool_pre_ping=True)
    with engine.connect() as conn:
        print("SUCCESSFULLY CONNECTED USING HOSTADDR!")
except Exception as e:
    print(f"FAILED TO CONNECT: {e}")
