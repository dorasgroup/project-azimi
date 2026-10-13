from pydantic import BaseModel, EmailStr

class LoginRequestDTO(BaseModel):
    email: EmailStr
    password: str

class TokenResponseDTO(BaseModel):
    access_token: str
    token_type: str = "bearer"
