"""Pydantic DTOs — the OpenAPI contract falls out of these."""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, EmailStr, Field

# ---- auth ----
class LoginIn(BaseModel):
    email: EmailStr
    password: str

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"

class UserIn(BaseModel):
    email: EmailStr
    name: str = ""
    password: str = Field(min_length=6)
    is_super_admin: bool = False

class UserOut(BaseModel):
    id: str
    email: str
    name: str
    is_super_admin: bool
    is_active: bool
    model_config = {"from_attributes": True}

# ---- products ----
class ChannelConfigIn(BaseModel):
    redis_url: str = ""
    channel_prefix: str = "registry"

class ProductIn(BaseModel):
    key: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$")
    name: str
    description: str = ""

class ProductOut(BaseModel):
    id: str
    key: str
    name: str
    description: str
    seq: int
    is_active: bool
    role: Optional[str] = None          # caller's role on this product
    model_config = {"from_attributes": True}

class MemberIn(BaseModel):
    email: EmailStr
    role: str = Field(pattern="^(admin|user)$")

class MemberOut(BaseModel):
    user_id: str
    email: str
    name: str
    role: str

class AudienceIn(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,49}$")
    display_name: str = ""
    is_default: bool = False

class AudienceOut(BaseModel):
    id: str
    key: str
    display_name: str
    is_default: bool
    model_config = {"from_attributes": True}

class ApiKeyOut(BaseModel):
    id: str
    name: str
    prefix: str
    revoked: bool
    model_config = {"from_attributes": True}

class ApiKeyCreated(ApiKeyOut):
    plaintext: str                       # returned exactly once

# ---- entities ----
class EntityIn(BaseModel):
    """A tool/agent definition. `payload` is the full MCP-facing document."""
    type: str = "tool"
    payload: Dict[str, Any]
    note: str = ""
    model_config = {"json_schema_extra": {"examples": [{
        "type": "tool",
        "note": "initial version",
        "payload": {
            "name": "get_invoice",
            "title": "Get invoice",
            "description": "Fetch a single invoice by its ID, including line items and payment state.",
            "input_schema": {"type": "object",
                             "properties": {"invoice_id": {"type": "string",
                                                           "description": "Unique invoice identifier"}},
                             "required": ["invoice_id"]},
            "annotations": {"readOnlyHint": True},
            "audiences": {"internal": {"overrides": {
                "description": "Internal ledger view of a single invoice."}}}
        }
    }]}}

class EntityPatch(BaseModel):
    payload: Dict[str, Any]
    note: str = ""

class ValidationError(BaseModel):
    path: str
    code: str
    message: str

class EntityOut(BaseModel):
    id: str
    type: str
    name: str
    version: int
    payload: Dict[str, Any]
    resolved: Dict[str, Any]
    model_config = {"from_attributes": True}

class DryRunOut(BaseModel):
    valid: bool
    errors: List[ValidationError] = []
    resolved: Dict[str, Any] = {}

class VersionOut(BaseModel):
    version: int
    note: str
    author_id: str
    created_at: Any
    changes: str = ""            # human summary: what this version touched
    model_config = {"from_attributes": True}

class SimilarOut(BaseModel):
    id: str
    product_key: str
    type: str
    name: str
    score: float
    lexical: float
    method: str = "cosine"          # 'reranked' = cross-encoder verified this %
    cosine: Optional[float] = None  # pre-rerank retrieval score, when reranked
    name_sim: Optional[float] = None  # separate name-collision signal, NOT in score

TokenOut.model_rebuild()
