from tools import db


def init_db():
    from .models.schedule import Schedule
    db.get_shared_metadata().create_all(bind=db.engine)

