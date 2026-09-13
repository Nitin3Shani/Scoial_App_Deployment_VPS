from typing import Annotated

from fastapi import Depends, HTTPException, status, APIRouter, UploadFile, Query, BackgroundTasks
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import delete as sql_delete

from starlette.concurrency import run_in_threadpool

from PIL import UnidentifiedImageError

from image_utils import process_profile_image, delete_profile_image, upload_profile_image   
import models
from database import get_db
from schemas import PostResponse, UserCreate, UserPublic, UserPrivate, UserUpdate, Token, PaginatedPostResponse, ChangePasswordRequest, ResetPasswordRequest, ForgetPasswordRequest

from config import settings
from datetime import timedelta, UTC , datetime
from fastapi.security import OAuth2PasswordRequestForm

from auth import create_access_token, hash_password, verify_password, CurrentUser, generate_reset_token, hash_reset_token

from email_utils import send_password_reset_email

from botocore.exceptions import ClientError

router = APIRouter()

@router.get('', response_model=list[UserPublic])
async def get_all_users(db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(select(models.User).order_by(models.User.id.asc()))
    users = result.scalars().all()
    return users


@router.post('', response_model=UserPrivate, status_code=status.HTTP_201_CREATED)
async def create_user(user: UserCreate, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(
        select(models.User).where(func.lower(models.User.username) == user.username.lower())
    )
    existing_user = result.scalars().first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Username already exists',
        )

    result = await db.execute(
        select(models.User).where(func.lower(models.User.email) == user.email.lower())
    )
    existing_email = result.scalars().first()
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Email already exists',
        )

    new_user = models.User(username=user.username, email=user.email.lower(), password_hash = hash_password(user.password))
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user

@router.post('/token', response_model= Token)
async def login_for_access_token(form_data: Annotated[OAuth2PasswordRequestForm,Depends()], db: Annotated[AsyncSession, Depends(get_db)]):

    result = await db.execute(
        select(models.User).where(func.lower(models.User.email) == form_data.username.lower())
    )
    user = result.scalars().first()

    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code= status.HTTP_401_UNAUTHORIZED,
            detail= 'Incorrect email or password',
            headers = {'WWW-Authenticate': 'Bearer'},
        )

    access_token_expires = timedelta(minutes= settings.access_token_expire_minutes)
    access_token = create_access_token(data = {'sub' : str(user.id)},
                                       expires_delta= access_token_expires,
                                       )
    return Token(access_token= access_token, token_type= 'bearer')


@router.get('/me', response_model = UserPrivate)
async def get_current_user(current_user: CurrentUser):
    return current_user

@router.post('/forgot-password', status_code= status.HTTP_202_ACCEPTED)
async def forgot_password(request_data: ForgetPasswordRequest, background_taks: BackgroundTasks, db: Annotated[AsyncSession, Depends(get_db)]):

    result = await db.execute(
        select(models.User).where(func.lower(models.User.email) == request_data.email.lower())
    )
    user = result.scalars().first()

    if user:
        await db.execute(
            sql_delete(models.PasswordRsestToken).where(user.id == models.PasswordRsestToken.user_id)
        )

        token = generate_reset_token()
        hash_token = hash_reset_token(token)
        expires_at = datetime.now() + timedelta(
            minutes = settings.reset_token_expire_minutes
        )

        reset_token = models.PasswordRsestToken(
            user_id = user.id,
            token_hash = hash_token,
            expires_at = expires_at
        )

        db.add(reset_token)
        await db.commit()

        background_taks.add_task(
            send_password_reset_email,
            to_email= user.email,
            username= user.username,
            token = token
        )

    return {
        'message' : 'If an account exists with this email, you will recieve password reset instructions.'
    }

