from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class Author(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    username: str
    image_path: str

class UserBase(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    email: EmailStr = Field(max_length=120)

class UserCreate(UserBase):
    password:str = Field(min_length= 5)

class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str = Field(min_length=1, max_length=50)
    image_file: str | None = None
    image_path: str

class UserPrivate(UserPublic):
    email: EmailStr

class UserUpdate(BaseModel):
    username: str | None = Field(default = None, min_length=1, max_length=50)
    email: EmailStr | None = Field(default = None, max_length=120)
    

class Token(BaseModel):
    access_token: str
    token_type: str

class PostBase(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1)

class PostCreate(PostBase):
    pass

class PostResponse(PostBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    author: Author
    date_posted: datetime

class PaginatedPostResponse(BaseModel):
    posts: list[PostResponse]
    total: int
    skip: int
    limit: int
    has_more: bool

class PostUpdate(BaseModel):
    title: str | None = Field(default= None, min_length= 1, max_length= 100)
    content : str | None = Field(default = None, min_length = 1)


class ForgetPasswordRequest(BaseModel):
    email: EmailStr = Field(max_length = 120)

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length= 5)

class ChangePasswordRequest(BaseModel):
    current_password: str 
    new_password: str = Field(min_length = 5)