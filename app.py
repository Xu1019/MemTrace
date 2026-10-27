from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer
)
from pydantic import BaseModel, Field

from memory_db import init_db, add_message_batch, RequestConflictError
from retrieval import search_memories
from retrieval_config import get_mode

import os
import secrets


app = FastAPI()

init_db()


memory_api_key = os.getenv("MEMORY_API_KEY")

if not memory_api_key:
    raise RuntimeError("请设置 MEMORY_API_KEY")


security = HTTPBearer(auto_error=False)


def verify_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(security)
):
    if credentials is None or not secrets.compare_digest(
        credentials.credentials.encode("utf-8"),
        memory_api_key.encode("utf-8")
    ):
        raise HTTPException(
            status_code=401,
            detail="访问凭证缺失或不正确",
            headers={"WWW-Authenticate": "Bearer"}
        )



class MessageInput(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)
    timestamp: int | None = None


class AddRequest(BaseModel):
    request_id: str = Field(min_length=1)
    messages: list[MessageInput] = Field(min_length=1)
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    options: list[str] | None = None
    user_id: str = Field(min_length=1)
    top_k: int = Field(ge=1, le=100)


@app.get("/health")
def health_check():
    return {"status": "ok", "retrieval_mode": get_mode()}


@app.post("/add", dependencies=[Depends(verify_api_key)])
def add_endpoint(request: AddRequest):
    messages = [
        message.model_dump()
        for message in request.messages
    ]

    try:
        add_message_batch(request.request_id, request.user_id, request.session_id, messages)
    except RequestConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return {
        "success": True,
        "request_id": request.request_id,
        "user_id": request.user_id,
        "session_id": request.session_id
    }


@app.post("/search", dependencies=[Depends(verify_api_key)])
def search_endpoint(request: SearchRequest):
    results = search_memories(
        user_id=request.user_id,
        query=request.query,
        top_k=request.top_k
    )

    return {"data": results}
