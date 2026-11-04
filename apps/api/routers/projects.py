from fastapi import APIRouter, HTTPException, status

from models.project import ProjectCreate, ProjectMeta
from services import audit, storage

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectMeta])
def list_projects():
    return storage.list_projects()


@router.post("", response_model=ProjectMeta, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate):
    meta = storage.create_project(payload.name, payload.description)
    # First step of the workflow — a "project created" entry goes at the very top of the log.
    audit.write(
        meta.id,
        kind="project_create",
        title=f"Proje oluşturuldu: {meta.name}",
        title_key="audit.titles.projectCreated",
        title_params={"name": meta.name},
        details={"name": meta.name, "description": payload.description or ""},
        user_action="create_project",
    )
    return meta


@router.get("/{project_id}", response_model=ProjectMeta)
def get_project(project_id: str):
    meta = storage.get_project(project_id)
    if meta is None:
        raise HTTPException(404, "project_not_found")
    return meta


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    try:
        storage.delete_project(project_id)
    except Exception as e:
        # If not caught, the 500 comes back from ServerErrorMiddleware without a CORS
        # header (a misleading "CORS" error in the browser). An HTTPException passes
        # through the inner layer, gets the CORS header and shows the user the real cause.
        raise HTTPException(409, f"project_delete_failed: {e}")
    return None
