from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from config import DATABASE_URL, IS_SQLITE

connect_args = {"check_same_thread": False, "timeout": 30} if IS_SQLITE else {}
# 개별종목 상세 페이지 하나가 ~28개 API를 동시에 쏘는데 SQLAlchemy 기본 풀(5+10=15)이
# 이보다 작아 절반 가까이가 커넥션을 기다리며 직렬화되고 있었음(2026-08-14 실측:
# 요청당 0.2~0.6초 → 풀 경합 시 최대 7.6초). Postgres max_connections=100 대비
# 여유가 충분해 풀을 확장.
_pool_kwargs = {} if IS_SQLITE else {"pool_size": 20, "max_overflow": 20, "pool_recycle": 1800}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True, **_pool_kwargs)
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
