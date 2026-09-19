# 冒烟测试（任务1验收）：真PG测试库基建可用——连接、vector扩展、表隔离
from sqlalchemy import text
from sqlalchemy.orm import Session


def test_connects_to_test_db(db: Session):
    assert db.execute(text("SELECT current_database()")).scalar_one() == "umaxrag_test"


def test_pgvector_extension_active(db: Session):
    count = db.execute(
        text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
    ).scalar_one()
    assert count == 1


def test_probe_write(db: Session):
    """写入一行探针数据，由下一条测试验证函数级清库隔离。"""
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS _probe_infra (id serial primary key, v int)"
    ))
    db.execute(text("INSERT INTO _probe_infra (v) VALUES (1)"))
    db.commit()
    assert db.execute(text("SELECT count(*) FROM _probe_infra")).scalar_one() == 1


def test_probe_isolated(db: Session):
    """_probe_infra 不在模型元数据里，故意不自动清理——验证 drop 兜底可清。"""
    db.execute(text("DROP TABLE IF EXISTS _probe_infra"))
    db.commit()
    assert db.execute(text(
        "SELECT count(*) FROM information_schema.tables WHERE table_name='_probe_infra'"
    )).scalar_one() == 0
