from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from config import DATABASE_URL

# SQLite connect_args is needed for multiple threads
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        # [버그 수정] 예외 발생 시 명시적 롤백으로 트랜잭션 안전하게 정리
        db.rollback()
        raise
    finally:
        db.close()
