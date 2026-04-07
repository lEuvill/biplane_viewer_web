"""
consumers.py
WebSocket consumer for job progress updates.
On connect, immediately sends the current task status so the browser
doesn't get stuck if Celery already finished before WS connected.
"""

import json
from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async


class ProgressConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.job_id = self.scope["url_route"]["kwargs"]["job_id"]
        self.group  = f"progress_{self.job_id}"
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

        # Catch-up: if the task already finished before we connected, send
        # the current status immediately so the browser doesn't hang forever.
        await self._send_catchup()

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.group, self.channel_name)

    async def progress_update(self, event):
        await self.send(text_data=json.dumps(event["message"]))

    async def _send_catchup(self):
        try:
            from .cache import get_study_id_for_job, get_status

            study_id = await sync_to_async(get_study_id_for_job)(self.job_id)
            if not study_id:
                await self.send(text_data=json.dumps({
                    "phase": "init", "msg": "Waiting for task to start…"
                }))
                return

            status = await sync_to_async(get_status)(study_id)

            if status["status"] == "ready":
                meta = status.get("meta") or {}
                await self.send(text_data=json.dumps({
                    "phase":       "complete",
                    "n_frames":    meta.get("n_frames", 0),
                    "cursor_frac": meta.get("cursor_frac", 0.5),
                }))
            elif status["status"] == "error":
                await self.send(text_data=json.dumps({
                    "phase": "error",
                    "msg":   "Task failed — check server logs.",
                }))
            else:
                await self.send(text_data=json.dumps({
                    "phase": "init",
                    "msg":   "Processing… (decode in progress)",
                }))
        except Exception as e:
            import traceback
            traceback.print_exc()
            # Don't crash the connection — just send a safe fallback
            await self.send(text_data=json.dumps({
                "phase": "init", "msg": f"Processing… (catchup error: {e})"
            }))
