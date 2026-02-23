import os
import firebase_admin
from firebase_admin import credentials, auth
from fastapi import HTTPException
import traceback

from app.core.config import settings


def init_firebase():
    try:
        firebase_admin.get_app()
        return
    except ValueError:
        pass

    # 🔎 DEBUG: show exactly what file is being used
    print("🔥 FIREBASE_SERVICE_ACCOUNT_JSON setting =", settings.FIREBASE_SERVICE_ACCOUNT_JSON)
    print("🔥 Absolute path =", os.path.abspath(settings.FIREBASE_SERVICE_ACCOUNT_JSON))

    cred = credentials.Certificate(settings.FIREBASE_SERVICE_ACCOUNT_JSON)

    # 🔎 DEBUG: confirm which project this service account belongs to
    print("🔥 Loaded service account project_id =", cred.project_id)

    firebase_admin.initialize_app(cred)


def verify_firebase_token(id_token: str):
    init_firebase()
    try:
        return auth.verify_id_token(id_token, check_revoked=False)
    except Exception as e:
        print("🔥 FIREBASE TOKEN VERIFY FAILED")
        print("Type:", type(e))
        print("Message:", str(e))
        traceback.print_exc()
        raise HTTPException(
            status_code=401,
            detail=f"Firebase verify failed: {type(e).__name__}: {e}",
        )