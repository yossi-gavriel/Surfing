"""Analysis result endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from shared.utils.logger import get_logger
from src.security import get_current_user

logger = get_logger("api-analysis")

router = APIRouter()


@router.get("/{track_id}")
def get_analysis(
    track_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Get analysis result for a specific track."""
    store = request.app.state.pipeline_store
    if store is None:
        raise HTTPException(status_code=503, detail="Pipeline store unavailable")

    job = store.get_analysis_job(track_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis not found")

    # Users can only see their own analyses (admins see all)
    if current_user.get("role") != "admin" and job.get("user_id") != current_user.get("user_id"):
        raise HTTPException(status_code=404, detail="Analysis not found")

    return _public_view(job, request)


@router.get("")
def list_analyses(
    request: Request,
    current_user: dict = Depends(get_current_user),
    status: str | None = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=200),
) -> list[dict]:
    """List analysis results for the current user."""
    store = request.app.state.pipeline_store
    if store is None:
        raise HTTPException(status_code=503, detail="Pipeline store unavailable")

    jobs = store.list_analysis_jobs_for_user(
        current_user["user_id"],
        status=status,
        limit=limit,
    )
    return [_public_view(j, request) for j in jobs]


def _public_view(job: dict, request: Request) -> dict:
    """Strip internal fields and add presigned URLs if available."""
    media = getattr(request.app.state, "media_service", None)

    result = {
        "track_id": job["track_id"],
        "video_id": job["video_id"],
        "status": job["status"],
        "ride_duration_seconds": job.get("ride_duration_seconds"),
        "dominant_direction": job.get("dominant_direction"),
        "ride_score": job.get("ride_score"),
        "maneuver_count": job.get("maneuver_count"),
        "model_version": job.get("model_version"),
        "created_at": job.get("created_at"),
        "completed_at": job.get("completed_at"),
    }

    # Add presigned URL for canonical results if available
    canonical_s3 = job.get("canonical_s3")
    if canonical_s3 and media:
        try:
            result["canonical_url"] = media.get_presigned_url(canonical_s3)
        except Exception:
            result["canonical_url"] = None
    else:
        result["canonical_url"] = None

    if job.get("status") == "failed":
        result["failure_code"] = job.get("failure_code")

    return result
