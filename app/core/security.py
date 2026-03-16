import logging
import os
import traceback
from time import perf_counter

import firebase_admin
from fastapi import HTTPException
from firebase_admin import auth, credentials

from app.core.config import settings

logger = logging.getLogger(__name__)


def init_firebase():
    try:
        firebase_admin.get_app()
        return
    except ValueError:
        pass

    logger.info(
        "firebase.init start service_account_path=%s absolute_path=%s",
        settings.FIREBASE_SERVICE_ACCOUNT_JSON,
        os.path.abspath(settings.FIREBASE_SERVICE_ACCOUNT_JSON),
    )
    cred = credentials.Certificate(settings.FIREBASE_SERVICE_ACCOUNT_JSON)
    logger.info("firebase.init loaded project_id=%s", cred.project_id)
    firebase_admin.initialize_app(cred)
    logger.info("firebase.init complete")


def verify_firebase_token(id_token: str):
    verify_started_at = perf_counter()
    logger.info("firebase.verify start token_length=%s", len(id_token))
    init_firebase()
    try:
        decoded = auth.verify_id_token(id_token, check_revoked=False)
        logger.info(
            "firebase.verify success uid=%s elapsed_ms=%.2f",
            decoded.get("uid"),
            (perf_counter() - verify_started_at) * 1000,
        )
        return decoded
    except Exception as exc:
        logger.error("firebase.verify failed type=%s message=%s", type(exc).__name__, str(exc))
        traceback.print_exc()
        raise HTTPException(
            status_code=401,
            detail=f"Firebase verify failed: {type(exc).__name__}: {exc}",
        ) from exc
