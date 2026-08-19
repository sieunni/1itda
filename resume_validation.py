import os
import zipfile

from werkzeug.utils import secure_filename


ALLOWED_RESUME_MIMETYPES = {
    "pdf": {"application/pdf"},
    "doc": {"application/msword"},
    "docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
    },
}

OLE_COMPOUND_FILE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
MAX_DOCX_ENTRIES = 10_000
MAX_DOCX_UNCOMPRESSED_SIZE = 20 * 1024 * 1024
MAX_DOCX_ENTRY_SIZE = 10 * 1024 * 1024
MAX_DOCX_COMPRESSION_RATIO = 100
ALLOWED_RESUME_EXTENSIONS = {"pdf", "doc", "docx"}
LEGACY_PREVIEW_EXTENSIONS = {"html", "htm"}


def resume_filename_details(filename):
    if not filename or "\x00" in filename or "/" in filename or "\\" in filename:
        return None
    if os.path.isabs(filename):
        return None

    safe_name = secure_filename(filename)
    if not safe_name or len(safe_name) > 255:
        return None

    parts = safe_name.rsplit(".", 2)
    declared_extension = parts[-1].lower() if len(parts) >= 2 else ""
    if declared_extension not in ALLOWED_RESUME_EXTENSIONS:
        return None

    if len(parts) == 3:
        storage_extension = parts[-2].lower()
        if declared_extension != "pdf" or storage_extension not in LEGACY_PREVIEW_EXTENSIONS:
            return None
        return safe_name, declared_extension, storage_extension, True

    return safe_name, declared_extension, declared_extension, False


def _is_docx(stream):
    try:
        with zipfile.ZipFile(stream) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_DOCX_ENTRIES:
                return False
            if sum(entry.file_size for entry in entries) > MAX_DOCX_UNCOMPRESSED_SIZE:
                return False
            for entry in entries:
                if entry.flag_bits & 0x1 or entry.file_size > MAX_DOCX_ENTRY_SIZE:
                    return False
                if (
                    entry.file_size > 1 * 1024 * 1024
                    and entry.compress_size > 0
                    and entry.file_size / entry.compress_size > MAX_DOCX_COMPRESSION_RATIO
                ):
                    return False
            names = {entry.filename for entry in entries}
            return "[Content_Types].xml" in names and "word/document.xml" in names
    except (OSError, zipfile.BadZipFile):
        return False
    finally:
        stream.seek(0)


def is_valid_resume_file(uploaded_file, extension, use_legacy_preview=False):
    if use_legacy_preview:
        return extension == "pdf"

    mimetype = (uploaded_file.mimetype or "").lower()
    if mimetype not in ALLOWED_RESUME_MIMETYPES.get(extension, set()):
        return False

    stream = uploaded_file.stream
    header = stream.read(8)
    stream.seek(0)

    if extension == "pdf":
        return header.startswith(b"%PDF-")
    if extension == "doc":
        return header == OLE_COMPOUND_FILE_SIGNATURE
    if extension == "docx":
        return header.startswith(b"PK") and _is_docx(stream)
    return False
