from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from pipeline.query.intent import classify_intent
from pipeline.query.executor import execute
from pipeline.query.formatter import format_result, format_unknown

router = APIRouter(tags=["websocket"])


@router.websocket("/api/{agency_id}/chat")
async def chat(websocket: WebSocket, agency_id: int):
    await websocket.accept()
    pool = websocket.app.state.pool
    try:
        while True:
            question = await websocket.receive_text()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT agency_id FROM agencies WHERE agency_id=$1", agency_id
                )
                if not row:
                    await websocket.send_json({"error": f"Agency {agency_id} not found"})
                    await websocket.close(code=4004)
                    return
                intent = await classify_intent(question)
                if intent.get("unknown"):
                    answer = await format_unknown(question, conn, agency_id)
                else:
                    rows = await execute(intent, conn, agency_id)
                    answer = format_result(intent["query_type"], rows or [], intent)
            await websocket.send_json({"answer": answer, "intent": intent})
    except WebSocketDisconnect:
        pass
