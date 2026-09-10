import os

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://convoscore:convoscore@localhost:5432/convoscore"
)
