from __future__ import annotations

from dataclasses import asdict

from ejs.contracts.tmh import artifact_key, execution_event_key
from ejs.domain.template_history import (
    CanonicalFieldDefinition,
    ExecutionEvent,
    FieldMappingRecord,
    SubmissionArtifact,
    TemplateVersionRecord,
)


class InMemoryTmhRepository:
    def __init__(self) -> None:
        self.canonical_fields: dict[str, CanonicalFieldDefinition] = {}
        self.templates: dict[str, TemplateVersionRecord] = {}
        self.mappings: dict[str, FieldMappingRecord] = {}
        self.execution_events: dict[str, ExecutionEvent] = {}
        self.execution_order: list[str] = []
        self.artifacts: dict[str, SubmissionArtifact] = {}

    @staticmethod
    def _append_immutable(store: dict[str, object], key: str, value: object) -> bool:
        existing = store.get(key)
        if existing is None:
            store[key] = value
            return True
        if asdict(existing) == asdict(value):
            return False
        raise ValueError(f"immutable key collision: {key}")

    def put_canonical_field(self, field: CanonicalFieldDefinition) -> bool:
        existing = self.canonical_fields.get(field.field_key)
        if existing is None:
            self.canonical_fields[field.field_key] = field
            return True
        if existing == field:
            return False
        raise ValueError(f"canonical field cannot be silently overwritten: {field.field_key}")

    def append_template(self, record: TemplateVersionRecord) -> bool:
        return self._append_immutable(self.templates, record.version_key, record)

    def append_mapping(self, record: FieldMappingRecord) -> bool:
        return self._append_immutable(self.mappings, record.version_key, record)

    def append_execution_event(self, event: ExecutionEvent) -> bool:
        key = execution_event_key(event.execution_id, event.sequence, event.event_type)
        appended = self._append_immutable(self.execution_events, key, event)
        if appended:
            self.execution_order.append(key)
        return appended

    def append_artifact(self, artifact: SubmissionArtifact) -> bool:
        identity = artifact.content_hash or artifact.artifact_version
        key = artifact_key(artifact.execution_id, artifact.artifact_type, identity)
        return self._append_immutable(self.artifacts, key, artifact)

    def events_for_execution(self, execution_id: str) -> list[ExecutionEvent]:
        return [
            self.execution_events[k]
            for k in self.execution_order
            if self.execution_events[k].execution_id == execution_id
        ]
