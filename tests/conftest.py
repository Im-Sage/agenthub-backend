import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.broadcaster import broadcaster
from app.core.rate_limit import rate_limiter
from app.main import app
from app.db.session import get_db
from app.models.user import Base
from app.workers import agent_tasks


# 使用内存 SQLite 进行测试
SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"

from sqlalchemy import create_engine
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)
    
    yield session
    
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session):
    rate_limiter.clear()
    class DummyAsyncResult:
        id = "test-celery-task-id"

    async def noop_subscribe(*args, **kwargs):
        return None

    async def noop_stop(*args, **kwargs):
        return None

    async def noop_publish(*args, **kwargs):
        return None

    original_subscribe = broadcaster.subscribe
    original_stop = broadcaster.stop
    original_publish = broadcaster.publish
    original_run_agent_delay = agent_tasks.run_agent_task.delay
    original_run_orchestrator_delay = agent_tasks.run_orchestrator_task.delay
    original_resume_orchestrator_delay = agent_tasks.resume_orchestrator_task.delay
    broadcaster.subscribe = noop_subscribe
    broadcaster.stop = noop_stop
    broadcaster.publish = noop_publish
    agent_tasks.run_agent_task.delay = lambda *args, **kwargs: DummyAsyncResult()
    agent_tasks.run_orchestrator_task.delay = lambda *args, **kwargs: DummyAsyncResult()
    agent_tasks.resume_orchestrator_task.delay = lambda *args, **kwargs: DummyAsyncResult()

    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    
    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        rate_limiter.clear()
        app.dependency_overrides.clear()
        broadcaster.subscribe = original_subscribe
        broadcaster.stop = original_stop
        broadcaster.publish = original_publish
        agent_tasks.run_agent_task.delay = original_run_agent_delay
        agent_tasks.run_orchestrator_task.delay = original_run_orchestrator_delay
        agent_tasks.resume_orchestrator_task.delay = original_resume_orchestrator_delay
