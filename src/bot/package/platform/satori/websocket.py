import asyncio
import json
import logging
import random

import websockets

from bot.package.config import BotConfig
from bot.package.platform.satori.events import EventBody, LoginList, Signal

logger = logging.getLogger(__name__)


class SatoriClient:

    def __init__(self, config: BotConfig | None = None) -> None:
        self.config = config or BotConfig()
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._handlers: dict[str, list] = {}
        self._running = False

    def on(self, event_type: str):
        def decorator(func):
            self._handlers.setdefault(event_type, []).append(func)
            return func
        return decorator

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    async def connect(self):
        url = self.config.ws_url
        logger.info("Connecting to %s …", url)
        self._ws = await websockets.connect(url)
        logger.info("Connected")

        identify = {"op": 3}
        if self.config.token:
            identify["token"] = self.config.token
        await self._ws.send(json.dumps(identify))
        logger.info("IDENTIFY sent")

    async def disconnect(self):
        self._running = False
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
            self._ws = None

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
            except Exception:
                logger.exception("Unexpected error")

            if not self._running or not self.config.reconnect:
                break

            attempt += 1
            delay = self._reconnect_delay(attempt)
            logger.info("Reconnecting in %.1f seconds …", delay)
            await asyncio.sleep(delay)

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

        handlers = self._handlers.get(event.type, [])
        handlers += self._handlers.get("event", [])

        for handler in handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception("Handler %s failed", handler.__name__)

    async def _dispatch_login(self, body: dict):
        try:
            login_list = LoginList.model_validate(body)
        except Exception as exc:
            logger.warning("LoginList parse failed: %s — body=%.80s", exc, body)
            return

        for handler in self._handlers.get("login", []):
            try:
                await handler(login_list)
            except Exception:
                logger.exception("Login handler %s failed", handler.__name__)

    async def _send_raw(self, data: dict):
        if self._ws is not None and not self._ws.closed:
            await self._ws.send(json.dumps(data))

    def _reconnect_delay(self, attempt: int) -> float:
        delay = min(1.0 * 2 ** attempt, float(self.config.max_reconnect_delay))
        jitter = random.uniform(-0.5, 0.5)
        return max(0.5, delay + jitter)