@router.post('/reset-password', status_code= status.HTTP_200_OK)
async def reset_password(
    request_data: ResetPasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    token_hash = hash_reset_token(request_data.token)

    result = await db.execute(
        select(models.PasswordRsestToken).where(
            models.PasswordRsestToken.token_hash == token_hash
        ),
    )
    reset_token = result.scalars().first()

    if not reset_token:
        raise HTTPException(
            status_code = status.HTTP_400_BAD_REQUEST,
            detail = "Invalid or expired reset token"
        )

    if reset_token.expires_at < datetime.now(UTC):
        await db.delete(reset_token)
        await db.commit()
        raise HTTPException(
            status_code= status.HTTP_400_BAD_REQUEST,
            detail = 'Invalid or Expired reset token'
        )

    result = await db.execute(
        select(models.User).where(models.User.id == reset_token.user_id)
    )
    user = result.scalars().first()

    if not user:
        raise HTTPException(
            status_code= status.HTTP_400_BAD_REQUEST,
            detail = 'Invalid or Expired reset token'
        )

    user.password_hash = hash_password(request_data.new_password)
    await db.execute(
        sql_delete(models.PasswordRsestToken).where(
            models.PasswordRsestToken.user_id == user.id
     )
    )

    await db.commit()
    return {
        'message' : 'Password reset successfully. You can now log in with your new password.'
    }

@router.patch('/me/password', status_code= status.HTTP_200_OK)
async def change_password(
        password_data: ChangePasswordRequest,
        current_user: CurrentUser,
        db: Annotated[AsyncSession, Depends(get_db)]
):
    if not verify_password(password_data.current_password, current_user.password_hash):
        raise HTTPException(
            status_code= status.HTTP_400_BAD_REQUEST,
            detail = 'Current password is incorrect'
        )

    current_user.password_hash = hash_password(password_data.new_password)

    await db.execute(
        sql_delete(models.PasswordRsestToken).where(
            models.PasswordRsestToken.user_id == current_user.id
        )
    )

    await db.commit()
    return {'message': 'Passowrd changed successfully'}


@router.get('/{user_id}', response_model=UserPublic)
async def get_user(user_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    user = await db.get(models.User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
    return user

@router.get('/{user_id}/posts', response_model= PaginatedPostResponse
)
async def get_user_posts(user_id: int, db: Annotated[AsyncSession, Depends(get_db)], skip: Annotated[int, Query(ge = 0)] = 0, limit: Annotated[int, Query(ge= 1, le = 100)] = 10):
    user = await db.get(models.User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')

    count_result = await db.execute(
    select(func.count())
    .select_from(models.Post)
    .where(models.Post.user_id == user_id)
)


    total = count_result.scalar() or 0

    result = await db.execute(
        select(models.Post).options(selectinload(models.Post.author))
        .where(models.Post.user_id == user_id)
        .order_by(models.Post.date_posted.desc())
        .offset(skip)
        .limit(limit)
    )
    posts = result.scalars().all()
     
    has_more = skip + len(posts) < total

    return PaginatedPostResponse(
        posts= [PostResponse.model_validate(post) for post in posts],
        total = total,
        skip = skip,
        limit = limit,
        has_more = has_more,
    )

@router.patch('/{user_id}', response_model= UserPrivate)
async def update_user_account(user_id: int, update_user: UserUpdate, current_user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    if user_id != current_user.id:
       raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Not Authorized to update this user')

    user = await db.get(models.User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')

    if update_user.username is not None and update_user.username.lower() != user.username.lower() :
        result = await db.execute(
            select(models.User).where(func.lower(models.User.username) == update_user.username.lower())
        )
        existing_user = result.scalars().first()
        if existing_user:
            raise HTTPException(
                status_code= status.HTTP_400_BAD_REQUEST,
                detail = 'Username already exists'
            )

    if update_user.email is not None and update_user.email.lower() != user.email.lower():
        result = await db.execute(
            select(models.User).where(func.lower(models.User.email) == update_user.email.lower())
        )
        existing_email = result.scalars().first()
        if existing_email:
            raise HTTPException(
                status_code = status.HTTP_400_BAD_REQUEST,
                detail= 'email already exists'
            )

    if update_user.username is not None:
        user.username = update_user.username
    if update_user.email is not None:
            user.email = update_user.email.lower()
    
    await db.commit()
    await db.refresh(user)
    return user

@router.delete('/{user_id}', status_code= status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, current_user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    if user_id != current_user.id:
       raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Not Authorized to delete this user')

    user = await db.get(models.User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')

    old_filename = user.image_file

    await db.delete(user)
    await db.commit()

    await delete_profile_image(old_filename)


@router.patch('/{user_id}/picture', response_model= UserPrivate)  
async def upload_profile_pic(user_id: int, file: UploadFile, current_user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):

    if current_user.id != user_id:
        raise HTTPException(
            status_code= status.HTTP_403_FORBIDDEN,
            detail= "Not Authorized to update this user's picture",
        )

    content = await file.read()

    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code= status.HTTP_400_BAD_REQUEST,
            detail = f'File too large. Maximum size is {settings.max_upload_size_bytes // 1024 * 1024}MB'
        )

    
    try:
        processed_bytes, new_filename = await run_in_threadpool(process_profile_image, content)
    except UnidentifiedImageError as err:
        raise HTTPException(
            status_code= status.HTTP_400_BAD_REQUEST,
            detail = 'Invalid image file. please upload a valid image(JPEG, PNG, GIF, WebP).'
        )from err

    try:
        await upload_profile_image(processed_bytes, new_filename)
    except ClientError as err:
        raise HTTPException(
            status_code= status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail = 'Failed to upload image. Please try again.'
        )from err

    old_filename = current_user.image_file

    current_user.image_file = new_filename
    await db.commit()
    await db.refresh(current_user)

    if old_filename:
        await delete_profile_image(old_filename)

    return current_user


@router.delete('/{user_id}/picture', response_model= UserPrivate)
async def delete_profile_pic(user_id: int, current_user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):

    if current_user.id != user_id:
        raise HTTPException(
            status_code= status.HTTP_403_FORBIDDEN,
            detail= "Not authorized to delete this user's pic"
        )

    old_filename = current_user.image_file

    if old_filename is None:
        raise HTTPException(
            status_code= status.HTTP_400_BAD_REQUEST,
            detail = 'No Profile picture to delete'
        )

    current_user.image_file = None

    await db.commit()
    await db.refresh(current_user)

    await delete_profile_image(old_filename)

    return current_user

