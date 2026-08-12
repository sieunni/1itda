import zipfile


ALLOWED_RESUME_MIMETYPES = {
    "pdf": {"application/pdf", "application/octet-stream"},
    "doc": {"application/msword", "application/octet-stream"},
    "docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "application/octet-stream",
    },
}

OLE_COMPOUND_FILE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
MAX_DOCX_ENTRIES = 10_000
MAX_DOCX_UNCOMPRESSED_SIZE = 20 * 1024 * 1024


def _is_docx(stream):
    try:
        with zipfile.ZipFile(stream) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_DOCX_ENTRIES:
                return False
            if sum(entry.file_size for entry in entries) > MAX_DOCX_UNCOMPRESSED_SIZE:
                return False
            names = {entry.filename for entry in entries}
            return "[Content_Types].xml" in names and any(
                name.startswith("word/") for name in names
            )
    except (OSError, zipfile.BadZipFile):
        return False
    finally:
        stream.seek(0)


def is_valid_resume_file(uploaded_file, extension):
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
