"""Validación de imágenes por contenido y almacenamiento privado de demostración."""
from hashlib import sha256
from io import BytesIO
from math import isfinite
from pathlib import Path
import warnings
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.utils import secure_filename

from persistence.provider import get_repository

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_DIMENSION = 8192
MAX_UPLOAD_FILES = 5


def storage_root(config):
    root = Path(config.get("ARES_OCT_STORAGE") or get_repository().oct_storage_root()).resolve()
    project = Path(__file__).resolve().parent
    for public in (project / "static", project / "templates"):
        if root == public or root.is_relative_to(public):
            raise ValueError("El almacenamiento OCT debe estar fuera de static y templates")
    return root


def resolve_private(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("Ruta de archivo OCT inválida")
    return path


def cleanup_files(root, attachments):
    for attachment in attachments:
        for field in ("file_path", "preview_path"):
            path = resolve_private(root, attachment[field])
            path.unlink(missing_ok=True)
        folder = resolve_private(root, attachment["file_path"]).parent
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()


def _decode(content):
    if content[128:132] == b"DICM":
        raise ValueError("DICOM requiere un adaptador OCT validado. Exporta una imagen PNG, JPEG o TIFF de una sola página")
    signatures = (content.startswith(b"\x89PNG\r\n\x1a\n"), content.startswith(b"\xff\xd8\xff"),
                  content.startswith((b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")))
    if not any(signatures):
        raise ValueError("Formato no compatible. Se admiten PNG, JPEG y TIFF verificados por contenido")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content), formats=["PNG", "JPEG", "TIFF"]) as image:
                detected = image.format
                width, height = image.size
                mode = image.mode
                if width * height > MAX_PIXELS or max(width, height) > MAX_DIMENSION:
                    raise ValueError("La imagen supera 16 millones de píxeles o 8192 píxeles por lado")
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Exporta cada corte como imagen individual. Las imágenes animadas o multipágina requieren un visor específico")
                image.verify()
            with Image.open(BytesIO(content), formats=["PNG", "JPEG", "TIFF"]) as image:
                image.load()
                preview = ImageOps.exif_transpose(image)
                transform = "Vista previa RGB de 8 bits"
                if mode.startswith("I") or mode == "F":
                    preview = preview.convert("F")
                    low, high = preview.getextrema()
                    if not isfinite(low) or not isfinite(high):
                        raise ValueError("La imagen contiene intensidades no finitas")
                    scale = 255 / (high - low) if high > low else 0
                    preview = preview.point(lambda value: (value - low) * scale).convert("L")
                    transform = "Vista previa de 8 bits con escala lineal de intensidad"
                preview = preview.convert("RGB")
                preview.thumbnail((2048, 2048))
                output = BytesIO()
                preview.save(output, format="PNG")
                metadata = {"width": width, "height": height, "mode": mode, "format": detected,
                            "preview_width": preview.width, "preview_height": preview.height,
                            "preview_transform": transform, "content_verified": True}
                return detected, output.getvalue(), metadata
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning, OverflowError) as error:
        raise ValueError("Archivo no interpretable o imagen dañada. Exporta una imagen compatible y vuelve a cargarla") from error


def prepare_files(uploads, config):
    uploads = [upload for upload in uploads if upload.filename]
    if len(uploads) > MAX_UPLOAD_FILES:
        raise ValueError("Puedes importar hasta 5 archivos por operación")
    root = storage_root(config)
    attachments = []
    try:
        for upload in uploads:
            content = upload.stream.read(MAX_FILE_BYTES + 1)
            if not content or len(content) > MAX_FILE_BYTES:
                raise ValueError("Cada archivo debe contener una imagen y ocupar como máximo 10 MiB")
            detected, preview, metadata = _decode(content)
            checksum = sha256(content).hexdigest()
            if any(item["checksum"] == checksum for item in attachments):
                raise ValueError("La selección incluye un archivo duplicado")
            folder = root / uuid4().hex
            folder.mkdir(parents=True, exist_ok=False)
            suffix = {"PNG": "png", "JPEG": "jpg", "TIFF": "tiff"}[detected]
            attachment = {"file_path": f"{folder.name}/original.{suffix}", "preview_path": f"{folder.name}/preview.png",
                          "file_type": detected, "original_name": secure_filename(upload.filename.replace('\\', '/').split('/')[-1])[:120] or f"imagen.{suffix}",
                          "checksum": checksum, "size_bytes": len(content), "metadata": metadata}
            attachments.append(attachment)
            with resolve_private(root, attachment["file_path"]).open("xb") as target:
                target.write(content)
            with resolve_private(root, attachment["preview_path"]).open("xb") as target:
                target.write(preview)
        return root, attachments
    except Exception:
        cleanup_files(root, attachments)
        raise
