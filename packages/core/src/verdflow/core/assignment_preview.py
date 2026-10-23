"""Stateless display assignment use case, independent of strategy execution."""

from verdflow.contracts import AssignmentPreviewRequest, AssignmentPreviewResult
from verdflow.planting import resolve_assignment_preview


class AssignmentPreviewService:
    @staticmethod
    def resolve(request: AssignmentPreviewRequest) -> AssignmentPreviewResult:
        return resolve_assignment_preview(request)
