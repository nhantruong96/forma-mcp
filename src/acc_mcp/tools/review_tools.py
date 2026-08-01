"""ACC Reviews (approval workflow) tools — the Shared→Published gate."""

from __future__ import annotations

from typing import Annotated, Any, Optional

from pydantic import Field

from ..app import mcp
from ..http import ok, request, tool_error
from ..ids import without_b
from .. import config


def _fmt_review(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r.get("id"),
        "sequenceId": r.get("sequenceId"),
        "name": r.get("name"),
        "status": r.get("status"),
        "workflowId": r.get("workflowId"),
        "currentStepDueDate": r.get("currentStepDueDate"),
        "createdBy": r.get("createdBy"),
        "createdAt": r.get("createdAt"),
        "finishedAt": r.get("finishedAt"),
    }


@mcp.tool(
    name="acc_list_review_workflows",
    annotations={"title": "ACC: List approval workflows", "readOnlyHint": True},
)
async def acc_list_review_workflows(
    project_id: Annotated[str, Field(description="Project id")],
    active_only: Annotated[bool, Field(description="Only ACTIVE workflows")] = True,
) -> str:
    """List the document approval workflows configured in ACC (steps,
    reviewers/approvers, approval status labels, copy-files destination).
    These are the controlled WIP→Shared→Published transitions of the ISO
    19650 CDE. Workflows are created in the ACC UI (or acc_create tools may
    not cover every option) — this shows what exists and the workflowId
    needed to start a review."""
    try:
        params: dict[str, Any] = {"limit": 50}
        if active_only:
            params["filter[status]"] = "ACTIVE"
        payload = await request(
            "GET",
            f"{config.REVIEWS_V1}/projects/{without_b(project_id)}/workflows",
            params=params,
            impersonate_header="x-user-id",
        )
        workflows = []
        for w in payload.get("results", []):
            workflows.append(
                {
                    "id": w.get("id"),
                    "name": w.get("name"),
                    "status": w.get("status"),
                    "steps": [
                        {
                            "id": s.get("id"),
                            "name": s.get("name"),
                            "type": s.get("type"),
                            "durationDays": s.get("duration"),
                        }
                        for s in (w.get("steps") or [])
                    ],
                    "approvalStatusOptions": [
                        {"id": o.get("id"), "label": o.get("label"), "value": o.get("value")}
                        for o in (w.get("approvalStatusOptions") or [])
                    ],
                    "copyFilesOptions": w.get("copyFilesOptions"),
                }
            )
        return ok(workflows, count=len(workflows))
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_list_reviews",
    annotations={"title": "ACC: List reviews", "readOnlyHint": True},
)
async def acc_list_reviews(
    project_id: Annotated[str, Field(description="Project id")],
    status: Annotated[Optional[str], Field(description="Filter: OPEN | CLOSED | VOID | FAILED")] = None,
    workflow_id: Annotated[Optional[str], Field(description="Filter by workflow")] = None,
    limit: Annotated[int, Field(ge=1, le=50)] = 25,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> str:
    """List document reviews (approval processes) in a project — e.g. all OPEN
    reviews awaiting action, for the daily CDE follow-up."""
    try:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["filter[status]"] = status.upper()
        if workflow_id:
            params["filter[workflowId]"] = workflow_id
        payload = await request(
            "GET",
            f"{config.REVIEWS_V1}/projects/{without_b(project_id)}/reviews",
            params=params,
            impersonate_header="x-user-id",
        )
        reviews = [_fmt_review(r) for r in payload.get("results", [])]
        pag = payload.get("pagination", {})
        return ok(reviews, totalResults=pag.get("totalResults"), offset=offset)
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_get_review",
    annotations={"title": "ACC: Get review detail", "readOnlyHint": True},
)
async def acc_get_review(
    project_id: Annotated[str, Field(description="Project id")],
    review_id: Annotated[str, Field(description="Review id (from acc_list_reviews)")],
    include_progress: Annotated[bool, Field(description="Include per-step progress/actions")] = True,
    include_files: Annotated[bool, Field(description="Include the file versions under review")] = False,
) -> str:
    """Get one review's detail: status, current step and due date, per-step
    progress (who acted, decisions) and optionally the files in the review.
    Note: step decisions (approve/reject) can only be made in the ACC UI, not
    via API — use this to monitor and chase."""
    try:
        pid = without_b(project_id)
        review = await request(
            "GET",
            f"{config.REVIEWS_V1}/projects/{pid}/reviews/{review_id}",
            impersonate_header="x-user-id",
        )
        out: dict[str, Any] = {"review": review}
        if include_progress:
            out["progress"] = await request(
                "GET",
                f"{config.REVIEWS_V1}/projects/{pid}/reviews/{review_id}/progress",
                impersonate_header="x-user-id",
            )
        if include_files:
            out["versions"] = await request(
                "GET",
                f"{config.REVIEWS_V1}/projects/{pid}/reviews/{review_id}/versions",
                impersonate_header="x-user-id",
            )
        return ok(out)
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_create_review",
    annotations={"title": "ACC: Start review", "readOnlyHint": False},
)
async def acc_create_review(
    project_id: Annotated[str, Field(description="Project id")],
    workflow_id: Annotated[str, Field(description="Approval workflow id (acc_list_review_workflows)")],
    name: Annotated[str, Field(description="Review name, e.g. 'M3 - Phat hanh ho so thiet ke dot 1'")],
    version_urns: Annotated[str, Field(description="Comma/newline-separated version URNs of the documents to submit (tip versions)")],
    notes: Annotated[Optional[str], Field(description="Notes for the reviewers")] = None,
) -> str:
    """Start (initiate) a document review on an existing approval workflow —
    submitting containers for the checking/approval transition (e.g. S3 review
    or S4 stage approval). The subsequent approve/reject decisions happen in
    the ACC UI; monitor with acc_get_review."""
    try:
        urns = [u.strip() for u in version_urns.replace("\n", ",").split(",") if u.strip()]
        if not urns:
            return "Error: provide at least one version URN."
        body: dict[str, Any] = {
            "name": name,
            "workflowId": workflow_id,
            "fileVersions": [{"urn": u} for u in urns],
        }
        if notes:
            body["notes"] = notes
        payload = await request(
            "POST",
            f"{config.REVIEWS_V1}/projects/{without_b(project_id)}/reviews",
            json_body=body,
            impersonate_header="x-user-id",
        )
        return ok(_fmt_review(payload), note="Review started — decisions are made in the ACC UI.")
    except Exception as exc:
        return tool_error(exc)
