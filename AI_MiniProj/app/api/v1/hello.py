from fastapi import APIRouter

router = APIRouter()

@router.get("/api/hello")
def hello():
    return {"message": "Hello, World!"} 