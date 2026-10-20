"""HTTP adapters for workspace application operations."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api_service.deps import get_context, get_db
from api_service.schemas import WorkspaceCreateRequest, WorkspaceDeleteRequest, WorkspaceSummary, WorkspaceUpdateRequest
from api_service.workspaces import service
from backend_common.auth import AuthContext

router = APIRouter(prefix="/api/app", tags=["workspaces"])


@router.get("/workspaces", response_model=list[WorkspaceSummary])
def list_workspaces(db: Session = Depends(get_db), context: AuthContext = Depends(get_context)) -> list[WorkspaceSummary]:
    return service.list_workspaces(db=db, context=context)


@router.post("/workspaces", response_model=WorkspaceSummary)
def create_workspace(request: WorkspaceCreateRequest, db: Session = Depends(get_db), context: AuthContext = Depends(get_context)) -> WorkspaceSummary:
    return service.create_workspace(request, db=db, context=context)


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceSummary)
def update_workspace(workspace_id: str, request: WorkspaceUpdateRequest, db: Session = Depends(get_db), context: AuthContext = Depends(get_context)) -> WorkspaceSummary:
    return service.update_workspace(workspace_id, request, db=db, context=context)


@router.delete("/workspaces/{workspace_id}", response_model=dict)
def delete_workspace(workspace_id: str, request: WorkspaceDeleteRequest | None = None, db: Session = Depends(get_db), context: AuthContext = Depends(get_context)) -> dict:
    return service.delete_workspace(workspace_id, request, db=db, context=context)
