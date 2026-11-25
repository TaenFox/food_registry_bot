from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.bot.access import can_access_bot
from food_registry_bot.db.models import DataExchangeDirection, DataExchangeFile, DataExchangeStatus
from food_registry_bot.db.repositories import DataExchangeFileRepository, UserRepository
from food_registry_bot.exchange.service import (
    DataExchangeService,
    DuplicateDataRowError,
    DuplicateFileError,
    FileLimitExceededError,
)

MAX_UPLOAD_BYTES = 1024 * 1024


class FileCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["export", "import", "download", "delete"]
    file_id: Optional[int] = Field(default=None, gt=0, le=2_147_483_647, strict=True)

    @model_validator(mode="after")
    def validate_target(self) -> FileCommand:
        if (self.file_id is None) != (self.action == "export"):
            raise ValueError("Only export creation has no file_id")
        return self


@dataclass(frozen=True)
class CsvUpload:
    filename: str
    content: bytes


@dataclass(frozen=True)
class CsvDownload:
    filename: str
    content: bytes


def file_data(file: DataExchangeFile) -> dict:
    return {
        "id": file.id,
        "filename": file.original_filename,
        "direction": file.direction.value,
        "status": file.status.value,
        "type": file.contract_type,
        "rows": file.row_count,
        "food_count": file.food_entry_count,
        "water_count": file.water_entry_count,
        "date_from": file.date_from.isoformat() if file.date_from else None,
        "date_to": file.date_to.isoformat() if file.date_to else None,
        "message": file.processing_message or file.validation_message,
    }


def run_file_exchange(
    session_factory: sessionmaker[Session],
    telegram_user_id: int,
    admin_user_ids: tuple[int, ...],
    command: FileCommand | CsvUpload | None,
) -> tuple[int, dict] | CsvDownload:
    try:
        with session_factory.begin() as session:
            if not can_access_bot(session, telegram_user_id, admin_user_ids):
                return 403, {"error": "access_denied"}
            user = UserRepository(session).get_by_telegram_user_id(
                telegram_user_id, for_update=command is not None,
            )
            if user is None:
                return 404, {"error": "profile_missing"}
            service = DataExchangeService(session)
            repository = DataExchangeFileRepository(session)
            message = None
            if isinstance(command, CsvUpload):
                with TemporaryDirectory(prefix="food-registry-csv-") as directory:
                    path = Path(directory) / "upload.csv"
                    path.write_bytes(command.content)
                    digest, validation = service.validate_import_file(
                        user=user, source_path=path, original_filename=command.filename,
                    )
                    file = service.create_import_file(
                        user=user, source_path=path, original_filename=command.filename,
                        sha256=digest, validation_result=validation,
                    )
                message = file.validation_message
            elif isinstance(command, FileCommand):
                if command.action == "export":
                    service.create_export_files(user=user)
                    message = "Экспорт подготовлен."
                else:
                    assert command.file_id is not None
                    file = repository.get_by_id_for_user(file_id=command.file_id, user_id=user.id)
                    if file is None:
                        return 404, {"error": "file_missing", "message": "Файл недоступен."}
                    if command.action == "delete":
                        service.delete_file(exchange_file=file)
                        message = "Файл удалён."
                    elif command.action == "download":
                        if file.direction is not DataExchangeDirection.EXPORT:
                            return 400, {"error": "invalid_file_action"}
                        csv = service.get_download_path(exchange_file=file).read_bytes()
                        if file.status is not DataExchangeStatus.PROCESSED:
                            service.mark_export_downloaded(
                                exchange_file=file, processing_message="Файл скачан из Mini App.",
                            )
                        return CsvDownload(file.original_filename, csv)
                    else:
                        if file.direction is not DataExchangeDirection.IMPORT:
                            return 400, {"error": "invalid_file_action"}
                        try:
                            service.import_file(exchange_file=file, user=user)
                        except DuplicateDataRowError as exc:
                            repository.mark_error(file_id=file.id, processing_message=str(exc))
                            return 409, {
                                "error": "import_conflict", "message": str(exc),
                                "files": [file_data(item) for item in service.list_files(user_id=user.id)],
                            }
                        message = file.processing_message
            return 200, {
                "files": [file_data(item) for item in service.list_files(user_id=user.id)],
                "message": message,
            }
    except (DuplicateFileError, DuplicateDataRowError, FileLimitExceededError) as exc:
        return 409, {"error": "file_conflict", "message": str(exc)}
    except UnicodeError:
        return 422, {"error": "invalid_file", "message": "Сохраните CSV в кодировке UTF-8."}
    except ValueError as exc:
        return 422, {"error": "invalid_file", "message": str(exc)}
    except FileNotFoundError:
        return 404, {"error": "file_missing", "message": "Файл недоступен."}
