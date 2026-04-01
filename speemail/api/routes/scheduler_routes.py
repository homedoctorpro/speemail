from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from speemail import scheduler as sched

router = APIRouter(prefix="/api/v1/scheduler", tags=["scheduler"])


@router.post("/trigger", response_class=HTMLResponse)
def trigger(request: Request):
    sched.trigger_now()
    t = request.app.state.templates
    return HTMLResponse(
        t.get_template("partials/_toast.html").render(
            {"request": request, "message": "Checking email now…", "type": "info"}
        )
    )


@router.get("/status")
def status():
    return sched.get_status()
