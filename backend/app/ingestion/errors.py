"""Ingestion error taxonomy (doc 02 §3, §6).

Every failure mode a hostile or merely broken document can produce is a
subclass of :class:`IngestionError`, so the orchestrator can decide uniformly
whether a stage is fatal and what to tell the user. Messages are *user-facing*
and must never contain document text (doc 02 §5 — never log document text).
"""

from __future__ import annotations


class IngestionError(Exception):
    """Base class for all ingestion failures."""

    #: shown to the user; must not contain document content
    user_message_pl: str = "Nie udało się przetworzyć dokumentu."
    user_message_en: str = "The document could not be processed."

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail or self.__class__.__name__)
        self.detail = detail


class UnsupportedFormatError(IngestionError):
    user_message_pl = "Nieobsługiwany format pliku."
    user_message_en = "Unsupported file format."


class ParseError(IngestionError):
    """The file is the right format but cannot be parsed into an IR."""

    user_message_pl = "Dokument jest uszkodzony lub nieczytelny."
    user_message_en = "The document is corrupted or unreadable."


class ResourceLimitError(IngestionError):
    """A guardrail from doc 02 §6 fired: size, pages, members, depth, budget."""

    user_message_pl = "Dokument przekracza limity przetwarzania."
    user_message_en = "The document exceeds the processing limits."


class EncryptedDocumentError(ParseError):
    user_message_pl = "Dokument jest zaszyfrowany — usuń hasło i spróbuj ponownie."
    user_message_en = "The document is password protected — remove the password and retry."


class SuspiciousArchiveError(ResourceLimitError):
    """Zip-bomb / path-traversal shape in a DOCX container (doc 02 §3)."""

    user_message_pl = "Archiwum dokumentu wygląda na uszkodzone lub niebezpieczne."
    user_message_en = "The document archive looks corrupted or unsafe."
