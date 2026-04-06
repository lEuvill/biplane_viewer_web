"""
consumers.py
WebSocket consumer for job progress updates.
"""

import json
from channels.generic.websocket import AsyncWebsocketConsumer


class ProgressConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.job_id = self.scope["url_route"]["kwargs"]["job_id"]
        self.group  = f"progress_{self.job_id}"
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.group, self.channel_name)

    # Called by channel layer when a task pushes a progress event
    async def progress_update(self, event):
        await self.send(text_data=json.dumps(event["message"]))
