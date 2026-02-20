import firebase_admin
from firebase_admin import credentials, auth

from app.core.config import settings


def init_firebase():
    # If already initialised (common during reload), reuse it
    try:
        firebase_admin.get_app()
        return
    except ValueError:
        pass

    cred = credentials.Certificate(settings.FIREBASE_SERVICE_ACCOUNT_JSON)
    firebase_admin.initialize_app(cred)


def verify_firebase_token(id_token: str):
    init_firebase()
    return auth.verify_id_token(id_token)
