from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from services import audit, converter, storage

router = APIRouter(prefix="/projects/{project_id}/convert", tags=["convert"])


class ConvertCsvRequest(BaseModel):
    files: list[str]
    output: str = "scopus_merged.xlsx"


class ConvertWosRequest(BaseModel):
    files: list[str]
    output: str = "wos_merged.xlsx"


class ConvertXlsxRequest(BaseModel):
    file: str
    output: str | None = None


class ConvertResult(BaseModel):
    name: str
    size: int
    relative_path: str


def _result(path: Path) -> ConvertResult:
    return ConvertResult(
        name=path.name,
        size=path.stat().st_size,
        relative_path=str(path.relative_to(storage.settings.storage_path)),
    )


@router.get("/processed", response_model=list[ConvertResult])
def list_processed(project_id: str):
    return converter.list_processed(project_id)


def _audit_convert(project_id: str, kind: str, inputs: list[str] | str, output: Path) -> None:
    audit.write(
        project_id,
        kind="convert",
        title=f"{kind}: {output.name}",
        title_key="audit.titles.converted",
        title_params={"name": output.name},
        analysis_id=None,  # proje-seviye tool
        details={
            "type": kind,
            "inputs": inputs if isinstance(inputs, list) else [inputs],
            "output": output.name,
            "output_size": output.stat().st_size,
        },
        user_action="convert",
    )


@router.post("/csv-to-xlsx", response_model=ConvertResult)
def csv_to_xlsx(project_id: str, payload: ConvertCsvRequest):
    p = converter.csv_to_xlsx(project_id, payload.files, payload.output)
    _audit_convert(project_id, "Scopus CSV → XLSX", payload.files, p)
    return _result(p)


@router.post("/wos-to-xlsx", response_model=ConvertResult)
def wos_to_xlsx(project_id: str, payload: ConvertWosRequest):
    p = converter.wos_to_xlsx(project_id, payload.files, payload.output)
    _audit_convert(project_id, "WoS TXT → XLSX", payload.files, p)
    return _result(p)


@router.post("/xlsx-to-wos-txt", response_model=ConvertResult)
def xlsx_to_wos_txt(project_id: str, payload: ConvertXlsxRequest):
    p = converter.xlsx_to_wos_txt(project_id, payload.file, payload.output)
    _audit_convert(project_id, "XLSX → WoS TXT", payload.file, p)
    return _result(p)


@router.post("/xlsx-to-tsv", response_model=ConvertResult)
def xlsx_to_tsv(project_id: str, payload: ConvertXlsxRequest):
    p = converter.xlsx_to_tsv(project_id, payload.file, payload.output)
    _audit_convert(project_id, "XLSX → TSV", payload.file, p)
    return _result(p)


@router.get("/download/{filename}")
def download(project_id: str, filename: str):
    meta = storage.get_project(project_id)
    if meta is None:
        raise HTTPException(404, "project_not_found")
    name = Path(filename).name
    target = storage.project_dir(project_id) / "processed" / name
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "file_not_found")
    return FileResponse(target, filename=name)
