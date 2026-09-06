from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.connection_manager import manager
from app.database import get_db
from app.models import User
from app.schemas import UserCreate, UserResponse, UserSearchResult

router = APIRouter(prefix="/users", tags=["Users"])


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
def create_user(user_in: UserCreate, db: Session = Depends(get_db)):
    """
    Register a new user with a display name and unique username.
    Returns HTTP 409 Conflict if the username is already registered.
    """
    clean_username = user_in.username.strip().lower()
    clean_name = user_in.name.strip()

    existing_user = db.query(User).filter(User.username == clean_username).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{clean_username}' already exists",
        )

    new_user = User(name=clean_name, username=clean_username)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    response_data = UserResponse.model_validate(new_user)
    response_data.is_online = manager.is_online(new_user.id)
    return response_data


@router.get("/search", response_model=List[UserSearchResult])
def search_users(
    q: str = Query(..., min_length=1, description="Search term for name or username"),
    user_id: Optional[str] = Query(None, description="Optional requesting user ID to exclude from results"),
    db: Session = Depends(get_db),
):
    """
    Search for users with case-insensitive partial match on name or username.
    Excludes the requesting user if user_id is provided.
    """
    term = f"%{q.strip().lower()}%"
    query = db.query(User).filter(
        (User.name.ilike(term)) | (User.username.ilike(term))
    )

    if user_id:
        query = query.filter(User.id != user_id.strip())

    users = query.all()

    results: List[UserSearchResult] = []
    for u in users:
        item = UserSearchResult(
            id=u.id,
            name=u.name,
            username=u.username,
            is_online=manager.is_online(u.id),
        )
        results.append(item)

    return results


@router.get("/{id}", response_model=UserResponse)
def get_user(id: str, db: Session = Depends(get_db)):
    """
    Return full user details by ID, or HTTP 404 if not found.
    """
    user = db.query(User).filter(User.id == id.strip()).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID '{id}' not found",
        )

    response_data = UserResponse.model_validate(user)
    response_data.is_online = manager.is_online(user.id)
    return response_data
