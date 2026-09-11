"""Opt-in synthetic dataset. Refuses to populate a nonempty customer database."""
import json
from database import SessionLocal
from main import seed_database

if __name__ == "__main__":
    with SessionLocal() as db:
        print(json.dumps(seed_database(db), ensure_ascii=False, indent=2))
