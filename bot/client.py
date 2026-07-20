import asyncio
import json
import logging
import random

import httpx
import websockets

from data_object.satori import Endpoint, EventBody, LoginList, Signal
from pydantic import BaseModel

from .config import BotConfig

logger = logging.getLogger(__name__)


class SatoriClient:
    def __init__(self, config: BotConfig | None = None) -> None:
        self.config = config or BotConfig()
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._http: httpx.AsyncClient | None = None
        self._handlers: dict[str, list] = {}
        self._running = False

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------

    def on(self, event_type: str):
        """Register a handler for a specific event type (e.g. ``"message-created"``).

        Can be used as a decorator::

            @client.on("message-created")
            async def handle(e: EventBody):
                ...
        """
        def decorator(func):
            self._handlers.setdefault(event_type, []).append(func)
            return func
        return decorator

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    async def connect(self):
        url = self.config.ws_url
        logger.info("Connecting to %s …", url)
        self._ws = await websockets.connect(url)
        logger.info("Connected")

        await self._ws.send(json.dumps({"op": 3}))
        logger.info("IDENTIFY sent")

    async def disconnect(self):
        self._running = False
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
            self._ws = None
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def run(self):
        self._running = True
        attempt = 0

        while self._running:
            try:
                await self.connect()
                attempt = 0
                await self._receive_loop()
            except websockets.InvalidURI as exc:
                logger.error("Invalid WebSocket URI: %s", exc)
                break
            except OSError as exc:
                logger.warning("Connection failed: %s", exc)
            except Exception as exc:
                logger.exception("Unexpected error: %s", exc)

            if not self._running or not self.config.reconnect:
                break

            attempt += 1
            delay = self._reconnect_delay(attempt)
            logger.info("Reconnecting in %.1f seconds …", delay)
            await asyncio.sleep(delay)

    # ------------------------------------------------------------------
    # Internal: receive / dispatch
    # ------------------------------------------------------------------

    async def _receive_loop(self):
        async for raw in self._ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON received: %.80s", raw)
                continue

            try:
                signal = Signal.model_validate(data)
            except Exception as exc:
                logger.warning("Signal parse failed: %s — data=%.120s", exc, raw)
                continue

            await self._handle_signal(signal)

    async def _handle_signal(self, signal: Signal):
        if signal.op == 0:
            await self._dispatch_event(signal.body or {})
        elif signal.op == 1:
            await self._send_raw({"op": 2})
        elif signal.op == 4:
            await self._dispatch_login(signal.body or {})
        else:
            logger.debug("Unknown op %s", signal.op)

    async def _dispatch_event(self, body: dict):
        try:
            event = EventBody.model_validate(body)
        except Exception as exc:
            logger.warning("EventBody parse failed: %s — body=%.120s", exc, body)
            return

        # 1) fire type‑specific handlers, e.g. "message-created"
        handlers = self._handlers.get(event.type, [])
        # 2) fire the catch‑all "event" handlers
        handlers += self._handlers.get("event", [])

        for handler in handlers:
            try:
                await handler(event)
            except Exception as exc:
                logger.exception("Handler %s failed: %s", handler.__name__, exc)

    async def _dispatch_login(self, body: dict):
        try:
            login_list = LoginList.model_validate(body)
        except Exception as exc:
            logger.warning("LoginList parse failed: %s — body=%.80s", exc, body)
            return

        for handler in self._handlers.get("login", []):
            try:
                await handler(login_list)
            except Exception as exc:
                logger.exception("Login handler %s failed: %s", handler.__name__, exc)

    # ------------------------------------------------------------------
    # HTTP API helper
    # ------------------------------------------------------------------

    async def call_api(self, endpoint: Endpoint, params: BaseModel | None = None, **extra) -> dict:
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=self.config.api_base_url)

        headers = {"Content-Type": "application/json"}
        if self.config.api_platform:
            headers["Satori-Platform"] = self.config.api_platform
        if self.config.api_user_id:
            headers["Satori-User-ID"] = self.config.api_user_id

        payload = {}
        if params is not None:
            payload.update(params.model_dump(exclude_none=True))
        payload.update(extra)

        resp = await self._http.post(endpoint.path, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _send_raw(self, data: dict):
        if self._ws is not None and not self._ws.closed:
            await self._ws.send(json.dumps(data))

    @staticmethod
    def _reconnect_delay(attempt: int) -> float:
        delay = min(1.0 * 2 ** attempt, 30.0)
        jitter = random.uniform(-0.5, 0.5)
        return max(0.5, delay + jitter)
