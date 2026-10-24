
import os
import psycopg

url = os.environ.get("DATABASE_URL")

if not url:
    raise SystemExit("DATABASE_URL is not set")

with psycopg.connect(url, connect_timeout=10) as conn:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT current_database(), current_user, version()"
        )
        database, user, version = cur.fetchone()

print("Neon connection successful")
print("Database:", database)
print("User:", user)
print("PostgreSQL:", version.split(",")[0])
