from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt as pyjwt
from app.config import get_settings

security = HTTPBearer()


def verify_token(token: str) -> dict:
    settings = get_settings()
    try:
        header = pyjwt.get_unverified_header(token)
        alg = header.get("alg", "HS256")

        if alg == "ES256":
            import httpx
            jwks_url = f"{settings.supabase_url}/auth/v1/.well-known/jwks.json"
            response = httpx.get(jwks_url)
            jwks = response.json()
            kid = header.get("kid")
            key_data = next((k for k in jwks["keys"] if k["kid"] == kid), None)
            if not key_data:
                raise HTTPException(status_code=401, detail="Signing key not found")
            public_key = pyjwt.algorithms.ECAlgorithm.from_jwk(key_data)
            payload = pyjwt.decode(
                token,
                public_key,
                algorithms=["ES256"],
                options={"verify_aud": False},
            )
        else:
            payload = pyjwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )

        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Token missing subject")

        return {
            "user_id": user_id,
            "email": payload.get("email", ""),
            "role": payload.get("role", "authenticated"),
        }

    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except pyjwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    return verify_token(credentials.credentials)


def get_user_org(user_id: str, db) -> str:
    result = (
        db.table("profiles")
        .select("org_id")
        .eq("id", user_id)
        .execute()
    )
    if not result.data or not result.data[0].get("org_id"):
        raise HTTPException(
            status_code=400,
            detail="Organisation not found. Please complete onboarding.",
        )
    return result.data[0]["org_id"]
